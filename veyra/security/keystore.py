"""On-disk key store for the Veyra crypto session.

The store never holds a plaintext secret.  It contains *only* material that is
already useless without the correct password:

* the Argon2id salt + parameters,
* a nonce,
* the **wrapped** (AES-256-GCM) master key,
* a verifier (HMAC of the master key) used to detect a wrong/rolled key.

On top of that the whole document is wrapped with the native DPAPI boundary
(:mod:`veyra.security.native_backend`), so lifting the file to another Windows
user / machine does not reveal even the wrapped material.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from veyra.security import native_backend

__all__ = ["KeyStore", "KeyStoreError", "DEFAULT_KEYSTORE_PATH"]

#: Default location of the crypto key store (project local, no plaintext).
DEFAULT_KEYSTORE_PATH = Path(__file__).resolve().parents[2] / ".veyra" / "keystore.bin"

_MAGIC = b"VKEY1"


class KeyStoreError(RuntimeError):
    """Raised when the key store cannot be read, written or decoded."""


class KeyStore:
    """Loads / stores the DPAPI + KEK protected key store document."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_KEYSTORE_PATH

    # -- state ---------------------------------------------------------
    def exists(self) -> bool:
        """Return ``True`` when a key store file is present."""
        try:
            return self.path.is_file()
        except OSError:
            return False

    # -- io ------------------------------------------------------------
    def load(self) -> dict:
        """Read and decode the document (DPAPI unwrap + JSON parse)."""
        if not self.exists():
            raise KeyStoreError("Key store belum dibuat.")
        try:
            raw = self.path.read_bytes()
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
        if document.get("version") != 1:
            raise KeyStoreError("Versi key store tidak didukung.")
        return document

    def save(self, document: dict) -> None:
        """Atomically write ``document`` wrapped in the native boundary."""
        document.setdefault("format", "veyra-keystore")
        document.setdefault("version", 1)
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
