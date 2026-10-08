"""Veyra file operations service - copy / move / rename / new folder / delete.

This backs the right-click context menu items *Copy to Folder...*,
*Move to Folder...*, *Copy*, *Cut*, *New Folder*, *Rename* and *Remove*.

The module is intentionally **filesystem only**: it owns no database, no ORM
and no session state.  It is the "existing service layer" the viewer uses for
plain file manipulation, kept separate from the crypto (``aimg_service``) and
the database (``group_service``) layers.

Safety rules:

* every operation works on real absolute paths and refuses to run when a
  source/target is missing;
* an existing destination is **never** overwritten (the caller gets a clear
  error and can pick another name/folder);
* a ``.aimg`` container is treated as an opaque file: the encrypted bytes are
  copied/moved/moved as-is and are never decrypted here.  Any operation that
  needs plaintext stays behind the :class:`~veyra.security.crypto_session.CryptoService`.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Iterable, List

__all__ = ["FileOpsService", "FileOpsError", "FileDeleteError"]

#: Characters that are never valid in a Windows file/folder name.
_INVALID_NAME_CHARS = '<>:\"|?*'


class FileOpsError(Exception):
    """Raised for an invalid path/name or a failed filesystem operation."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class FileDeleteError(FileOpsError):
    """Raised when a file cannot be deleted (e.g. read-only, busy, permission)."""
    pass


def _validate_name(name: str) -> str:
    """Return a cleaned file/folder name or raise :class:`FileOpsError`."""
    cleaned = (name or "").strip()
    if not cleaned:
        raise FileOpsError("Nama wajib diisi.")
    if cleaned in (".", ".."):
        raise FileOpsError("Nama tidak valid.")
    if "/" in cleaned or "\\" in cleaned:
        raise FileOpsError("Nama tidak boleh mengandung pemisah folder.")
    if any(char in cleaned for char in _INVALID_NAME_CHARS):
        raise FileOpsError("Nama mengandung karakter yang tidak diizinkan.")
    return cleaned


