"""On-disk key store for the Veyra crypto session.

The key store is no longer a secret container.  In the password-as-key-source
model it holds *only* public derivation material:

* the Argon2id parameters (``kdf``),
* a random ``derivation_salt`` that is mixed with the login password to derive
  the per-vault ``password_key`` (Argon2id(password, derivation_salt)).

There is **no** wrapped master key and **no** verifier anymore: any non-empty
password is accepted for login, and the password only matters when it is used
to (fail to) open a ``.aimg`` payload (AES-256-GCM authentication).

The document is still wrapped with the native DPAPI boundary
(:mod:`veyra.security.native_backend`), so lifting the file to another Windows
user / machine does not reveal the derivation salt - but even in the clear the
salt is not a secret.

Legacy ``version == 1`` key stores (the old ``wrapped_master_key`` + verifier
design) are preserved on upgrade under ``keystore.legacy.bin`` so an explicit
migration of old ``.aimg`` files can still unwrap the old master key.  The
normal read path never touches the legacy document.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from veyra.security import native_backend

__all__ = [
    "KeyStore",
    "KeyStoreError",
    "DEFAULT_KEYSTORE_PATH",
    "LEGACY_KEYSTORE_NAME",
    "KEYSTORE_VERSION",
    "LEGACY_VERSION",
]

#: Default location of the crypto key store (project local, no plaintext).
DEFAULT_KEYSTORE_PATH = Path(__file__).resolve().parents[2] / ".veyra" / "keystore.bin"

#: File name used to preserve a ``version == 1`` key store on upgrade.
LEGACY_KEYSTORE_NAME = "keystore.legacy.bin"

#: Current key store schema (password derivation salt only).
KEYSTORE_VERSION = 2
#: Legacy key store schema (wrapped master key + verifier), migration only.
LEGACY_VERSION = 1

_MAGIC = b"VKEY1"


class KeyStoreError(RuntimeError):
    """Raised when the key store cannot be read, written or decoded."""


class KeyStore:
    """Loads / stores the DPAPI protected key store document."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_KEYSTORE_PATH

    # -- paths ---------------------------------------------------------
    @property
    def legacy_path(self) -> Path:
        """Sibling path holding a preserved legacy (v1) key store."""
        return self.path.with_name(LEGACY_KEYSTORE_NAME)

    # -- state ---------------------------------------------------------
    def exists(self) -> bool:
        """Return ``True`` when a key store file is present."""
        try:
            return self.path.is_file()
        except OSError:
            return False

    # -- io ------------------------------------------------------------
    def _read_raw(self, path: Path) -> dict:
        """Read + DPAPI-unwrap + JSON-parse one key store document."""
        if not path.is_file():
            raise KeyStoreError("Key store belum dibuat.")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise KeyStoreError(f"Tidak dapat membaca key store: {exc}") from exc

        if not raw.startswith(_MAGIC):
            raise KeyStoreError("Key store rusak (magic tidak valid).")
        blob = raw[len(_MAGIC):]

        try:
            inner = native_backend.unprotect(blob)
        except native_backend.NativeBoundaryError as exc:
            raise KeyStoreError(f"Key store tidak dapat dibuka: {exc}") from exc

        try:
            document = json.loads(inner.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise KeyStoreError("Key store rusak (JSON tidak valid).") from exc
        if not isinstance(document, dict) or document.get("format") != "veyra-keystore":
            raise KeyStoreError("Key store rusak (format tidak dikenal).")
        return document

    def load(self) -> dict:
        """Read and decode the *current* (v2) document.

        Raises :class:`KeyStoreError` when the document is a legacy (v1) or an
        unsupported version, so the caller can run the explicit upgrade path
        (preserve legacy + create a fresh v2 store).
        """
        document = self._read_raw(self.path)
        if document.get("version") != KEYSTORE_VERSION:
            raise KeyStoreError("Versi key store tidak didukung (bukan format password).")
        salt = document.get("derivation_salt")
        if not isinstance(salt, str) or not salt:
            raise KeyStoreError("Key store rusak (derivation_salt hilang).")
        return document

    def load_legacy(self) -> dict:
        """Read the preserved legacy (v1) document - migration only.

        Looks first at ``keystore.legacy.bin`` and then at the active path (in
        case the upgrade preserved nothing yet).  Raises :class:`KeyStoreError`
        when no legacy document is available.
        """
        candidates = []
        if self.legacy_path.is_file():
            candidates.append(self.legacy_path)
        if self.path.is_file():
            candidates.append(self.path)
        for candidate in candidates:
            try:
                document = self._read_raw(candidate)
            except KeyStoreError:
                continue
            if document.get("version") == LEGACY_VERSION:
                return document
        raise KeyStoreError("Key store legacy (v1) tidak ditemukan.")

    def has_legacy(self) -> bool:
        """Return ``True`` when a readable legacy (v1) document exists."""
        try:
            self.load_legacy()
            return True
        except KeyStoreError:
            return False

    def preserve_legacy(self) -> Optional[Path]:
        """Preserve the current store if it is a legacy (v1) document.

        Renames ``keystore.bin`` to ``keystore.legacy.bin`` (only when the
        active store is still ``version == 1`` and no preserved copy exists
        yet) so the old master key stays available for a future migration.
        Returns the legacy path, or ``None`` when there was nothing to
        preserve.
        """
        if not self.path.is_file():
            return None
        try:
            document = self._read_raw(self.path)
        except KeyStoreError:
            return None
        if document.get("version") != LEGACY_VERSION:
            return None
        if self.legacy_path.exists():
            return self.legacy_path
        try:
            os.replace(self.path, self.legacy_path)
        except OSError as exc:
            raise KeyStoreError(f"Tidak dapat mempreservasi key store legacy: {exc}") from exc
        return self.legacy_path

    def save(self, document: dict) -> None:
        """Atomically write ``document`` wrapped in the native boundary."""
        document.setdefault("format", "veyra-keystore")
        document.setdefault("version", KEYSTORE_VERSION)
        try:
            inner = json.dumps(
                document, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise KeyStoreError(f"Key store tidak dapat diserialisasi: {exc}") from exc

        try:
            blob = native_backend.protect(inner)
        except native_backend.NativeBoundaryError as exc:
            raise KeyStoreError(f"Key store tidak dapat dilindungi: {exc}") from exc

        payload = _MAGIC + blob
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix=".keystore-", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, self.path)
            finally:
                try:
                    os.unlink(temp)
                except FileNotFoundError:
                    pass
        except OSError as exc:
            raise KeyStoreError(f"Tidak dapat menulis key store: {exc}") from exc

    def delete(self) -> None:
        """Remove the key store file (used by tests / reset)."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise KeyStoreError(f"Tidak dapat menghapus key store: {exc}") from exc
