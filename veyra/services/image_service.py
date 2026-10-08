"""Veyra backend services - Image viewing and management services.

This module backs the real image viewer: it walks the local filesystem,
discovers supported image files, reads their real metadata (size, pixel
dimensions, MIME type) and safely serves the raw bytes to the browser.

Pixel dimensions are parsed straight from the image headers so the service
has no hard dependency on Pillow (which is not installed in the venv). When a
dimension cannot be determined the field is simply ``None`` and the frontend
falls back to the ``naturalWidth``/``naturalHeight`` of the decoded image.
"""

from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


#: Image formats the viewer supports (all of them are browser displayable).
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg"}

_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


#: Container extension served as a first-class image source (needs a crypto
#: session to be unlocked before its payload can be read).
AIMG_EXTENSION = ".aimg"


@dataclass
class ImageInfo:
    """Real metadata about an image file on disk."""

    filename: str
    path: str
    size: int
    ext: str
    file_type: str
    mime_type: str
    width: Optional[int] = None
    height: Optional[int] = None
    created_at: Optional[float] = None
    modified_at: Optional[float] = None
    encrypted: bool = False

    def to_dict(self) -> dict:
        """Serialisable snapshot consumed by the frontend grid."""
        return {
            "name": self.filename,
            "path": self.path,
            "size": self.size,
            "ext": self.ext,
            "type": self.file_type,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "modified_at": self.modified_at,
            "encrypted": self.encrypted,
        }


# ---------------------------------------------------------------------------
# Image header parsing (no external dependency)
# ---------------------------------------------------------------------------

def read_image_size(path: Path) -> Tuple[Optional[int], Optional[int]]:
    """Return the real ``(width, height)`` of an image, or ``(None, None)``.

    The dimensions are read from the file header only, so this is cheap even
    for large images. Unknown/corrupt headers degrade gracefully to ``None``.
    """
    try:
        suffix = path.suffix.lower()
        if suffix == ".svg":
            return _svg_size(path)

        with path.open("rb") as handle:
            head = handle.read(64)

        if not head:
            return None, None
        if suffix == ".png":
            return _png_size(head)
        if suffix == ".gif":
            return _gif_size(head)
        if suffix == ".bmp":
            return _bmp_size(head)
        if suffix == ".webp":
            return _webp_size(head)
        if suffix in (".jpg", ".jpeg"):
            return _jpeg_size(path)
    except (OSError, ValueError, struct.error):
        return None, None
    return None, None


