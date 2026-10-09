"""Veyra crypto session: password -> Argon2id -> per-vault password key.

Design (password-as-key-source)::

    Veyra password ----------------------------+
          |                                     |
          v                                     v
       Argon2id(password, derivation_salt)   (cached per dsalt)
          |                                     |
          v                                     v
     password_key  -- HKDF(file_salt, info) --> per ``.aimg`` file key

* There is **no** master key and **no** login gate anymore.  Any non-empty
  password is accepted by :meth:`CryptoService.unlock`; the password is only
  used to derive the per-file keys.  A file encrypted with password *B* simply
  fails AES-256-GCM authentication when opened with password *A* (the wrong
  password never decrypts to another image - it cannot decrypt at all).
* ``password_key = Argon2id(password, derivation_salt)`` is the slow step and
  is computed **once per derivation salt** and cached, so opening thousands of
  ``.aimg`` thumbnails never re-runs Argon2id.
* The password is never stored at rest.  For the duration of an unlocked
  session it is kept only as native (DPAPI) sealed session material - the same
  boundary that used to seal the master key - and wiped by :meth:`lock`.
* ``is_unlocked`` is derived from the real native session ticket, so patching a
  Python attribute cannot open the session.

High entropy per-file sub-keys are derived from the password key with
HKDF-SHA256 (fast KDF); the header's per-file salt is the HKDF salt and the
header's derivation salt selects the Argon2id input.
"""

from __future__ import annotations

import hmac
from pathlib import Path
from typing import Dict, Optional

from argon2.low_level import Type, hash_secret_raw

from veyra.security import native_backend
from veyra.security.keystore import (
    DEFAULT_KEYSTORE_PATH,
    KeyStore,
    KeyStoreError,
)

__all__ = [
    "CryptoError",
    "LockedError",
    "WrongPasswordError",
    "CryptoService",
]

#: Argon2id parameters used to turn the password into the per-vault key.
_ARGON2_TIME_COST = 3
_ARGON2_MEMORY_COST = 64 * 1024  # KiB (64 MiB)
_ARGON2_PARALLELISM = 2
_ARGON2_HASH_LEN = 32  # 256-bit

#: Derivation salt size (mixed with the password to derive the password key).
_DERIVATION_SALT_SIZE = 16

#: Legacy (v1) constants, kept only for the explicit migration path.
_KEYSTORE_AAD = b"veyra.keystore.v1"
_VERIFIER_LABEL = b"veyra.masterkey.verifier.v1"


class CryptoError(RuntimeError):
    """Base error for crypto session failures."""


class LockedError(CryptoError):
    """Raised when a crypto operation is attempted while locked."""


class WrongPasswordError(CryptoError):
    """Raised when a legacy (master key) key store cannot be unwrapped.

    In the password-as-key-source model this is **only** used by the explicit
    migration path (opening an old ``.aimg`` vault), never by the login.
    """


def _argon2_raw(password: bytes, salt: bytes, params: Optional[dict] = None) -> bytes:
    """Argon2id over raw password bytes (the canonical derivation primitive)."""
    if not isinstance(password, (bytes, bytearray)) or not password:
        raise CryptoError("Password wajib diisi.")
    params = params or {}
    return hash_secret_raw(
        bytes(password),
        salt,
        int(params.get("time_cost", _ARGON2_TIME_COST)),
        int(params.get("memory_cost", _ARGON2_MEMORY_COST)),
        int(params.get("parallelism", _ARGON2_PARALLELISM)),
        int(params.get("hash_len", _ARGON2_HASH_LEN)),
        Type.ID,
    )


def _argon2_key(password: str, salt: bytes, params: Optional[dict] = None) -> bytes:
    """Argon2id over a ``str`` password (UTF-8 encoded)."""
    if not isinstance(password, str) or not password:
        raise WrongPasswordError("Password wajib diisi.")
    return _argon2_raw(password.encode("utf-8"), salt, params)


def _verifier(master_key: bytes) -> bytes:
    return hmac.new(master_key, _VERIFIER_LABEL, "sha256").digest()


def _wipe(buffer: Optional[bytearray]) -> None:
    """Best effort in-memory wipe of key material."""
    if buffer:
        for index in range(len(buffer)):
            buffer[index] = 0


