"""Veyra crypto session: password -> Argon2id -> KEK -> master key.

Design (see the security model in the project notes)::

    Veyra password
          |
          v
       Argon2id            (slow KDF: the only low entropy input)
          |
          v
         KEK               (key encryption key)
          |
          v
   unwrap master key        (AES-256-GCM, AAD bound to the key store)
          |
          v
    Crypto Session          (only while unlocked)

* The **application login** and the **crypto authorization** are separate
  concepts.  A future Veyra login screen simply calls
  :meth:`CryptoService.unlock` with the user password - there is exactly one
  password and one unlock mechanism, never a second password system.
* The password is never stored.  The master key is never stored in plaintext:
  it only exists, in plaintext, for the moments a session operation needs it.
* ``is_unlocked`` is **not** a boolean field.  It is computed from the real
  native session ticket (a DPAPI blob created at unlock time) re-validated
  against the key store verifier every time it is queried, so patching a
  Python attribute cannot open the session.

High entropy sub-keys (per ``.aimg`` file) are derived from the master key with
HKDF-SHA256 - a fast KDF - because the input is already full entropy.  Argon2id
stays where it belongs: stretching the low entropy human password into the KEK.
"""

from __future__ import annotations

import hmac
from pathlib import Path
from typing import Optional

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from veyra.security import native_backend
from veyra.security.keystore import DEFAULT_KEYSTORE_PATH, KeyStore

__all__ = [
    "CryptoError",
    "LockedError",
    "WrongPasswordError",
    "CryptoService",
]

#: Argon2id parameters used to turn the password into the KEK.
_ARGON2_TIME_COST = 3
_ARGON2_MEMORY_COST = 64 * 1024  # KiB (64 MiB)
_ARGON2_PARALLELISM = 2
_ARGON2_HASH_LEN = 32  # 256-bit

_SALT_SIZE = 16
_NONCE_SIZE = 12
_MASTER_KEY_SIZE = 32  # AES-256

#: AAD binding the wrapped master key to the key store document.
_KEYSTORE_AAD = b"veyra.keystore.v1"
#: Label for the master key verifier (HMAC).
_VERIFIER_LABEL = b"veyra.masterkey.verifier.v1"


class CryptoError(RuntimeError):
    """Base error for crypto session failures."""


class LockedError(CryptoError):
    """Raised when a crypto operation is attempted while locked."""


class WrongPasswordError(CryptoError):
    """Raised when the supplied password cannot unwrap the master key."""


def _argon2_key(password: str, salt: bytes, params: dict) -> bytes:
    if not isinstance(password, str) or not password:
        raise WrongPasswordError("Password wajib diisi.")
    return hash_secret_raw(
        password.encode("utf-8"),
        salt,
        int(params.get("time_cost", _ARGON2_TIME_COST)),
        int(params.get("memory_cost", _ARGON2_MEMORY_COST)),
        int(params.get("parallelism", _ARGON2_PARALLELISM)),
        int(params.get("hash_len", _ARGON2_HASH_LEN)),
        Type.ID,
    )


def _verifier(master_key: bytes) -> bytes:
    return hmac.new(master_key, _VERIFIER_LABEL, "sha256").digest()


def _wipe(buffer: Optional[bytearray]) -> None:
    """Best effort in-memory wipe of key material."""
    if buffer:
        for index in range(len(buffer)):
            buffer[index] = 0