def _png_size(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def _gif_size(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    if len(data) < 10 or data[:3] != b"GIF":
        return None, None
    width = int.from_bytes(data[6:8], "little")
    height = int.from_bytes(data[8:10], "little")
    return width, height


def _bmp_size(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    if len(data) < 26 or data[:2] != b"BM":
        return None, None
    width = struct.unpack("<i", data[18:22])[0]
    height = struct.unpack("<i", data[22:26])[0]
    return abs(width), abs(height)


def _webp_size(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None, None
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if chunk == b"VP8 ":
        if data[23:26] != b"\x9d\x01\x2a":
            return None, None
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return width, height
    if chunk == b"VP8L":
        if data[20] != 0x2F:
            return None, None
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return width, height
    return None, None


def _jpeg_size(path: Path) -> Tuple[Optional[int], Optional[int]]:
    """Scan JPEG markers for a Start-Of-Frame segment and read its size."""
    with path.open("rb") as handle:
        if handle.read(2) != b"\xff\xd8":
            return None, None
        while True:
            byte = handle.read(1)
            if not byte:
                return None, None
            if byte != b"\xff":
                continue
            marker = handle.read(1)
            while marker == b"\xff":
                marker = handle.read(1)
            if not marker:
                return None, None
            code = marker[0]
            if code in (0xD8, 0xD9) or 0xD0 <= code <= 0xD7:
                continue
            length_bytes = handle.read(2)
            if len(length_bytes) < 2:
                return None, None
            segment_length = int.from_bytes(length_bytes, "big")
            if 0xC0 <= code <= 0xCF and code not in (0xC4, 0xC8, 0xCC):
                info = handle.read(5)
                if len(info) < 5:
                    return None, None
                height = int.from_bytes(info[1:3], "big")
                width = int.from_bytes(info[3:5], "big")
                return width, height
            handle.seek(segment_length - 2, os.SEEK_CUR)


def _svg_size(path: Path) -> Tuple[Optional[int], Optional[int]]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:4096]
    except OSError:
        return None, None
    match = re.search(r"<svg[^>]*>", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None, None
    tag = match.group(0)

    def _attr(name: str) -> Optional[int]:
        found = re.search(name + r'\s*=\s*["\']([0-9]+(?:\.[0-9]+)?)', tag, re.IGNORECASE)
        return int(float(found.group(1))) if found else None

    width, height = _attr("width"), _attr("height")
    if width and height:
        return width, height
    view_box = re.search(
        r'viewBox\s*=\s*["\']\s*[0-9.\-]+\s+[0-9.\-]+\s+([0-9.]+)\s+([0-9.]+)',
        tag,
        re.IGNORECASE,
    )
    if view_box:
        return int(float(view_box.group(1))), int(float(view_box.group(2)))
    return None, None


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

class ImageService:
    """Filesystem backed service for browsing and serving image files.

    Browsing works on absolute paths so the viewer can open any local folder
    (e.g. ``J:\\...\\Revi Lia``); ``root_path`` is kept as a fallback root used
    when a caller does not provide a path.
    """

    SUPPORTED_EXTENSIONS = SUPPORTED_EXTENSIONS

    def __init__(self, root_path: Optional[Path] = None):
        self.root_path = Path(root_path).resolve() if root_path else Path(__file__).resolve().parents[2]

    # -- discovery -----------------------------------------------------
    def is_supported(self, path: Path) -> bool:
        """Return ``True`` when ``path`` has a supported image extension."""
        return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS

    def is_listable(self, path: Path) -> bool:
        """Return ``True`` for supported images and for ``.aimg`` containers."""
        suffix = Path(path).suffix.lower()
        return suffix in SUPPORTED_EXTENSIONS or suffix == AIMG_EXTENSION

    def list_images(self, directory) -> List[ImageInfo]:
        """List every supported image file in ``directory`` with real metadata.

        ``.aimg`` containers are included too.  Their metadata is read from the
        authenticated header only (never the payload), so listing a folder with
        thousands of encrypted files stays cheap and does not decrypt anything.
        """
        directory = Path(directory)
        if not directory.is_dir():
            return []
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            return []

        images: List[ImageInfo] = []
        for entry in entries:
            try:
                if not entry.is_file() or not self.is_listable(entry):
                    continue
                stat = entry.stat()
                suffix = entry.suffix.lower()
                if suffix == AIMG_EXTENSION:
                    images.append(self._aimg_info(entry, stat))
                    continue
                width, height = read_image_size(entry)
                images.append(ImageInfo(
                    filename=entry.name,
                    path=str(entry),
                    size=stat.st_size,
                    ext=suffix.lstrip("."),
                    file_type=entry.suffix.lstrip(".").upper(),
                    mime_type=_MIME_TYPES.get(suffix, "application/octet-stream"),
                    width=width,
                    height=height,
                    created_at=stat.st_ctime,
                    modified_at=stat.st_mtime,
                ))
            except OSError:
                continue
        return images

    @staticmethod
    def _aimg_info(entry: Path, stat) -> ImageInfo:
        """Build metadata for one ``.aimg`` container from its header only."""
        from veyra.services.aimg_service import AimgError, inspect_metadata

        original_ext = None
        mime_type = "application/octet-stream"
        width = None
        height = None
        try:
            header = inspect_metadata(entry)
            original_ext = header.get("extension")
            mime_type = header.get("mime_type", mime_type)
            width = header.get("width")
            height = header.get("height")
        except (AimgError, OSError, ValueError):
            original_ext = None

        if original_ext is not None:
            label = original_ext.lstrip(".").upper()
        else:
            label = "AIMG"

        return ImageInfo(
            filename=entry.name,
            path=str(entry),
            size=stat.st_size,
            ext=(original_ext.lstrip(".") if original_ext else "aimg"),
            file_type=label,
            mime_type=mime_type,
            width=width,
            height=height,
            created_at=stat.st_ctime,
            modified_at=stat.st_mtime,
            encrypted=True,
        )

    # Backwards compatible alias.
    def list_directory(self, directory) -> List[ImageInfo]:
        """Alias of :meth:`list_images` kept for existing callers."""
        return self.list_images(directory)

    def list_subdirectories(self, directory) -> List[Path]:
        """List the immediate sub-folders of ``directory`` (sorted)."""
        directory = Path(directory)
        if not directory.is_dir():
            return []
        try:
            return sorted(
                (entry for entry in directory.iterdir() if entry.is_dir()),
                key=lambda item: item.name.lower(),
            )
        except OSError:
            return []

    def has_subdirectories(self, directory) -> bool:
        """Return ``True`` when ``directory`` contains at least one sub-folder."""
        try:
            with os.scandir(directory) as iterator:
                return any(entry.is_dir() for entry in iterator)
        except OSError:
            return False

    def folder_children(self, directory) -> dict:
        """Immediate sub-folders plus a ``has_children`` flag for the tree."""
        directory = Path(directory)
        if not directory.is_dir():
            return {"ok": False, "folders": []}
        folders = [
            {
                "name": sub.name,
                "path": str(sub),
                "has_children": self.has_subdirectories(sub),
            }
            for sub in self.list_subdirectories(directory)
        ]
        return {"ok": True, "folders": folders}

    # -- access --------------------------------------------------------
    def resolve_path(self, path_str: str) -> Optional[Path]:
        """Resolve ``path_str`` to an absolute path (or ``None`` when empty)."""
        if path_str is None:
            return None
        try:
            return Path(path_str).expanduser().resolve()
        except (OSError, ValueError):
            return None

    def read_image(self, path_str: str) -> Optional[Tuple[bytes, str]]:
        """Return ``(bytes, mime_type)`` for a supported image file, else ``None``.

        Only files with a supported image extension can be served, which keeps
        the endpoint from being able to leak arbitrary files.
        """
        path = self.resolve_path(path_str)
        if path is None or not path.is_file() or not self.is_supported(path):
            return None
        try:
            data = path.read_bytes()
        except OSError:
            return None
        return data, _MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")


class FileService:
    """Service for reading arbitrary file content from the sandbox root."""

    def __init__(self, root_path: Optional[Path] = None):
        self.root_path = Path(root_path).resolve() if root_path else Path(__file__).resolve().parents[2]

    def read_file(self, relative_path: str) -> Optional[bytes]:
        """Read a file relative to the sandbox root (``None`` when missing)."""
        target = self._resolve_safe(relative_path)
        if target is None or not target.is_file():
            return None
        try:
            return target.read_bytes()
        except OSError:
            return None

    def _resolve_safe(self, relative_path: str) -> Optional[Path]:
        try:
            target = (self.root_path / relative_path).resolve()
            if str(target).startswith(str(self.root_path)):
                return target
        except (OSError, ValueError):
            pass
        return None