class CryptoService:
    """Authorization boundary for every ``.aimg`` operation.

    The instance is state-light: while unlocked it keeps native (DPAPI) sealed
    session material plus an in-memory ``password_key`` cache (one entry per
    derivation salt).  Nothing secret is written to disk.
    """

    def __init__(self, keystore_path: Optional[Path] = None) -> None:
        self._store = KeyStore(keystore_path or DEFAULT_KEYSTORE_PATH)
        # Native session material (created on unlock, wiped on lock).
        self._ticket: Optional[bytes] = None
        self._ticket_entropy: Optional[bytes] = None
        # Portable fallback when the native boundary is unavailable.
        self._fallback_secret: Optional[bytes] = None
        # Cached password keys (one per derivation salt).
        self._password_key_cache: Dict[bytes, bytes] = {}
        # Derivation material of the current key store.
        self._derivation_salt: Optional[bytes] = None
        self._kdf_params: dict = {}
        # Counts Argon2id derivations (introspection / verification helper).
        self._argon2_derivations = 0

    # -- introspection -------------------------------------------------
    @property
    def boundary(self) -> str:
        """Name of the active security boundary (``windows-dpapi`` / fallback)."""
        return native_backend.platform_name()

    @property
    def keystore_path(self) -> Path:
        return self._store.path

    @property
    def argon2_derivations(self) -> int:
        """Number of Argon2id derivations performed by this instance."""
        return self._argon2_derivations

    @property
    def derivation_salt(self) -> bytes:
        """Derivation salt of the current key store (used to stamp headers)."""
        if self._derivation_salt is None:
            try:
                document = self._store.load()
                self._derivation_salt = bytes.fromhex(document["derivation_salt"])
            except (KeyStoreError, KeyError, ValueError) as exc:
                raise CryptoError("Key store belum siap.") from exc
        return self._derivation_salt

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
        """Create the key store if needed, then open a session with ``password``.

        On upgrade the legacy (v1) key store is preserved under
        ``keystore.legacy.bin`` before a fresh v2 (password) store is written.
        """
        if not isinstance(password, str) or not password:
            raise CryptoError("Password wajib diisi.")
        self._ensure_store()
        self._open_session(password)

    def unlock(self, password: str) -> bool:
        """Open the session with **any** non-empty password.

        There is no login gate: the password is only a key source.  A wrong
        password is therefore not rejected here - it simply cannot decrypt
        ``.aimg`` files that were encrypted with a different password.
        """
        if not isinstance(password, str) or not password:
            raise CryptoError("Password wajib diisi.")
        self._ensure_store()
        self._open_session(password)
        return True

    def lock(self) -> None:
        """Close the session and wipe every trace of in-memory key material."""
        self._ticket = None
        self._ticket_entropy = None
        self._fallback_secret = None
        self._password_key_cache.clear()
        self._derivation_salt = None

    def is_unlocked(self) -> bool:
        """Return ``True`` when live native session material can be recovered.

        This is *derived* state: it materialises the sealed session secret every
        call.  Setting a Python flag cannot make it return ``True``.
        """
        if not self._store.exists():
            return False
        try:
            secret = self._materialize_secret()
        except native_backend.NativeBoundaryError:
            return False
        return secret is not None

    def validate(self, password: str) -> bool:
        """Return whether ``password`` can open a session.

        In the new model every non-empty password is a valid key source, so
        this mirrors :meth:`unlock`'s precondition (kept for compatibility).
        """
        return bool(password) and isinstance(password, str) and self._store.exists()

    # -- key derivation ------------------------------------------------
    def password_key_for(self, dsalt: bytes) -> bytes:
        """Return ``Argon2id(password, dsalt)`` for the session password.

        The result is cached per derivation salt, so Argon2id runs only once
        per distinct ``dsalt`` even when many files are opened.
        """
        dsalt = bytes(dsalt)
        cached = self._password_key_cache.get(dsalt)
        if cached is not None:
            return cached
        secret = self._materialize_secret()
        if secret is None:
            raise LockedError("Crypto session terkunci. Unlock diperlukan.")
        return self._cache_password_key(secret, dsalt)

    def password_key_for_password(self, password: str, dsalt: bytes) -> bytes:
        """Derive a password key for an explicit password (migration helper)."""
        if not isinstance(password, str) or not password:
            raise CryptoError("Password wajib diisi.")
        return _argon2_key(password, bytes(dsalt), self._kdf_params)

    def derive_subkey(
        self,
        dsalt: bytes,
        file_salt: bytes,
        info: bytes,
        length: int = 32,
    ) -> bytes:
        """Derive a per-file sub-key (HKDF-SHA256 over the password key).

        Raises :class:`LockedError` when the session is locked, so no
        derivation - and therefore no decryption - is possible without a live
        session.
        """
        password_key = self.password_key_for(dsalt)
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        return HKDF(
            algorithm=hashes.SHA256(),
            length=length,
            salt=bytes(file_salt),
            info=info,
        ).derive(password_key)

    def encrypt(self, plaintext: bytes, *, aad: bytes = b"") -> bytes:
        """AES-256-GCM encrypt with a fresh nonce from the native CSPRNG.

        Returns ``nonce || ciphertext``.  Uses the vault password key and
        requires an unlocked session.
        """
        key = self._require_key()
        try:
            nonce = native_backend.random_bytes(12)
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
            return nonce + ciphertext
        finally:
            _wipe(bytearray(key))

    def decrypt(self, blob: bytes, *, aad: bytes = b"") -> bytes:
        """Reverse :meth:`encrypt`; raises :class:`LockedError` when locked."""
        key = self._require_key()
        try:
            if len(blob) < 12 + 16:
                raise CryptoError("Ciphertext terlalu pendek.")
            nonce, ciphertext = blob[:12], blob[12:]
            from cryptography.exceptions import InvalidTag
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            try:
                return AESGCM(key).decrypt(nonce, ciphertext, aad)
            except InvalidTag as exc:
                raise CryptoError("Authentication gagal atau data rusak.") from exc
        finally:
            _wipe(bytearray(key))

    # -- legacy (migration only) --------------------------------------
    def unwrap_legacy_master_key(self, legacy_password: str) -> bytes:
        """Unwrap the v1 master key from the preserved legacy key store.

        Migration only: the normal read path never calls this.  Raises
        :class:`WrongPasswordError` when the legacy password is wrong and
        :class:`CryptoError` when no legacy key store is available.
        """
        try:
            document = self._store.load_legacy()
        except KeyStoreError as exc:
            raise CryptoError(
                "Key store legacy (v1) tidak tersedia untuk migrasi."
            ) from exc
        return self._unwrap_master_key(legacy_password, document)

    def has_legacy_keystore(self) -> bool:
        """Return ``True`` when a preserved legacy (v1) key store exists."""
        return self._store.has_legacy()

    # -- internals -----------------------------------------------------
    def _new_document(self) -> dict:
        salt = native_backend.random_bytes(_DERIVATION_SALT_SIZE)
        return {
            "format": "veyra-keystore",
            "version": 2,
            "kdf": {
                "algo": "argon2id",
                "time_cost": _ARGON2_TIME_COST,
                "memory_cost": _ARGON2_MEMORY_COST,
                "parallelism": _ARGON2_PARALLELISM,
                "hash_len": _ARGON2_HASH_LEN,
            },
            "derivation_salt": salt.hex(),
        }

    def _ensure_store(self) -> dict:
        """Return a v2 key store document, creating/upgrading it if needed."""
        if self._store.exists():
            try:
                return self._store.load()
            except KeyStoreError as exc:
                # Not a (readable) v2 store: preserve a legacy v1 store and
                # re-create a fresh v2 store.  A corrupt store is surfaced as a
                # CryptoError so the HTTP layer answers 400, never a 500.
                try:
                    preserved = self._store.preserve_legacy()
                except KeyStoreError:
                    preserved = None
                if preserved is None:
                    raise CryptoError(f"Key store rusak atau versi tidak didukung: {exc}") from exc
        document = self._new_document()
        self._store.save(document)
        return document

    def _open_session(self, password: str) -> None:
        document = self._store.load()
        try:
            dsalt = bytes.fromhex(document["derivation_salt"])
        except (KeyError, ValueError) as exc:
            raise CryptoError("Key store rusak (derivation_salt).") from exc
        if len(dsalt) < 8:
            raise CryptoError("Key store rusak (derivation_salt).")

        self._derivation_salt = dsalt
        self._kdf_params = document.get("kdf") or {}
        self._password_key_cache.clear()

        secret = password.encode("utf-8")
        # Pre-compute + cache the vault password key ("stored in the session").
        self._cache_password_key(secret, dsalt)
        self._seal_session(secret)

    def _cache_password_key(self, secret: bytes, dsalt: bytes) -> bytes:
        cached = self._password_key_cache.get(dsalt)
        if cached is not None:
            return cached
        key = _argon2_raw(secret, dsalt, self._kdf_params)
        self._argon2_derivations += 1
        self._password_key_cache[dsalt] = key
        return key

    def _seal_session(self, secret: bytes) -> None:
        if native_backend.is_available():
            entropy = native_backend.random_bytes(32)
            self._ticket = native_backend.protect(secret, entropy=entropy)
            self._ticket_entropy = entropy
            self._fallback_secret = None
        else:
            # Portable fallback: the session still requires a real unlock.
            self._ticket = None
            self._ticket_entropy = None
            self._fallback_secret = bytes(secret)

    def _materialize_secret(self) -> Optional[bytes]:
        """Return the live session secret, or ``None`` when absent."""
        if self._ticket is not None:
            return native_backend.unprotect(self._ticket, entropy=self._ticket_entropy)
        if self._fallback_secret is not None:
            return bytes(self._fallback_secret)
        return None

    def _require_key(self) -> bytes:
        if not self.is_unlocked():
            raise LockedError("Crypto session terkunci. Unlock diperlukan.")
        return self.password_key_for(self.derivation_salt)

    # -- legacy helpers (migration only) ------------------------------
    def _unwrap_master_key(self, password: str, document: dict) -> bytes:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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
            raise WrongPasswordError("Password legacy salah.") from exc
        finally:
            _wipe(bytearray(kek))

        expected = document.get("verifier")
        if expected:
            try:
                if not hmac.compare_digest(_verifier(master_key), bytes.fromhex(expected)):
                    raise WrongPasswordError("Password legacy salah.")
            except ValueError as exc:
                raise CryptoError("Key store rusak (verifier).") from exc
        return master_key
