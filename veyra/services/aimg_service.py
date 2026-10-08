"""Authenticated ``.aimg`` container built on top of the Veyra crypto session.

Format (version 2)::

    "AIMG" | header_len (4, big endian) | header (JSON, header_len) |
    thumb_ciphertext (thumb_len) | payload_ciphertext

* The header is authenticated as **AAD**, so its fields (original extension,
  salt, nonce, thumbnail length, ...) cannot be tampered with unnoticed.
* The payload is AES-256-GCM (authenticated encryption) with a fresh 12 byte
  nonce from the native CSPRNG.
* The per file key is derived from the **session master key** with
  HKDF-SHA256.  There is therefore exactly one password for the whole vault
  (never one password per ``.aimg``), and no operation - not even reading the
  header key - is possible while the crypto session is locked.
* An optional small thumbnail is embedded (also encrypted) so grid previews of
  a folder with thousands of files do not require decrypting every full image.

The previous version-1 files were keyed directly from the password.  They are
detected on read and rejected with a clear message instead of being silently
mis-decrypted.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional, Tuple

from veyra.security import native_backend
from veyra.security.crypto_session import CryptoService, LockedError

MAGIC = b"AIMG"
VERSION = 2
LEGACY_VERSION = 1
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg"}

MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}

_SALT_SIZE = 16
_NONCE_SIZE = 12
_PAYLOAD_INFO = b"veyra.aimg.payload.v2"
_THUMB_INFO = b"veyra.aimg.thumb.v2"

#: Embedded thumbnail settings (kept intentionally small).
THUMBNAIL_MAX = 256
THUMBNAIL_QUALITY = 72

#: Bounded LRU cache limits (see :class:`_BoundedCache`).
_PAYLOAD_CACHE_ITEMS = 48
_PAYLOAD_CACHE_BYTES = 96 * 1024 * 1024
_THUMB_CACHE_ITEMS = 512
_THUMB_CACHE_BYTES = 32 * 1024 * 1024


class AimgError(ValueError):
    """Raised for any ``.aimg`` format / authentication failure."""


# ---------------------------------------------------------------------------
# bounded LRU cache
# ---------------------------------------------------------------------------

class _BoundedCache:
    """Tiny thread-agnostic LRU capped by both entry count and total bytes."""

    def __init__(self, max_items: int, max_bytes: int) -> None:
        self._max_items = max(1, int(max_items))
        self._max_bytes = max(1, int(max_bytes))
        self._items: "OrderedDict[tuple, bytes]" = OrderedDict()
        self._bytes = 0

    @staticmethod
    def _size_of(value) -> int:
        if isinstance(value, tuple) and value:
            return len(value[0])
        return len(value)

    def get(self, key: tuple):
        value = self._items.get(key)
        if value is None:
            return None
        self._items.move_to_end(key)
        return value

    def put(self, key: tuple, value) -> None:
        if key in self._items:
            self._bytes -= self._size_of(self._items.pop(key))
        self._items[key] = value
        self._bytes += self._size_of(value)
        while self._items and (
            len(self._items) > self._max_items or self._bytes > self._max_bytes
        ):
            _, evicted = self._items.popitem(last=False)
            self._bytes -= self._size_of(evicted)

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0

    def __len__(self) -> int:  # pragma: no cover - introspection helper
        return len(self._items)


# ---------------------------------------------------------------------------
# header helpers
# ---------------------------------------------------------------------------

@dataclass
class AimgHeader:
    """Parsed ``.aimg`` header (no decryption performed)."""

    version: int
    ext: str
    salt: bytes
    nonce: bytes
    thumb_nonce: bytes
    thumb_len: int
    header_bytes: bytes
    raw_size: int

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "extension": self.ext,
            "mime_type": MIME_TYPES.get(self.ext, "application/octet-stream"),
            "has_thumbnail": self.thumb_len > 0,
        }


def _parse(path: Path) -> Tuple[AimgHeader, bytes, bytes]:
    """Return ``(header, thumb_ciphertext, payload_ciphertext)``."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AimgError(f"Tidak dapat membaca file: {exc}") from exc

    if len(raw) < 8 or raw[:4] != MAGIC:
        raise AimgError("Format .aimg tidak valid.")
    length = int.from_bytes(raw[4:8], "big")
    if length < 2 or 8 + length > len(raw):
        raise AimgError("Header .aimg rusak.")

    header_bytes = raw[8:8 + length]
    try:
        meta = json.loads(header_bytes.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise AimgError("Header .aimg rusak.") from exc

    version = meta.get("version")
    if version == LEGACY_VERSION:
        raise AimgError(
            "File .aimg versi lama (berbasis password) tidak didukung; "
            "impor ulang dengan crypto session."
        )
    if version != VERSION:
        raise AimgError("Versi .aimg tidak didukung.")
    ext = meta.get("ext")
    if ext not in SUPPORTED_EXTENSIONS:
        raise AimgError("Format image tidak didukung.")

    try:
        salt = bytes.fromhex(meta["salt"])
        nonce = bytes.fromhex(meta["nonce"])
        thumb_nonce = bytes.fromhex(meta.get("tnonce", "")) if meta.get("tnonce") else b""
        thumb_len = int(meta.get("thumb", 0))
    except (KeyError, ValueError) as exc:
        raise AimgError("Parameter keamanan .aimg tidak valid.") from exc

    if len(salt) != _SALT_SIZE or len(nonce) != _NONCE_SIZE:
        raise AimgError("Parameter keamanan .aimg tidak valid.")
    if thumb_len < 0:
        raise AimgError("Parameter keamanan .aimg tidak valid.")
    if thumb_len and len(thumb_nonce) != _NONCE_SIZE:
        raise AimgError("Parameter keamanan .aimg tidak valid.")

    body = raw[8 + length:]
    if thumb_len > len(body):
        raise AimgError("Header .aimg rusak (thumbnail melebihi payload).")
    thumb_ct = body[:thumb_len]
    payload_ct = body[thumb_len:]

    header = AimgHeader(
        version=version,
        ext=ext,
        salt=salt,
        nonce=nonce,
        thumb_nonce=thumb_nonce,
        thumb_len=thumb_len,
        header_bytes=header_bytes,
        raw_size=len(raw),
    )
    return header, thumb_ct, payload_ct


def inspect_metadata(path) -> dict:
    """Read only the ``.aimg`` header (cheap; no decryption, no full read)."""
    target = Path(path)
    try:
        with target.open("rb") as handle:
            prefix = handle.read(8)
            if len(prefix) < 8 or prefix[:4] != MAGIC:
                raise AimgError("Format .aimg tidak valid.")
            length = int.from_bytes(prefix[4:8], "big")
            if length < 2 or length > 1_000_000:
                raise AimgError("Header .aimg rusak.")
            header_bytes = handle.read(length)
    except OSError as exc:
        raise AimgError(f"Tidak dapat membaca file: {exc}") from exc

    try:
        meta = json.loads(header_bytes.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise AimgError("Header .aimg rusak.") from exc

    version = meta.get("version")
    if version not in (VERSION, LEGACY_VERSION):
        raise AimgError("Versi .aimg tidak didukung.")
    ext = meta.get("ext")
    if version == VERSION and ext not in SUPPORTED_EXTENSIONS:
        raise AimgError("Format image tidak didukung.")
    return {
        "version": version,
        "extension": ext,
        "mime_type": MIME_TYPES.get(ext, "application/octet-stream"),
        "has_thumbnail": int(meta.get("thumb", 0)) > 0,
        "width": int(meta.get("w", 0)) or None,
        "height": int(meta.get("h", 0)) or None,
    }


def is_aimg(path) -> bool:
    """Return ``True`` when ``path`` looks like a ``.aimg`` container."""
    return Path(path).suffix.lower() == ".aimg"


# ---------------------------------------------------------------------------
# crypto operations
# ---------------------------------------------------------------------------

def _derive_keys(crypto: CryptoService, salt: bytes) -> Tuple[bytes, bytes]:
    payload_key = crypto.derive_subkey(salt, _PAYLOAD_INFO)
    thumb_key = crypto.derive_subkey(salt, _THUMB_INFO)
    return payload_key, thumb_key


def _make_thumbnail(path: Path, ext: str) -> Tuple[bytes, Optional[int], Optional[int]]:
    """Build a small preview (best effort): ``(bytes, width, height)``.

    The real pixel size is captured here (once, at encryption time) and stored
    in the header so the grid can show the true resolution without decrypting
    the full payload later.
    """
    try:
        if ext == ".svg":
            data = path.read_bytes()
            return (data, None, None) if 0 < len(data) <= 256 * 1024 else (b"", None, None)

        from PIL import Image  # local import: optional dependency

        with Image.open(path) as image:
            width, height = image.size
            frame = image.convert("RGBA")
            frame.thumbnail((THUMBNAIL_MAX, THUMBNAIL_MAX))
            buffer = BytesIO()
            background = Image.new("RGB", frame.size, (255, 255, 255))
            background.paste(frame, mask=frame.split()[-1])
            background.save(buffer, format="JPEG", quality=THUMBNAIL_QUALITY)
            return buffer.getvalue(), width, height
    except Exception:  # noqa: BLE001 - thumbnail is best effort only
        return b"", None, None


def encrypt_file(image, crypto: CryptoService) -> Path:
    """Encrypt ``image`` into ``image.aimg`` (replacement, not a copy).

    Order of operations (safety first):

    1. build the ``.aimg`` atomically,
    2. authenticate / validate the result against the plaintext,
    3. only then delete the original.
    """
    source = Path(image)
    ext = source.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise AimgError("Format image tidak didukung.")
    if not source.is_file():
        raise AimgError("File sumber tidak ditemukan.")
    if not crypto.is_unlocked():
        raise LockedError("Crypto session terkunci. Unlock diperlukan.")

    target = source.with_suffix(".aimg")
    salt = native_backend.random_bytes(_SALT_SIZE)
    nonce = native_backend.random_bytes(_NONCE_SIZE)
    thumb, img_width, img_height = _make_thumbnail(source, ext)
    thumb_nonce = native_backend.random_bytes(_NONCE_SIZE) if thumb else b""

    # The stored thumbnail field is the *ciphertext* length (plaintext + 16 byte
    # GCM tag), which is what the reader uses to split the body.
    thumb_ct_len = (len(thumb) + 16) if thumb else 0
    meta = {
        "version": VERSION,
        "ext": ext,
        "salt": salt.hex(),
        "nonce": nonce.hex(),
        "tnonce": thumb_nonce.hex(),
        "thumb": thumb_ct_len,
        "w": int(img_width) if img_width else 0,
        "h": int(img_height) if img_height else 0,
        "alg": "AES-256-GCM",
        "kdf": "HKDF-SHA256",
    }
    header_bytes = json.dumps(meta, separators=(",", ":"), sort_keys=True).encode("utf-8")

    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise AimgError(f"Tidak dapat membaca sumber: {exc}") from exc

    payload_key, thumb_key = _derive_keys(crypto, salt)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    payload_ct = AESGCM(payload_key).encrypt(nonce, payload, header_bytes)
    thumb_ct = AESGCM(thumb_key).encrypt(thumb_nonce, thumb, header_bytes) if thumb else b""

    blob = MAGIC + len(header_bytes).to_bytes(4, "big") + header_bytes + thumb_ct + payload_ct
    _atomic_create(target, blob)

    try:
        # Authenticate + verify the freshly written container before touching the source.
        raw_check = read_payload(target, crypto)
        if raw_check != payload:
            raise AimgError("Verifikasi hasil enkripsi gagal.")
    except Exception:
        target.unlink(missing_ok=True)
        raise

    source.unlink()
    return target


def decrypt_file(aimg, crypto: CryptoService) -> Path:
    """Restore ``x.aimg`` into ``x.<original ext>`` (replacement, not a copy)."""
    source = Path(aimg)
    if not source.is_file():
        raise AimgError("File .aimg tidak ditemukan.")
    if not crypto.is_unlocked():
        raise LockedError("Crypto session terkunci. Unlock diperlukan.")

    header, _thumb_ct, _payload_ct = _parse(source)
    data = read_payload(source, crypto)
    target = source.with_suffix(header.ext)
    _atomic_create(target, data)
    try:
        if target.read_bytes() != data:
            raise AimgError("Verifikasi hasil dekripsi gagal.")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    source.unlink()
    return target


def read_payload(aimg, crypto: CryptoService) -> bytes:
    """Decrypt and return the plaintext image bytes (in memory, no temp file)."""
    header, _thumb_ct, payload_ct = _parse(Path(aimg))
    if not crypto.is_unlocked():
        raise LockedError("Crypto session terkunci. Unlock diperlukan.")
    payload_key, _ = _derive_keys(crypto, header.salt)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        return AESGCM(payload_key).decrypt(header.nonce, payload_ct, header.header_bytes)
    except Exception as exc:  # noqa: BLE001 - InvalidTag etc.
        raise AimgError("Authentication .aimg gagal atau file dimodifikasi.") from exc


def read_thumbnail(aimg, crypto: CryptoService) -> Optional[bytes]:
    """Return the embedded thumbnail bytes, or ``None`` when absent."""
    header, thumb_ct, _ = _parse(Path(aimg))
    if not header.thumb_len or not thumb_ct:
        return None
    if not crypto.is_unlocked():
        raise LockedError("Crypto session terkunci. Unlock diperlukan.")
    _, thumb_key = _derive_keys(crypto, header.salt)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        return AESGCM(thumb_key).decrypt(header.thumb_nonce, thumb_ct, header.header_bytes)
    except Exception as exc:  # noqa: BLE001
        raise AimgError("Thumbnail .aimg gagal diautentikasi.") from exc


def validate_aimg(aimg, crypto: Optional[CryptoService] = None, full: bool = False) -> bool:
    """Validate structure, and (optionally) full authentication.

    With ``crypto`` unlocked and ``full=True`` the payload is fully decrypted
    and authenticated; otherwise only the header structure is checked.
    """
    header, _thumb_ct, _payload_ct = _parse(Path(aimg))
    if full:
        if crypto is None:
            raise AimgError("Crypto session diperlukan untuk validasi penuh.")
        read_payload(aimg, crypto)
    return True


# ---------------------------------------------------------------------------
# filesystem helpers
# ---------------------------------------------------------------------------

def _atomic_create(target: Path, data: bytes) -> None:
    """Create ``target`` atomically; refuse to overwrite an existing file."""
    if target.exists():
        raise FileExistsError(f"Target sudah ada: {target}")
    fd, temp = tempfile.mkstemp(prefix=".aimg-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, target)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def _batch(root, crypto: CryptoService, decrypt: bool,
           progress: Optional[Callable] = None) -> dict:
    root = Path(root)
    if not root.is_dir():
        raise AimgError("Folder tidak valid atau tidak ditemukan.")

    if decrypt:
        found = sorted(p for p in root.rglob("*.aimg") if p.is_file())
    else:
        found = sorted(
            p for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        )

    result = {"found": len(found), "success": 0, "failed": 0, "errors": []}
    for path in found:
        try:
            (decrypt_file if decrypt else encrypt_file)(path, crypto)
            result["success"] += 1
        except Exception as exc:  # noqa: BLE001 - isolate single file failure
            result["failed"] += 1
            result["errors"].append({"path": str(path), "error": str(exc)})
        if progress:
            progress(result)
    return result


def encrypt_folder_recursive(path, crypto: CryptoService, progress=None) -> dict:
    return _batch(path, crypto, False, progress)


def decrypt_folder_recursive(path, crypto: CryptoService, progress=None) -> dict:
    return _batch(path, crypto, True, progress)


# ---------------------------------------------------------------------------
# service wrapper (caches + convenience for the web layer)
# ---------------------------------------------------------------------------

class AimgService:
    """High level, cache backed facade used by the HTTP routes.

    The wrapped :class:`CryptoService` is the single source of truth for
    authorization; every read/write refuses to work while it is locked.
    """

    def __init__(self, crypto: CryptoService) -> None:
        self.crypto = crypto
        self._payload_cache = _BoundedCache(_PAYLOAD_CACHE_ITEMS, _PAYLOAD_CACHE_BYTES)
        self._thumb_cache = _BoundedCache(_THUMB_CACHE_ITEMS, _THUMB_CACHE_BYTES)

    # -- auth passthrough ---------------------------------------------
    def is_unlocked(self) -> bool:
        return self.crypto.is_unlocked()

    def unlock(self, password: str) -> bool:
        return self.crypto.unlock(password)

    def lock(self) -> None:
        self.crypto.lock()
        self.clear_cache()

    def status(self) -> dict:
        return self.crypto.status()

    def clear_cache(self) -> None:
        self._payload_cache.clear()
        self._thumb_cache.clear()

    # -- cache helpers ------------------------------------------------
    @staticmethod
    def _key(path: Path) -> tuple:
        try:
            stat = path.stat()
            return (str(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            return (str(path), 0, 0)

    # -- reads --------------------------------------------------------
    def payload(self, path) -> Tuple[bytes, str]:
        """Return ``(plaintext_bytes, mime_type)`` for a ``.aimg`` file."""
        target = Path(path)
        key = self._key(target)
        cached = self._payload_cache.get(key)
        if cached is not None:
            return cached

        header, _thumb_ct, _payload_ct = _parse(target)
        data = read_payload(target, self.crypto)
        result = (data, MIME_TYPES.get(header.ext, "application/octet-stream"))
        self._payload_cache.put(key, result)
        return result

    def thumbnail(self, path) -> Optional[Tuple[bytes, str]]:
        """Return ``(thumbnail_bytes, mime_type)`` or ``None`` when absent."""
        target = Path(path)
        key = self._key(target)
        cached = self._thumb_cache.get(key)
        if cached is not None:
            return cached

        thumb = read_thumbnail(target, self.crypto)
        if not thumb:
            return None
        mime = MIME_TYPES[".svg"] if thumb[:1] == b"<" else "image/jpeg"
        result = (thumb, mime)
        self._thumb_cache.put(key, result)
        return result

    # -- writes -------------------------------------------------------
    def encrypt_file(self, image) -> Path:
        return encrypt_file(image, self.crypto)

    def decrypt_file(self, aimg) -> Path:
        return decrypt_file(aimg, self.crypto)

    def encrypt_folder(self, path, progress=None) -> dict:
        return encrypt_folder_recursive(path, self.crypto, progress)

    def decrypt_folder(self, path, progress=None) -> dict:
        return decrypt_folder_recursive(path, self.crypto, progress)