class CryptoService:
    """Authorization boundary for every ``.aimg`` operation.

    The instance is deliberately state-light: while unlocked it keeps a native
    (DPAPI) session ticket, never the master key in a long lived attribute.
    """

    def __init__(self, keystore_path: Optional[Path] = None) -> None:
        self._store = KeyStore(keystore_path or DEFAULT_KEYSTORE_PATH)
        # Native session material (created on unlock, wiped on lock).
        self._ticket: Optional[bytes] = None
        self._ticket_entropy: Optional[bytes] = None
        # Portable fallback when the native boundary is unavailable.
        self._fallback_key: Optional[bytes] = None

    # -- introspection -------------------------------------------------
    @property
    def boundary(self) -> str:
        """Name of the active security boundary (``windows-dpapi`` / fallback)."""
        return native_backend.platform_name()

    @property
    def keystore_path(self) -> Path:
        return self._store.path

    def is_initialized(self) -> bool:
        """Return ``True`` when a key store exists on disk."""
        return self._store.exists()

    def status(self) -> dict:
        """Lightweight snapshot for the UI (never exposes key material)."""
        return {
            "initialized": self.is_initialized(),
            "unlocked": self.is_unlocked(),
            "boundary": self.boundary,
        }

    # -- lifecycle -----------------------------------------------------
    def initialize(self, password: str) -> None:
        """Create a fresh key store (first run) and unlock with ``password``."""
        if self._store.exists():
            raise CryptoError("Crypto session sudah diinisialisasi.")
        document, master_key = self._build_document(password)
        self._store.save(document)
        try:
            self._establish_session(master_key, document)
        finally:
            _wipe(bytearray(master_key))

    def unlock(self, password: str) -> bool:
        """Derive the KEK, unwrap the master key and open the session.

        Returns ``True`` on success.  A wrong password raises
        :class:`WrongPasswordError` and leaves the session locked (no
        capability is produced).
        """
        if not self._store.exists():
            # Bootstrap: the first unlock defines the crypto password.
            self.initialize(password)
            return True

        document = self._store.load()
        master_key = self._unwrap_master_key(password, document)
        try:
            self._establish_session(master_key, document)
        finally:
            _wipe(bytearray(master_key))
        return True

    def lock(self) -> None:
        """Close the session and wipe every trace of in-memory key material."""
        self._ticket = None
        self._ticket_entropy = None
        if self._fallback_key is not None:
            self._fallback_key = None

    def is_unlocked(self) -> bool:
        """Return ``True`` only when the native boundary confirms a live key.

        This is *derived* state: it materialises and re-validates the master
        key every call.  Setting a Python flag cannot make it return ``True``.
        """
        if not self._store.exists():
            return False
        try:
            document = self._store.load()
        except Exception:  # noqa: BLE001 - corrupt store => locked
            return False

        try:
            master_key = self._materialize_key()
        except (LockedError, native_backend.NativeBoundaryError):
            return False
        if master_key is None:
            return False

        try:
            expected = bytes.fromhex(document["verifier"])
            return hmac.compare_digest(_verifier(master_key), expected)
        except (KeyError, ValueError):
            return False
        finally:
            _wipe(bytearray(master_key))

    def validate(self, password: str) -> bool:
        """Check ``password`` against the store without opening a session."""
        if not isinstance(password, str) or not password:
            return False
        if not self._store.exists():
            return False
        try:
            document = self._store.load()
            master_key = self._unwrap_master_key(password, document)
        except (WrongPasswordError, CryptoError, native_backend.NativeBoundaryError):
            return False
        try:
            expected = bytes.fromhex(document["verifier"])
            return hmac.compare_digest(_verifier(master_key), expected)
        except (KeyError, ValueError):
            return False
        finally:
            _wipe(bytearray(master_key))

    # -- crypto operations ---------------------------------------------
    def derive_subkey(self, salt: bytes, info: bytes, length: int = 32) -> bytes:
        """Derive a per-file sub-key from the master key (HKDF-SHA256).

        Raises :class:`LockedError` when the session is locked, so no
        derivation - and therefore no decryption - is possible without a valid
        unlock.
        """
        master_key = self._require_key()
        try:
            return HKDF(
                algorithm=hashes.SHA256(),
                length=length,
                salt=salt,
                info=info,
            ).derive(master_key)
        finally:
            _wipe(bytearray(master_key))

    def encrypt(self, plaintext: bytes, *, aad: bytes = b"") -> bytes:
        """AES-256-GCM encrypt with a fresh nonce from the native CSPRNG.

        Returns ``nonce || ciphertext``.  Requires an unlocked session.
        """
        master_key = self._require_key()
        try:
            nonce = native_backend.random_bytes(_NONCE_SIZE)
            ciphertext = AESGCM(master_key).encrypt(nonce, plaintext, aad)
            return nonce + ciphertext
        finally:
            _wipe(bytearray(master_key))

    def decrypt(self, blob: bytes, *, aad: bytes = b"") -> bytes:
        """Reverse :meth:`encrypt`; raises :class:`LockedError` when locked."""
        master_key = self._require_key()
        try:
            if len(blob) < _NONCE_SIZE + 16:
                raise CryptoError("Ciphertext terlalu pendek.")
            nonce, ciphertext = blob[:_NONCE_SIZE], blob[_NONCE_SIZE:]
            try:
                return AESGCM(master_key).decrypt(nonce, ciphertext, aad)
            except InvalidTag as exc:
                raise CryptoError("Authentication gagal atau data rusak.") from exc
        finally:
            _wipe(bytearray(master_key))

    # -- internals -----------------------------------------------------
    def _build_document(self, password: str) -> tuple[dict, bytes]:
        salt = native_backend.random_bytes(_SALT_SIZE)
        nonce = native_backend.random_bytes(_NONCE_SIZE)
        master_key = native_backend.random_bytes(_MASTER_KEY_SIZE)
        kek = _argon2_key(password, salt, {})
        try:
            wrapped = AESGCM(kek).encrypt(nonce, master_key, _KEYSTORE_AAD)
        finally:
            _wipe(bytearray(kek))
        document = {
            "format": "veyra-keystore",
            "version": 1,
            "kdf": {
                "algo": "argon2id",
                "time_cost": _ARGON2_TIME_COST,
                "memory_cost": _ARGON2_MEMORY_COST,
                "parallelism": _ARGON2_PARALLELISM,
                "hash_len": _ARGON2_HASH_LEN,
            },
            "salt": salt.hex(),
            "nonce": nonce.hex(),
            "wrapped_master_key": wrapped.hex(),
            "verifier": _verifier(master_key).hex(),
        }
        return document, master_key

    def _unwrap_master_key(self, password: str, document: dict) -> bytes:
        try:
            salt = bytes.fromhex(document["salt"])
        except (KeyError, ValueError) as exc:
            raise CryptoError("Key store rusak (salt).") from exc
        try:
            nonce = bytes.fromhex(document["nonce"])
            wrapped = bytes.fromhex(document["wrapped_master_key"])
        except (KeyError, ValueError) as exc:
            raise CryptoError("Key store rusak (wrapped key).") from exc

        params = document.get("kdf") or {}
        kek = _argon2_key(password, salt, params)
        try:
            master_key = AESGCM(kek).decrypt(nonce, wrapped, _KEYSTORE_AAD)
        except InvalidTag as exc:
            raise WrongPasswordError("Password salah.") from exc
        finally:
            _wipe(bytearray(kek))

        expected = document.get("verifier")
        if expected:
            try:
                if not hmac.compare_digest(_verifier(master_key), bytes.fromhex(expected)):
                    raise WrongPasswordError("Password salah.")
            except ValueError as exc:
                raise CryptoError("Key store rusak (verifier).") from exc
        return master_key

    def _establish_session(self, master_key: bytes, document: dict) -> None:
        # Sanity: the master key must match the store verifier.
        expected = document.get("verifier")
        if expected:
            try:
                if not hmac.compare_digest(_verifier(master_key), bytes.fromhex(expected)):
                    raise WrongPasswordError("Password salah.")
            except ValueError as exc:
                raise CryptoError("Key store rusak (verifier).") from exc

        if native_backend.is_available():
            entropy = native_backend.random_bytes(32)
            self._ticket = native_backend.protect(master_key, entropy=entropy)
            self._ticket_entropy = entropy
            self._fallback_key = None
        else:
            # Portable fallback: still requires a real unwrap with the password.
            self._ticket = None
            self._ticket_entropy = None
            self._fallback_key = bytes(master_key)

    def _materialize_key(self) -> Optional[bytes]:
        """Return the live master key, or ``None`` when a capability is absent."""
        if self._ticket is not None:
            return native_backend.unprotect(self._ticket, entropy=self._ticket_entropy)
        if self._fallback_key is not None:
            return bytes(self._fallback_key)
        return None

    def _require_key(self) -> bytes:
        if not self.is_unlocked():
            raise LockedError("Crypto session terkunci. Unlock diperlukan.")
        master_key = self._materialize_key()
        if master_key is None:
            raise LockedError("Crypto session terkunci. Unlock diperlukan.")
        return master_key
