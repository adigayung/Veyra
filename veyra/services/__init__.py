"""Veyra services package."""

from __future__ import annotations

from veyra.security import (
    CryptoError,
    CryptoService,
    LockedError,
    WrongPasswordError,
)
from veyra.services.image_service import ImageService, FileService, ImageInfo
from veyra.services.aimg_service import (
    AimgService,
    AimgError,
    AimgAuthError,
    AimgMigrationRequired,
    encrypt_file,
    decrypt_file,
    read_payload,
    read_thumbnail,
    inspect_metadata,
    validate_aimg,
    migrate_file,
    migrate_folder,
    encrypt_folder_recursive,
    decrypt_folder_recursive,
)
from veyra.services.group_models import (
    File,
    Group,
    GroupMember,
    PIVOT_TABLE,
    configure_models,
)
from veyra.services.group_service import (
    GroupService,
    GroupError,
    compute_stable_id,
)
from veyra.services.file_ops import (
    FileOpsService,
    FileOpsError,
)
from veyra.services.database import (
    DATABASE_DRIVER,
    DATABASE_NAME,
    DATABASE_SERVICE,
    DB_ENV,
    build_config,
    build_manager,
    default_database_path,
    ensure_schema,
    install_database_for_app,
)

__all__ = [
    # images
    "ImageService",
    "FileService",
    "ImageInfo",
    # crypto session
    "CryptoService",
    "CryptoError",
    "LockedError",
    "WrongPasswordError",
    # aimg
    "AimgService",
    "AimgError",
    "AimgAuthError",
    "AimgMigrationRequired",
    "encrypt_file",
    "decrypt_file",
    "read_payload",
    "read_thumbnail",
    "inspect_metadata",
    "validate_aimg",
    "migrate_file",
    "migrate_folder",
    "encrypt_folder_recursive",
    "decrypt_folder_recursive",
    # groups / database (Betrayer data layer)
    "File",
    "Group",
    "GroupMember",
    "PIVOT_TABLE",
    "configure_models",
    "GroupService",
    "GroupError",
    "compute_stable_id",
    # file operations (context menu)
    "FileOpsService",
    "FileOpsError",
    "DATABASE_DRIVER",
    "DATABASE_NAME",
    "DATABASE_SERVICE",
    "DB_ENV",
    "build_config",
    "build_manager",
    "default_database_path",
    "ensure_schema",
    "install_database_for_app",
]
