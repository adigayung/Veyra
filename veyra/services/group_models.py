"""Database ORM models for File and Group collections using Betrayer ORM.

This module declares **only** models built on the Betrayer Data Layer
(``betrayer.data.ORMModel`` / ``ORMField`` / ``ManyToMany``).  It introduces no
database abstraction of its own and no raw SQL: every table and column is a
declarative Betrayer model, and the physical storage engine is whatever the
Betrayer ``DatabaseRegistry`` resolves from configuration (SQLite here, purely
as the storage engine behind the Betrayer contract).

Identity
--------
A :class:`File` row is keyed by a *stable identity* (``stable_id``) derived from
the file content, not from its path.  That keeps group membership valid across
rename/move as long as the content (and therefore the identity) is preserved.

Relationships
-------------
``File`` <-> ``Group`` is a many-to-many relationship through the
``group_members`` pivot (also declared as the :class:`GroupMember` model so the
pivot can be managed through the same Betrayer Query Builder).  A single file
can belong to many groups and a group can contain files from any folder,
including ``.aimg`` containers.

No secret material (password, master key, decrypted bytes) is ever modelled or
stored here: the only file-related values are metadata + a content hash.
"""

from __future__ import annotations

from betrayer.data.orm import ORMModel, ORMField
from betrayer.data.relationships import ManyToMany

__all__ = ["File", "Group", "GroupMember", "PIVOT_TABLE", "configure_models"]

#: Pivot table backing the File <-> Group many-to-many relationship.
PIVOT_TABLE = "group_members"


class Group(ORMModel):
    """A user-defined collection of files.

    ``id`` is the auto-increment primary key; ``name`` is the display name.
    ``created``/``updated`` are ISO-8601 timestamps stored as text.
    """

    __table__ = "groups"
    #: Bound to the Betrayer DatabaseManager by :func:`configure_models`.
    __connection__ = None

    id = ORMField(int, primary_key=True)
    name = ORMField(str, required=True)
    created = ORMField(str)
    updated = ORMField(str)

    files = ManyToMany(
        "File",
        pivot=PIVOT_TABLE,
        pivot_local_key="group_id",
        pivot_foreign_key="file_id",
        description="Files in this group (many-to-many via group_members).",
    )


class File(ORMModel):
    """Metadata record for a file indexed by Veyra.

    Fields correspond to stable identity and basic attributes required for
    grouping and UI display.  The record deliberately contains **no** content,
    no password and no key material — only metadata plus a content hash.
    """

    __table__ = "files"
    #: Bound to the Betrayer DatabaseManager by :func:`configure_models`.
    __connection__ = None

    id = ORMField(int, primary_key=True)
    stable_id = ORMField(
        str,
        required=True,
        unique=True,
        description="Content identity (hash); survives rename/move.",
    )
    path = ORMField(str, required=True)
    name = ORMField(str)
    extension = ORMField(str)
    size = ORMField(int)
    modified = ORMField(str)
    is_aimg = ORMField(bool)

    groups = ManyToMany(
        "Group",
        pivot=PIVOT_TABLE,
        pivot_local_key="file_id",
        pivot_foreign_key="group_id",
        description="Groups this file belongs to (many-to-many via group_members).",
    )


class GroupMember(ORMModel):
    """Pivot row linking a :class:`Group` to a :class:`File`.

    Declared as a first-class Betrayer model so membership rows are created and
    removed through the same Query Builder (``Query("group_members", ...)``)
    instead of hand-written SQL.
    """

    __table__ = PIVOT_TABLE
    #: Bound to the Betrayer DatabaseManager by :func:`configure_models`.
    __connection__ = None

    id = ORMField(int, primary_key=True)
    group_id = ORMField(int, required=True)
    file_id = ORMField(int, required=True)


def configure_models(manager) -> None:
    """Bind ``manager`` as the Betrayer ORM connection for every model here.

    The Betrayer ORM resolves its connection from ``Model.__connection__``; this
    helper sets it to a :class:`betrayer.data.database.DatabaseManager` so the
    models can query/mutate through the Betrayer database subsystem.
    """
    for model in (File, Group, GroupMember):
        model.__connection__ = manager
