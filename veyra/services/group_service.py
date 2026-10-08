"""Veyra Group service - collections of files backed by the Betrayer database.

The service is a thin layer of *business logic* on top of the Betrayer Data
Layer.  It owns no SQL and no database driver: every read/write goes through the
Betrayer ORM (``File``/``Group``/``GroupMember`` models) and the Betrayer Query
Builder, using the connected :class:`betrayer.data.database.DatabaseManager`.

Guarantees (all covered by ``verify_groups.py``):

* a group and its membership live in the database, not in memory;
* one file can belong to many groups; one group can hold files from many
  folders (membership is by stable identity, not by folder);
* adding the same file to the same group twice never creates a duplicate row;
* deleting a group or removing a membership only touches database rows - the
  physical files are never deleted;
* file identity is content based, so membership survives rename/move as long as
  the content (identity) is preserved; :meth:`GroupService.reconcile` re-links
  the stored path to the file's new location;
* ``.aimg`` containers are first-class members (only their metadata/hash is
  read, never a password/key/plaintext).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from betrayer.data.query import Query

from veyra.services.group_models import (
    PIVOT_TABLE,
    File,
    Group,
    GroupMember,
    configure_models,
)

__all__ = ["GroupService", "GroupError", "compute_stable_id"]

#: Read size used when hashing a file's content for its stable identity.
_HASH_CHUNK = 1024 * 1024


class GroupError(Exception):
    """Raised for invalid group/membership operations (never for missing files)."""


def compute_stable_id(path) -> str:
    """Return the content identity of ``path`` (``sha256:<hex>``).

    The identity is derived from the file *content*, so it stays the same after
    a rename/move (the requirement's "identity dapat dipertahankan").  For an
    ``.aimg`` container the hashed bytes are the encrypted container bytes - the
    payload is never decrypted.
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GroupService:
    """Create/rename/delete groups and manage their file membership."""

    def __init__(self, manager, image_service=None) -> None:
        #: Connected Betrayer DatabaseManager (the only database entry point).
        self._manager = manager
        #: Optional ImageService (kept for callers that want metadata reuse).
        self._images = image_service
        # Bind the ORM models' __connection__ to this manager so model queries
        # and the many-to-many relationship resolve against it.
        configure_models(manager)

    # ── internal helpers ──────────────────────────────────────────────

    def _commit(self) -> None:
        connection = self._manager.connection
        if connection is not None:
            connection.commit()

    def _pivot(self) -> Query:
        """A Betrayer Query bound to the membership pivot table."""
        return Query(PIVOT_TABLE, database=self._manager)

    # ── files ─────────────────────────────────────────────────────────

    def register_file(self, path) -> File:
        """Insert (or refresh) the ``File`` row for ``path`` and return it.

        Identity is content based, so an already-known file (same content) is
        updated in place - preserving its ``id`` and therefore its memberships -
        even when it was renamed/moved.
        """
        target = Path(path)
        if not target.is_file():
            raise GroupError(f"File tidak ditemukan: {target}")

        stable_id = compute_stable_id(target)
        stat = target.stat()
        name = target.name
        extension = target.suffix.lower().lstrip(".")
        is_aimg = target.suffix.lower() == ".aimg"
        modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

        existing = File.query().where("stable_id", stable_id).first()
        if existing is not None:
            existing.path = str(target)
            existing.name = name
            existing.extension = extension
            existing.size = int(stat.st_size)
            existing.modified = modified
            existing.is_aimg = is_aimg
            existing.save()
            self._commit()
            return existing

        record = File.create(
            stable_id=stable_id,
            path=str(target),
            name=name,
            extension=extension,
            size=int(stat.st_size),
            modified=modified,
            is_aimg=is_aimg,
        )
        self._commit()
        return record

    def get_file(self, file_id: int) -> Optional[File]:
        """Return the ``File`` row for ``file_id`` (or ``None``)."""
        return File.query().where("id", file_id).first()

    def find_file_by_path(self, path) -> Optional[File]:
        """Return the ``File`` row whose identity matches the file at ``path``."""
        target = Path(path)
        if not target.is_file():
            return None
        return File.query().where("stable_id", compute_stable_id(target)).first()

    # ── groups (CRUD) ─────────────────────────────────────────────────

    def create_group(self, name: str) -> dict:
        """Create a new group and return its serialised form."""
        cleaned = (name or "").strip()
        if not cleaned:
            raise GroupError("Nama group wajib diisi.")
        timestamp = _now()
        group = Group.create(name=cleaned, created=timestamp, updated=timestamp)
        self._commit()
        return self._group_dict(group)

    def rename_group(self, group_id: int, name: str) -> dict:
        """Rename an existing group."""
        cleaned = (name or "").strip()
        if not cleaned:
            raise GroupError("Nama group wajib diisi.")
        group = self.get_group(group_id)
        if group is None:
            raise GroupError(f"Group {group_id} tidak ditemukan.")
        group.name = cleaned
        group.updated = _now()
        group.save()
        self._commit()
        return self._group_dict(group)

    def delete_group(self, group_id: int) -> dict:
        """Delete a group and its membership rows.

        The physical files are **never** touched - only the group row and its
        pivot rows are removed.
        """
        group = self.get_group(group_id)
        if group is None:
            raise GroupError(f"Group {group_id} tidak ditemukan.")
        removed = self._pivot().where("group_id", group_id).delete()
        group.delete()
        self._commit()
        return {
            "id": group_id,
            "memberships_removed": int((removed or {}).get("affected", 0) or 0),
        }

    def get_group(self, group_id: int) -> Optional[Group]:
        """Return the ``Group`` row for ``group_id`` (or ``None``)."""
        return Group.query().where("id", group_id).first()

    def list_groups(self) -> List[dict]:
        """Return every group as a serialised list (with member counts)."""
        groups = Group.query().order_by("name", "asc").get()
        return [self._group_dict(group) for group in groups]

    # ── membership ────────────────────────────────────────────────────

    def add_member(self, group_id: int, path) -> dict:
        """Add the file at ``path`` to ``group_id`` (idempotent).

        Registers the file (by identity), refuses to create a duplicate
        membership row, and never modifies the file on disk.
        """
        group = self.get_group(group_id)
        if group is None:
            raise GroupError(f"Group {group_id} tidak ditemukan.")
        file = self.register_file(path)

        already = (
            self._pivot()
            .where("group_id", group_id)
            .where("file_id", file.id)
            .exists()
        )
        if not already:
            self._pivot().insert({"group_id": group_id, "file_id": file.id})
            group.updated = _now()
            group.save()
            self._commit()
        return {
            "group_id": group_id,
            "file": self._file_dict(file),
            "added": not already,
            "duplicate": already,
        }

    def remove_member(
        self,
        group_id: int,
        *,
        path=None,
        file_id: Optional[int] = None,
    ) -> dict:
        """Remove a membership row (never the physical file).

        Resolve the file either by ``file_id`` or by the identity of ``path``.
        """
        group = self.get_group(group_id)
        if group is None:
            raise GroupError(f"Group {group_id} tidak ditemukan.")

        resolved_id = file_id
        if resolved_id is None:
            if path is None:
                raise GroupError("Butuh 'path' atau 'file_id'.")
            record = self.find_file_by_path(path)
            if record is None:
                raise GroupError(f"File tidak dikenal: {path}")
            resolved_id = record.id

        result = (
            self._pivot()
            .where("group_id", group_id)
            .where("file_id", resolved_id)
            .delete()
        )
        group.updated = _now()
        group.save()
        self._commit()
        return {
            "group_id": group_id,
            "file_id": resolved_id,
            "removed": int((result or {}).get("affected", 0) or 0),
        }

    def group_members(self, group_id: int) -> Optional[dict]:
        """Return a group plus its member files (``None`` when missing)."""
        group = self.get_group(group_id)
        if group is None:
            return None
        members = group.files().order_by("name", "asc").get()  # many-to-many
        return {
            **self._group_dict(group),
            "members": [self._file_dict(member) for member in members],
        }

    def groups_for_file(self, file_id: int) -> List[dict]:
        """Return every group that contains ``file_id`` (via the M2M link)."""
        file = self.get_file(file_id)
        if file is None:
            return []
        groups = file.groups().order_by("name", "asc").get()  # many-to-many
        return [self._group_dict(group) for group in groups]

    def groups_for_path(self, path) -> List[dict]:
        """Return every group that contains the file at ``path``."""
        record = self.find_file_by_path(path)
        if record is None:
            return []
        return self.groups_for_file(record.id)

    def remove_file_path(self, path) -> dict:
        """Remove stale registration and memberships after physical deletion."""
        record = File.query().where("path", str(path)).first()
        if record is None:
            return {"removed": 0}
        removed = self._pivot().where("file_id", record.id).delete()
        record.delete()
        self._commit()
        return {"removed": int((removed or {}).get("affected", 0) or 0)}

    def reconcile(self, old_path, new_path) -> dict:
        """Re-link a renamed/moved file to its new path, keeping membership.

        Membership is stored against the file's ``id`` (identity), so this only
        refreshes the stored ``path``/metadata; the pivot rows are untouched.
        When no matching identity exists yet the file is registered fresh.
        """
        target = Path(new_path)
        if not target.is_file():
            raise GroupError(f"File tujuan tidak ditemukan: {target}")

        new_id = compute_stable_id(target)
        record = File.query().where("stable_id", new_id).first()

        old = Path(old_path)
        if record is None and old.is_file():
            record = File.query().where("stable_id", compute_stable_id(old)).first()

        if record is None:
            record = self.register_file(target)
            return {"file": self._file_dict(record), "relinked": False, "registered": True}

        stat = target.stat()
        record.path = str(target)
        record.name = target.name
        record.extension = target.suffix.lower().lstrip(".")
        record.size = int(stat.st_size)
        record.modified = datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat()
        record.is_aimg = target.suffix.lower() == ".aimg"
        record.save()
        self._commit()
        return {
            "file": self._file_dict(record),
            "relinked": True,
            "registered": False,
            "groups": self.groups_for_file(record.id),
        }

    # ── serialisation ─────────────────────────────────────────────────

    def _group_dict(self, group: Group) -> dict:
        return {
            "id": group.id,
            "name": group.name,
            "created": group.created,
            "updated": group.updated,
            "member_count": int(group.files().count()),
        }

    @staticmethod
    def _file_dict(file: File) -> dict:
        return {
            "id": file.id,
            "stable_id": file.stable_id,
            "path": file.path,
            "name": file.name,
            "extension": file.extension,
            "size": file.size,
            "modified": file.modified,
            "is_aimg": bool(file.is_aimg),
        }
