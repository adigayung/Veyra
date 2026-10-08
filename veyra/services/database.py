"""Betrayer database wiring for Veyra.

Veyra uses the **Betrayer Data Layer** as its only database stack.  This module
contains no new database abstraction: it merely *composes* the existing Betrayer
classes so the application gets a connected, schema-ready database.

What it reuses (all from ``betrayer.data``):

* :class:`betrayer.core.config.Config` — holds the ``database.default.*`` block.
* :func:`betrayer.data.bootstrap.build_database_registry` — resolves the driver
  (``sqlite``) through :class:`betrayer.data.registry.DatabaseRegistry`.
* :func:`betrayer.data.bootstrap.build_database_manager` — produces the
  lifecycle-aware :class:`betrayer.data.database.DatabaseManager`.
* :func:`betrayer.data.bootstrap.install_database` — registers the connected
  manager under the canonical ``"database"`` service key on the application.
* :func:`betrayer.data.migration.create_table_sql` — derives DDL from the ORM
  models + dialect, so migrations never hand-write SQL.

SQLite is therefore only the **storage engine** selected by configuration; the
rest of the code sees only the Betrayer contracts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from betrayer.core.config import Config
from betrayer.data.bootstrap import (
    DATABASE_SERVICE,
    build_database_manager,
    build_database_registry,
    install_database,
)
from betrayer.data.database import DatabaseManager
from betrayer.data.migration import create_table_sql

from veyra.services.group_models import File, Group, GroupMember, configure_models

__all__ = [
    "DATABASE_NAME",
    "DATABASE_DRIVER",
    "DATABASE_SERVICE",
    "DB_ENV",
    "default_database_path",
    "build_config",
    "build_manager",
    "install_database_for_app",
    "ensure_schema",
]

#: Connection name inside the Betrayer ``database.<name>.*`` configuration.
DATABASE_NAME = "default"

#: Driver identifier resolved through the Betrayer registry (storage engine).
DATABASE_DRIVER = "sqlite"

#: Environment override for the SQLite file (used by tests to stay isolated).
DB_ENV = "VEYRA_DB"

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Models that make up the Veyra schema (tables created via Betrayer DDL).
_SCHEMA_MODELS = (Group, File, GroupMember)

#: Unique indexes enforced at the storage engine level (defence in depth on top
#: of the service-level checks): file identity is unique, membership is unique.
_UNIQUE_INDEXES = (
    ("ux_files_stable_id", "files", ("stable_id",)),
    ("ux_group_members", "group_members", ("group_id", "file_id")),
)


def default_database_path() -> Path:
    """Return the SQLite file Veyra uses (``.veyra/veyra.db`` by default)."""
    override = os.environ.get(DB_ENV)
    if override:
        return Path(override)
    return PROJECT_ROOT / ".veyra" / "veyra.db"


def build_config(db_path: Optional[Path] = None) -> Config:
    """Build the Betrayer :class:`Config` describing the ``default`` database."""
    path = Path(db_path) if db_path is not None else default_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return Config(defaults={
        "app.name": "veyra",
        f"database.{DATABASE_NAME}.driver": DATABASE_DRIVER,
        f"database.{DATABASE_NAME}.database": str(path),
        f"database.{DATABASE_NAME}.timeout": "10",
    })


def build_manager(db_path: Optional[Path] = None, *, connect: bool = True) -> DatabaseManager:
    """Build a connected Betrayer :class:`DatabaseManager` for Veyra.

    Used both by the application bootstrap and by the verification suite (which
    passes a temporary path so it never touches the developer's real database).
    """
    config = build_config(db_path)
    registry = build_database_registry(config)
    manager = build_database_manager(
        config, DATABASE_NAME, registry=registry, connect=connect
    )
    configure_models(manager)
    return manager


def install_database_for_app(application, db_path: Optional[Path] = None) -> DatabaseManager:
    """Install the connected Betrayer database on a ``BetrayerApplication``.

    Registers the manager under the canonical ``"database"`` container key and
    the registry under ``"database.registry"``, binds the ORM models and makes
    sure the schema exists.
    """
    config = build_config(db_path)
    manager = install_database(application, name=DATABASE_NAME, config=config)
    configure_models(manager)
    ensure_schema(manager)
    return manager


def ensure_schema(manager: DatabaseManager) -> None:
    """Create the Veyra tables + unique indexes on ``manager`` (idempotent).

    DDL is produced by Betrayer's :func:`create_table_sql` from the ORM models
    and the engine dialect; the unique indexes go through the same
    ``DatabaseManager.execute`` seam the migration layer uses.
    """
    dialect = manager.dialect
    for model in _SCHEMA_MODELS:
        manager.execute(create_table_sql(model, dialect))
    quote = dialect.quote_identifier
    for name, table, columns in _UNIQUE_INDEXES:
        cols = ", ".join(quote(col) for col in columns)
        manager.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {quote(name)} "
            f"ON {quote(table)} ({cols})"
        )
    _commit(manager)


def _commit(manager: DatabaseManager) -> None:
    """Commit the current transaction on the manager's connection."""
    connection = manager.connection
    if connection is not None:
        connection.commit()