class FileOpsService:
    """Copy / move / rename files and create folders on the local filesystem."""

    def __init__(self, allowed_roots: Iterable = None) -> None:
        self.allowed_roots = [Path(r).resolve() for r in (allowed_roots or [])]

    # -- helpers -------------------------------------------------------
    @staticmethod
    def resolve(path) -> Path:
        """Resolve ``path`` to an absolute :class:`Path`."""
        try:
            return Path(path).expanduser().resolve()
        except (OSError, ValueError) as exc:
            raise FileOpsError(f"Path tidak valid: {path}") from exc

    # -- new folder ----------------------------------------------------
    def new_folder(self, parent, name: str) -> dict:
        """Create a new folder ``name`` inside ``parent``."""
        parent_path = self.resolve(parent)
        if not parent_path.is_dir():
            raise FileOpsError(f"Folder induk tidak ditemukan: {parent_path}")
        clean = _validate_name(name)
        target = parent_path / clean
        if target.exists():
            raise FileOpsError(f"Folder sudah ada: {clean}")
        try:
            target.mkdir(parents=False, exist_ok=False)
        except OSError as exc:
            raise FileOpsError(f"Gagal membuat folder: {exc}") from exc
        return {"name": clean, "path": str(target), "parent": str(parent_path)}

    # -- copy / move ---------------------------------------------------
    def copy(self, paths: Iterable, target_dir) -> dict:
        """Copy ``paths`` into ``target_dir`` (originals kept)."""
        return self._transfer(paths, target_dir, move=False)

    def move(self, paths: Iterable, target_dir) -> dict:
        """Move ``paths`` into ``target_dir`` (originals removed)."""
        return self._transfer(paths, target_dir, move=True)

    def _transfer(self, paths: Iterable, target_dir, *, move: bool) -> dict:
        destination = self.resolve(target_dir)
        # The target folder may not exist yet: create it (and every missing
        # parent) instead of failing, so *Copy/Move to Folder...* works for
        # nested paths such as ``D:\\data\\img\\new`` at any depth.
        if destination.exists() and not destination.is_dir():
            raise FileOpsError(f"Tujuan bukan sebuah folder: {destination}")
        try:
            destination.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FileOpsError(f"Gagal membuat folder tujuan: {exc}") from exc

        sources: List[Path] = []
        for raw in (paths or []):
            sources.append(self.resolve(raw))
        if not sources:
            raise FileOpsError("Tidak ada file yang dipilih.")

        counter = "moved" if move else "copied"
        result = {counter: 0, "failed": 0, "errors": [], "items": []}
        for source in sources:
            try:
                if not source.is_file():
                    raise FileOpsError(f"File tidak ditemukan: {source}")
                if destination == source.parent:
                    raise FileOpsError(
                        f"File sudah berada di folder tujuan: {source.name}"
                    )
                target = destination / source.name
                if target.exists():
                    raise FileOpsError(f"Target sudah ada: {source.name}")
                if move:
                    shutil.move(str(source), str(target))
                else:
                    shutil.copy2(str(source), str(target))
                result[counter] += 1
                result["items"].append({"source": str(source), "target": str(target)})
            except FileOpsError as exc:
                result["failed"] += 1
                result["errors"].append({"path": str(source), "error": str(exc)})
            except OSError as exc:
                result["failed"] += 1
                result["errors"].append({"path": str(source), "error": str(exc)})
        return result

    # -- rename --------------------------------------------------------
    def rename(self, path, new_name: str) -> dict:
        """Rename a single file.

        When ``new_name`` has no extension the original one is preserved, so
        renaming ``photo.png`` to ``holiday`` yields ``holiday.png``.
        """
        source = self.resolve(path)
        if not source.is_file():
            raise FileOpsError(f"File tidak ditemukan: {source}")
        clean = _validate_name(new_name)
        if "." not in clean and source.suffix:
            clean = clean + source.suffix
        target = source.with_name(clean)
        if target == source:
            return {
                "old_path": str(source),
                "path": str(source),
                "name": source.name,
                "unchanged": True,
            }
        if target.exists():
            raise FileOpsError(f"File tujuan sudah ada: {clean}")
        try:
            source.rename(target)
        except OSError as exc:
            raise FileOpsError(f"Gagal rename: {exc}") from exc
        return {
            "old_path": str(source),
            "path": str(target),
            "name": target.name,
            "unchanged": False,
        }

    # -- batch rename --------------------------------------------------
    def batch_rename(
        self,
        paths: Iterable,
        *,
        prefix: str = "",
        suffix: str = "",
        numbering: bool = True,
        start: int = 1,
        padding: int = 0,
    ) -> dict:
        """Rename many files with a prefix/suffix and an optional sequence.

        Rules (mirrors the UI preview and the ``/api/files/batch-rename`` route):

        * the original extension is always preserved;
        * the new base name is ``prefix + <sequence> + suffix`` where the
          sequence runs upward from ``start`` and is zero-padded to ``padding``
          digits; the sequence is omitted entirely when ``numbering`` is false;
        * a new name that would overwrite an existing file - or that duplicates
          another name inside the same batch - is refused, so files are never
          silently overwritten;
        * renames are applied in two phases through unique temporary names, so a
          batch may safely swap names (``a.png`` -> ``b.png``, ``b.png`` ->
          ``a.png``) without clobbering either file.

        Returns a summary ``{"renamed", "unchanged", "failed", "items"}`` with a
        per-file ``status`` (``renamed``/``unchanged``/``failed``) and the
        original + new path, so the caller can report partial success and
        reconcile group membership.
        """
        prefix = prefix or ""
        suffix = suffix or ""
        try:
            start = int(start)
            padding = int(padding)
        except (TypeError, ValueError) as exc:
            raise FileOpsError("Nomor awal/padding tidak valid.") from exc
        if start < 0 or padding < 0 or padding > 20:
            raise FileOpsError("Nomor awal/padding tidak valid.")

        raw_paths = list(paths or [])
        if not raw_paths:
            raise FileOpsError("Tidak ada file yang dipilih.")

        items: List[dict] = []
        pending: List[dict] = []
        seq_index = 0
        for raw in raw_paths:
            try:
                source = self.resolve(raw)
            except FileOpsError as exc:
                items.append({
                    "path": str(raw), "name": Path(str(raw)).name,
                    "status": "failed", "error": str(exc),
                })
                continue
            if not source.is_file():
                items.append({
                    "path": str(source), "name": source.name,
                    "status": "failed", "error": "File tidak ditemukan",
                })
                continue

            ext = source.suffix
            seq = ""
            if numbering:
                seq = str(start + seq_index)
                if padding > 0:
                    seq = seq.zfill(padding)
            try:
                clean = _validate_name(prefix + seq + suffix)
            except FileOpsError as exc:
                items.append({
                    "path": str(source), "name": source.name,
                    "status": "failed", "error": str(exc),
                })
                seq_index += 1
                continue

            new_name = clean + ext
            item = {
                "path": str(source),
                "name": source.name,
                "new_name": new_name,
                "new_path": str(source.with_name(new_name)),
                "status": "pending",
            }
            pending.append(item)
            items.append(item)
            seq_index += 1

        # -- collision detection (never overwrite) -------------------------
        seen = {}
        for item in pending:
            key = item["new_path"].lower()
            if key in seen:
                item["status"] = "failed"
                item["error"] = "Nama baru bentrok dengan file lain pada batch ini"
                continue
            seen[key] = item
            source = self.resolve(item["path"])
            target = Path(item["new_path"])
            if str(source) == str(target):
                item["status"] = "unchanged"
                continue
            if target.exists():
                item["status"] = "failed"
                item["error"] = f"File tujuan sudah ada: {target.name}"

        to_rename = [item for item in pending if item["status"] == "pending"]

        # -- phase 1: move every source to a unique temporary name ---------
        temp_pairs = []
        for item in to_rename:
            source = self.resolve(item["path"])
            temp_name = ".veyra~tmp-%s-%d" % (uuid.uuid4().hex, len(temp_pairs))
            try:
                moved = self.rename(source, temp_name)
            except FileOpsError as exc:
                item["status"] = "failed"
                item["error"] = str(exc)
                continue
            temp_pairs.append((item, moved["path"]))

        # -- phase 2: move each temporary name to its final target ---------
        for item, temp_path in temp_pairs:
            try:
                final = self.rename(temp_path, item["new_name"])
            except FileOpsError as exc:
                item["status"] = "failed"
                item["error"] = str(exc)
                try:
                    self.rename(temp_path, item["name"])  # best-effort restore
                except FileOpsError:
                    pass
                continue
            item["new_path"] = final["path"]
            item["new_name"] = final["name"]
            item["status"] = "unchanged" if final.get("unchanged") else "renamed"

        summary = {"renamed": 0, "unchanged": 0, "failed": 0, "items": items}
        for item in items:
            status = item.get("status")
            if status == "renamed":
                summary["renamed"] += 1
            elif status == "unchanged":
                summary["unchanged"] += 1
            else:
                summary["failed"] += 1
        return summary

    # -- delete --------------------------------------------------------
    def delete(self, path: str) -> dict:
        """Delete a single file from the filesystem.

        The operation always succeeds unless the file is missing or protected
        (read-only, in use, or permission denied).
        """
        target = self.resolve(path)
        if not target.is_file():
            raise FileOpsError(f"File tidak ditemukan: {target}")
        if not target.exists():
            # File vanished between resolution and deletion
            return {"deleted": False, "path": str(target), "reason": "not_found"}

        try:
            target.unlink()
        except PermissionError as exc:
            raise FileDeleteError(f"Permission denied: {exc}") from exc
        except OSError as exc:
            if exc.winerror == 32:
                # Windows error code: file is in use
                raise FileDeleteError(f"File sedang digunakan: {exc}") from exc
            raise FileDeleteError(f"Gagal menghapus file: {exc}") from exc

        return {
            "deleted": True,
            "path": str(target),
            "name": target.name,
        }

    # -- delete multiple -----------------------------------------------
    def delete_multiple(self, paths: Iterable) -> dict:
        """Delete multiple files in a single batch.

        Returns a summary with per-file results, allowing the UI to show
        partial success for mixed failures (e.g. one file busy, one not found).
        """
        result = {
            "deleted": 0,
            "failed": 0,
            "errors": [],
            "items": [],
        }

        for raw in (paths or []):
            target = self.resolve(raw)
            if not target.is_file():
                result["failed"] += 1
                result["errors"].append({"path": str(raw), "error": "File tidak ditemukan"})
                result["items"].append({"path": str(raw), "deleted": False, "reason": "not_found"})
                continue

            try:
                target.unlink()
                result["deleted"] += 1
                result["items"].append({"path": str(target), "deleted": True, "name": target.name})
            except PermissionError as exc:
                result["failed"] += 1
                result["errors"].append({"path": str(raw), "error": f"Permission denied: {exc}"})
                result["items"].append({
                    "path": str(raw),
                    "deleted": False,
                    "error": f"Permission denied: {exc}"
                })
            except OSError as exc:
                if exc.winerror == 32:
                    reason = "File sedang digunakan"
                else:
                    reason = f"Gagal menghapus: {exc}"
                result["failed"] += 1
                result["errors"].append({"path": str(raw), "error": reason})
                result["items"].append({
                    "path": str(raw),
                    "deleted": False,
                    "error": reason
                })

        return result