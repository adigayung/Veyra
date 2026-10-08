"""Veyra Web Layer - HTTP routes and view handlers.

All HTTP endpoints used by the single page UI live here.  The handlers return
:class:`betrayer.web.response.Response` objects and never raise on invalid
input: an unreadable or missing folder is reported as a normal JSON payload
with ``ok: false`` (HTTP 200) so the browser can display a clear error without
the server ever answering with a 500.

Crypto boundary
---------------
The ``/api/aimg/*`` endpoints and the ``.aimg`` branches of ``/api/image`` and
``/api/thumb`` do **not** trust any request flag: they ask the
:class:`~veyra.security.crypto_session.CryptoService` whether the session is
really unlocked (native DPAPI ticket + master key verifier) before touching
encrypted bytes.  When it is locked the requests are refused with HTTP 403.
"""

from __future__ import annotations

import os
import string
from pathlib import Path

from betrayer.application import BetrayerApplication
from betrayer.web.adapter import FlaskAdapter
from betrayer.web.routing import WebRouter
from betrayer.web.response import Response

from veyra.security.crypto_session import (
    CryptoError,
    CryptoService,
    LockedError,
    WrongPasswordError,
)
from veyra.services import (
    AimgError,
    AimgService,
    FileOpsError,
    FileOpsService,
    GroupError,
    GroupService,
    ImageService,
)

from veyra.services.image_service import AIMG_EXTENSION as _AIMG_EXTENSION


#: Project root (e.g. ``J:\Veyra``) resolved from this file's location:
#: ``<root>/veyra/web/routes.py`` -> parents[0]=web, [1]=veyra, [2]=<root>.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Application package directory (``J:\Veyra\veyra``).  The single page UI ships
#: inside the package as ``veyra/index.html`` instead of the project root.
PACKAGE_ROOT = Path(__file__).resolve().parents[1]

#: Folder opened when the viewer starts.  Falls back to the project root.
DEFAULT_BROWSE_PATH = r"J:\Program AndroidX\b\ai\StableDiffusion\face\knl\Revi Lia"

#: Optional environment overrides (used by the automated verification suite so
#: it never touches the developer's real key store / folders).
_KEYSTORE_ENV = "VEYRA_KEYSTORE"
_DEFAULT_PATH_ENV = "VEYRA_DEFAULT_PATH"


def _keystore_path() -> Path:
    override = os.environ.get(_KEYSTORE_ENV)
    if override:
        return Path(override)
    return PROJECT_ROOT / ".veyra" / "keystore.bin"


def _default_path() -> Path:
    """Return the folder the viewer opens on startup."""
    override = os.environ.get(_DEFAULT_PATH_ENV)
    if override and Path(override).is_dir():
        return Path(override)
    candidate = Path(DEFAULT_BROWSE_PATH)
    if candidate.is_dir():
        return candidate
    return PROJECT_ROOT


def _tree_root(default_path: Path) -> Path:
    """Folder the sidebar tree is rooted at (parent of the default folder)."""
    parent = default_path.parent
    if parent.is_dir() and parent != default_path:
        return parent
    return default_path


def _list_drives() -> list:
    """Return the available filesystem drive roots (``C:\\`` .. ``Z:\\``).

    The explorer tree is rooted at "This PC" and lists every drive the machine
    exposes, exactly like Windows Explorer -- it is **not** limited to the
    project/workspace folder.  On Windows the logical drive bitmask is queried
    (fast, does not touch empty removable drives); elsewhere the single
    filesystem root is returned.
    """
    roots: list[str] = []
    if os.name == "nt":
        try:
            import ctypes

            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            roots = [
                letter + ":\\"
                for index, letter in enumerate(string.ascii_uppercase)
                if bitmask & (1 << index)
            ]
        except Exception:  # pragma: no cover - defensive fallback
            roots = [
                letter + ":\\"
                for letter in string.ascii_uppercase
                if Path(letter + ":\\").exists()
            ]
    if not roots:
        roots = ["/"]

    drives = []
    for root in roots:
        name = root[:2] if os.name == "nt" else root
        drives.append({"name": name, "path": root, "has_children": True})
    return drives


def _error_browse(path: str, error: str, tree_root: Path | None = None) -> dict:
    """Empty browse payload describing why a folder could not be opened."""
    return {
        "ok": False,
        "path": path,
        "images": [],
        "directories": [],
        "tree_root": str(tree_root) if tree_root else None,
        "error": error,
    }


def _is_aimg(path: str) -> bool:
    return Path(path).suffix.lower() == _AIMG_EXTENSION


def _image_headers(mime_type: str) -> dict:
    return {
        "Content-Type": mime_type,
        "Cache-Control": "no-cache",
        "X-Content-Type-Options": "nosniff",
    }


def _resolve_database(app: BetrayerApplication):
    """Return the connected Betrayer database manager for the application.

    When the application container already holds the ``"database"`` service
    (installed by ``create_app``), that manager is reused.  Otherwise a manager
    is built directly through the Betrayer data bootstrap so the router stays
    usable on its own (e.g. focused tests).
    """
    container = getattr(app, "container", None)
    if container is not None and container.has("database"):
        return container.resolve("database")
    from veyra.services.database import build_manager, ensure_schema

    manager = build_manager()
    ensure_schema(manager)
    return manager


def create_router(app: BetrayerApplication) -> WebRouter:
    """Create the web router with all Veyra routes."""
    router = WebRouter(name="veyra.web")

    image_service = ImageService(root_path=PROJECT_ROOT)
    crypto_service = CryptoService(_keystore_path())
    aimg_service = AimgService(crypto_service)
    group_service = GroupService(_resolve_database(app), image_service)
    file_ops = FileOpsService()

    app.registry.register("veyra.image_service", image_service)
    app.registry.register("veyra.crypto_service", crypto_service)
    app.registry.register("veyra.aimg_service", aimg_service)
    app.registry.register("veyra.group_service", group_service)
    app.registry.register("veyra.file_ops", file_ops)

    @router.get("/")
    def index_handler(request, context) -> Response:
        """Serve the Veyra UI from its location inside the package."""
        index_path = PACKAGE_ROOT / "index.html"
        if not index_path.is_file():
            return Response.text(f"index.html not found at {index_path}", status=404)
        try:
            content = index_path.read_text(encoding="utf-8")
        except OSError as exc:
            return Response.text(f"Error reading index.html: {exc}", status=500)
        return Response.html(content)

    @router.get("/api/health")
    def health_handler(request, context) -> Response:
        """Health check endpoint."""
        return Response.json({
            "status": "healthy",
            "service": "veyra",
            "version": "0.1.0",
        })

    @router.get("/api/config")
    def config_handler(request, context) -> Response:
        """Expose the startup folder, supported extensions and crypto status."""
        default_path = _default_path()
        return Response.json({
            "ok": True,
            "default_path": str(default_path),
            "tree_root": str(_tree_root(default_path)),
            "extensions": sorted(image_service.SUPPORTED_EXTENSIONS),
            "crypto": aimg_service.status(),
        })

    @router.get("/api/drives")
    def drives_handler(request, context) -> Response:
        """List the machine's drive roots for the "This PC" tree node."""
        return Response.json({"ok": True, "drives": _list_drives()})

    @router.get("/api/browse")
    def browse_handler(request, context) -> Response:
        """List the images (and ``.aimg`` containers) plus sub-folders."""
        raw_path = request.query.get("path")
        default_path = _default_path()
        tree_root = _tree_root(default_path)

        if raw_path:
            target = image_service.resolve_path(raw_path)
            if target is None:
                return Response.json(_error_browse(
                    str(raw_path), "Path tidak valid.", tree_root))
        else:
            target = default_path

        if not target.exists():
            return Response.json(_error_browse(
                str(target), f"Folder tidak ditemukan: {target}", tree_root))
        if not target.is_dir():
            return Response.json(_error_browse(
                str(target), f"Bukan sebuah folder: {target}", tree_root))

        try:
            images = image_service.list_images(target)
            directories = image_service.list_subdirectories(target)
        except OSError as exc:
            return Response.json(_error_browse(
                str(target), f"Tidak dapat membaca folder: {exc}", tree_root))

        return Response.json({
            "ok": True,
            "path": str(target),
            "images": [image.to_dict() for image in images],
            "directories": [
                {
                    "name": folder.name,
                    "path": str(folder),
                    "has_children": image_service.has_subdirectories(folder),
                }
                for folder in directories
            ],
            "tree_root": str(tree_root),
            "default_path": str(default_path),
            "crypto": aimg_service.status(),
            "error": None,
        })

    @router.get("/api/tree")
    def tree_handler(request, context) -> Response:
        """List the immediate sub-folders of a folder for the sidebar tree."""
        raw_path = request.query.get("path")
        if raw_path:
            target = image_service.resolve_path(raw_path)
            if target is None:
                return Response.json({
                    "ok": False, "path": str(raw_path),
                    "folders": [], "error": "Path tidak valid.",
                })
        else:
            target = _tree_root(_default_path())

        if not target.is_dir():
            return Response.json({
                "ok": False, "path": str(target),
                "folders": [], "error": f"Folder tidak ditemukan: {target}",
            })

        payload = image_service.folder_children(target)
        payload["path"] = str(target)
        payload.setdefault("error", None)
        return Response.json(payload)

    @router.get("/api/image")
    def image_handler(request, context) -> Response:
        """Serve raw image bytes; ``.aimg`` payloads are decrypted on demand.

        Plain images are streamed straight from disk.  A ``.aimg`` container is
        only served when the crypto session is actually unlocked; the
        plaintext stays in memory (no temporary file) and is never written to
        disk.
        """
        raw_path = request.query.get("path")
        if not raw_path:
            return Response.text("Missing 'path' parameter", status=400)

        if _is_aimg(raw_path):
            try:
                data, mime_type = aimg_service.payload(raw_path)
            except LockedError:
                return Response.json(
                    {"ok": False, "error": "Crypto session terkunci."},
                    status=403)
            except AimgError as exc:
                return Response.text(str(exc), status=422)
            except OSError:
                return Response.text(
                    f"Image not found: {raw_path}", status=404)
            return Response(data, content_type=mime_type,
                            headers=_image_headers(mime_type))

        served = image_service.read_image(raw_path)
        if served is None:
            return Response.text(
                f"Image not found or unsupported: {raw_path}", status=404)
        data, mime_type = served
        return Response(data, content_type=mime_type,
                        headers=_image_headers(mime_type))

    @router.get("/api/thumb")
    def thumb_handler(request, context) -> Response:
        """Serve a small preview for a card (encrypted thumbs for ``.aimg``).

        For grid rendering this avoids decrypting the full resolution payload
        of every visible ``.aimg``; the embedded thumbnail is decrypted instead.
        """
        raw_path = request.query.get("path")
        if not raw_path:
            return Response.text("Missing 'path' parameter", status=400)

        if _is_aimg(raw_path):
            try:
                thumb = aimg_service.thumbnail(raw_path)
                if thumb is None:
                    # No embedded thumbnail: fall back to the full payload.
                    data, mime_type = aimg_service.payload(raw_path)
                    return Response(data, content_type=mime_type,
                                    headers=_image_headers(mime_type))
                data, mime_type = thumb
            except LockedError:
                return Response.json(
                    {"ok": False, "error": "Crypto session terkunci."},
                    status=403)
            except AimgError as exc:
                return Response.text(str(exc), status=422)
            return Response(data, content_type=mime_type,
                            headers=_image_headers(mime_type))

        served = image_service.read_image(raw_path)
        if served is None:
            return Response.text(
                f"Image not found or unsupported: {raw_path}", status=404)
        data, mime_type = served
        return Response(data, content_type=mime_type,
                        headers=_image_headers(mime_type))

    # -- crypto session -------------------------------------------------
    @router.get("/api/aimg/status")
    def aimg_status_handler(request, context) -> Response:
        """Report the real crypto session state (never trusts client flags)."""
        return Response.json({"ok": True, **aimg_service.status()})

    @router.post("/api/aimg/unlock")
    def aimg_unlock_handler(request, context) -> Response:
        """Unlock the crypto session with the given password."""
        payload = request.get_json(default={}) or {}
        password = payload.get("password")
        if not isinstance(password, str) or not password:
            return Response.json({"ok": False, "error": "Password wajib diisi."},
                                 status=400)
        try:
            aimg_service.unlock(password)
        except WrongPasswordError:
            return Response.json(
                {"ok": False, "error": "Password salah."}, status=401)
        except CryptoError as exc:
            return Response.json({"ok": False, "error": str(exc)}, status=400)
        return Response.json({"ok": True, **aimg_service.status()})

    @router.post("/api/aimg/lock")
    def aimg_lock_handler(request, context) -> Response:
        """Lock the crypto session and drop all cached plaintext."""
        aimg_service.lock()
        return Response.json({"ok": True, **aimg_service.status()})

    @router.post("/api/aimg/encrypt")
    def aimg_encrypt_handler(request, context) -> Response:
        """Encrypt every supported image in a folder recursively."""
        payload = request.get_json(default={}) or {}
        raw_path = payload.get("path") or request.query.get("path")
        if not raw_path:
            return Response.json({"ok": False, "error": "Parameter 'path' wajib."})
        if not aimg_service.is_unlocked():
            return Response.json(
                {"ok": False, "error": "Crypto session terkunci."}, status=403)
        target = image_service.resolve_path(raw_path)
        if target is None or not target.exists() or not target.is_dir():
            return Response.json(
                {"ok": False, "error": "Folder tidak valid atau tidak ditemukan."})
        try:
            result = aimg_service.encrypt_folder(target)
        except LockedError:
            return Response.json(
                {"ok": False, "error": "Crypto session terkunci."}, status=403)
        except AimgError as exc:
            return Response.json({"ok": False, "error": str(exc)})
        aimg_service.clear_cache()
        return Response.json({"ok": True, **result})

    @router.post("/api/aimg/decrypt")
    def aimg_decrypt_handler(request, context) -> Response:
        """Decrypt every ``.aimg`` file in a folder recursively."""
        payload = request.get_json(default={}) or {}
        raw_path = payload.get("path") or request.query.get("path")
        if not raw_path:
            return Response.json({"ok": False, "error": "Parameter 'path' wajib."})
        if not aimg_service.is_unlocked():
            return Response.json(
                {"ok": False, "error": "Crypto session terkunci."}, status=403)
        target = image_service.resolve_path(raw_path)
        if target is None or not target.exists() or not target.is_dir():
            return Response.json(
                {"ok": False, "error": "Folder tidak valid atau tidak ditemukan."})
        try:
            result = aimg_service.decrypt_folder(target)
        except LockedError:
            return Response.json(
                {"ok": False, "error": "Crypto session terkunci."}, status=403)
        except AimgError as exc:
            return Response.json({"ok": False, "error": str(exc)})
        aimg_service.clear_cache()
        return Response.json({"ok": True, **result})

    # -- groups (database backed, Betrayer Data Layer) -------------------
    @router.get("/api/groups")
    def groups_list_handler(request, context) -> Response:
        """List every group stored in the database."""
        return Response.json({"ok": True, "groups": group_service.list_groups()})

    @router.post("/api/groups")
    def groups_create_handler(request, context) -> Response:
        """Create a new group."""
        payload = request.get_json(default={}) or {}
        name = payload.get("name") or request.query.get("name")
        try:
            group = group_service.create_group(name)
        except GroupError as exc:
            return Response.json({"ok": False, "error": str(exc)}, status=400)
        return Response.json({"ok": True, "group": group})

    @router.post("/api/groups/rename")
    def groups_rename_handler(request, context) -> Response:
        """Rename an existing group."""
        payload = request.get_json(default={}) or {}
        try:
            group = group_service.rename_group(int(payload.get("id")), payload.get("name"))
        except (GroupError, TypeError, ValueError) as exc:
            return Response.json({"ok": False, "error": str(exc)}, status=400)
        return Response.json({"ok": True, "group": group})

    @router.post("/api/groups/delete")
    def groups_delete_handler(request, context) -> Response:
        """Delete a group (and its memberships, never the physical files)."""
        payload = request.get_json(default={}) or {}
        try:
            result = group_service.delete_group(int(payload.get("id")))
        except (GroupError, TypeError, ValueError) as exc:
            return Response.json({"ok": False, "error": str(exc)}, status=400)
        return Response.json({"ok": True, **result})

    @router.get("/api/groups/detail")
    def groups_detail_handler(request, context) -> Response:
        """Return one group together with its member files."""
        try:
            group_id = int(request.query.get("id"))
        except (TypeError, ValueError):
            return Response.json({"ok": False, "error": "Parameter 'id' wajib."}, status=400)
        detail = group_service.group_members(group_id)
        if detail is None:
            return Response.json(
                {"ok": False, "error": f"Group {group_id} tidak ditemukan."}, status=404)
        return Response.json({"ok": True, "group": detail})

    @router.post("/api/groups/add")
    def groups_add_handler(request, context) -> Response:
        """Add the file at ``path`` to the group (duplicates are ignored)."""
        payload = request.get_json(default={}) or {}
        path = payload.get("path")
        if not path:
            return Response.json({"ok": False, "error": "Parameter 'path' wajib."}, status=400)
        try:
            result = group_service.add_member(int(payload.get("id")), path)
        except (GroupError, TypeError, ValueError) as exc:
            return Response.json({"ok": False, "error": str(exc)}, status=400)
        return Response.json({"ok": True, **result})

    @router.post("/api/groups/remove")
    def groups_remove_handler(request, context) -> Response:
        """Remove a membership (never the physical file)."""
        payload = request.get_json(default={}) or {}
        file_id = payload.get("file_id")
        try:
            result = group_service.remove_member(
                int(payload.get("id")),
                path=payload.get("path"),
                file_id=int(file_id) if file_id is not None else None,
            )
        except (GroupError, TypeError, ValueError) as exc:
            return Response.json({"ok": False, "error": str(exc)}, status=400)
        return Response.json({"ok": True, **result})

    @router.get("/api/groups/for")
    def groups_for_handler(request, context) -> Response:
        """List the groups a given file (by path) belongs to."""
        path = request.query.get("path")
        if not path:
            return Response.json({"ok": False, "error": "Parameter 'path' wajib."}, status=400)
        return Response.json({"ok": True, "groups": group_service.groups_for_path(path)})

    @router.post("/api/groups/reconcile")
    def groups_reconcile_handler(request, context) -> Response:
        """Re-link a renamed/moved file to its group membership by identity."""
        payload = request.get_json(default={}) or {}
        new_path = payload.get("new_path")
        if not new_path:
            return Response.json(
                {"ok": False, "error": "Parameter 'new_path' wajib."}, status=400)
        try:
            result = group_service.reconcile(payload.get("old_path"), new_path)
        except (GroupError, TypeError, ValueError) as exc:
            return Response.json({"ok": False, "error": str(exc)}, status=400)
        return Response.json({"ok": True, **result})

    # -- file operations (right-click context menu) ---------------------
    def _paths_from(payload: dict) -> list:
        """Return the clean list of path strings from a JSON payload."""
        paths = (payload or {}).get("paths")
        if not isinstance(paths, list):
            return []
        return [p for p in paths if isinstance(p, str) and p.strip()]

    @router.post("/api/files/newfolder")
    def files_newfolder_handler(request, context) -> Response:
        """Create a new folder inside the active ``parent`` folder."""
        payload = request.get_json(default={}) or {}
        parent = payload.get("parent") or request.query.get("parent")
        name = payload.get("name") or request.query.get("name")
        if not parent or not name:
            return Response.json(
                {"ok": False, "error": "Parameter 'parent' dan 'name' wajib."},
                status=400)
        resolved = image_service.resolve_path(parent)
        if resolved is None or not resolved.is_dir():
            return Response.json(
                {"ok": False, "error": "Folder induk tidak valid."}, status=400)
        try:
            folder = file_ops.new_folder(resolved, name)
        except FileOpsError as exc:
            return Response.json({"ok": False, "error": str(exc)}, status=400)
        return Response.json({"ok": True, "folder": folder})

    @router.post("/api/files/rename")
    def files_rename_handler(request, context) -> Response:
        """Rename a single file (membership is re-linked by identity)."""
        payload = request.get_json(default={}) or {}
        raw_path = payload.get("path") or request.query.get("path")
        name = payload.get("name")
        if not raw_path or not name:
            return Response.json(
                {"ok": False, "error": "Parameter 'path' dan 'name' wajib."},
                status=400)
        tracked = False
        try:
            tracked = bool(group_service.groups_for_path(raw_path))
        except (GroupError, OSError, ValueError):
            tracked = False
        try:
            result = file_ops.rename(raw_path, name)
        except FileOpsError as exc:
            return Response.json({"ok": False, "error": str(exc)}, status=400)
        if tracked and result.get("old_path") and not result.get("unchanged"):
            try:
                group_service.reconcile(result["old_path"], result["path"])
            except (GroupError, OSError, ValueError):
                pass
        return Response.json({"ok": True, **result})

    def _transfer(request, *, move: bool) -> Response:
        payload = request.get_json(default={}) or {}
        paths = _paths_from(payload)
        target = payload.get("target") or payload.get("folder") \
            or request.query.get("target")
        if not paths:
            return Response.json(
                {"ok": False, "error": "Parameter 'paths' wajib (array)."},
                status=400)
        if not target:
            return Response.json(
                {"ok": False, "error": "Parameter 'target' wajib."}, status=400)

        tracked = set()
        if move:
            for raw in paths:
                resolved = image_service.resolve_path(raw)
                if resolved is None:
                    continue
                try:
                    if group_service.groups_for_path(str(resolved)):
                        tracked.add(str(resolved))
                except (GroupError, OSError, ValueError):
                    continue
        try:
            result = file_ops.move(paths, target) if move else file_ops.copy(paths, target)
        except FileOpsError as exc:
            return Response.json({"ok": False, "error": str(exc)}, status=400)

        if tracked:
            for item in result.get("items", []):
                if item.get("source") in tracked:
                    try:
                        group_service.reconcile(item["source"], item["target"])
                    except (GroupError, OSError, ValueError):
                        continue
        return Response.json({"ok": True, **result})

    @router.post("/api/files/batch-rename")
    def files_batch_rename_handler(request, context) -> Response:
        """Rename a batch of files using prefix/suffix + sequence/padding rules.

        The naming, collision handling and the two-phase (temp) rename all live
        in the existing :class:`FileOpsService`; this handler only validates the
        request, remembers which files belong to a group *before* renaming, and
        re-links those memberships afterwards so group references stay intact.
        """
        payload = request.get_json(default={}) or {}
        paths = _paths_from(payload)
        if not paths:
            return Response.json(
                {"ok": False, "error": "Parameter 'paths' wajib (array)."},
                status=400)

        prefix = str(payload.get("prefix") or "")
        suffix = str(payload.get("suffix") or "")
        numbering = payload.get("numbering")
        numbering = True if numbering is None else bool(numbering)
        try:
            start = int(payload.get("start") if payload.get("start") is not None else 1)
            padding = int(payload.get("padding") if payload.get("padding") is not None else 0)
        except (TypeError, ValueError):
            return Response.json(
                {"ok": False, "error": "Parameter nomor/padding tidak valid."},
                status=400)

        # Remember group membership *before* the rename so reconcile() only
        # touches real members (never registers a phantom row for an untracked
        # file).
        tracked = set()
        for raw in paths:
            resolved = image_service.resolve_path(raw)
            if resolved is None:
                continue
            try:
                if group_service.groups_for_path(str(resolved)):
                    tracked.add(str(resolved))
            except (GroupError, OSError, ValueError):
                continue

        try:
            result = file_ops.batch_rename(
                paths,
                prefix=prefix,
                suffix=suffix,
                numbering=numbering,
                start=start,
                padding=padding,
            )
        except FileOpsError as exc:
            return Response.json({"ok": False, "error": str(exc)}, status=400)

        for item in result.get("items", []):
            old = item.get("path")
            new = item.get("new_path")
            if item.get("status") in ("renamed", "unchanged") and old in tracked and new:
                try:
                    group_service.reconcile(old, new)
                except (GroupError, OSError, ValueError):
                    continue
        return Response.json({"ok": True, **result})

    @router.post("/api/files/delete")
    def files_delete_handler(request, context) -> Response:
        """Delete selected files and remove stale group memberships."""
        payload = request.get_json(default={}) or {}
        paths = _paths_from(payload)
        if not paths:
            return Response.json({"ok": False, "error": "Parameter 'paths' wajib (array)."}, status=400)
        try:
            result = file_ops.delete_multiple(paths)
        except FileOpsError as exc:
            return Response.json({"ok": False, "error": str(exc)}, status=400)
        # Deleted files cannot be resolved by identity anymore; remove their
        # registered records and pivot rows through the existing Group service.
        for item in result.get("items", []):
            if item.get("deleted"):
                try:
                    group_service.remove_file_path(item.get("path"))
                except (GroupError, OSError, ValueError):
                    pass
        return Response.json({"ok": True, **result})

    @router.post("/api/files/copy")
    def files_copy_handler(request, context) -> Response:
        """Copy one or many files into a target folder."""
        return _transfer(request, move=False)

    @router.post("/api/files/move")
    def files_move_handler(request, context) -> Response:
        """Move one or many files into a target folder."""
        return _transfer(request, move=True)

    def _aimg_files(request, *, decrypt: bool) -> Response:
        """Encrypt/decrypt only the *selected* files, validating every type.

        The context menu's Encrypt must only ever touch plain, supported image
        files (never a ``.aimg`` container), and Decrypt must only ever touch
        ``.aimg`` containers (never a plain image).  A mixed selection keeps
        the files of the right type and reports the rest as skipped, instead
        of blindly running the crypto operation on whatever was selected.
        """
        payload = request.get_json(default={}) or {}
        paths = _paths_from(payload)
        if not paths:
            return Response.json(
                {"ok": False, "error": "Parameter 'paths' wajib (array)."},
                status=400)
        if not aimg_service.is_unlocked():
            return Response.json(
                {"ok": False, "error": "Crypto session terkunci."}, status=403)

        result = {
            "found": len(paths), "success": 0, "failed": 0, "skipped": 0,
            "errors": [], "items": [],
        }
        candidates = []
        for raw in paths:
            target = image_service.resolve_path(raw)
            if target is None or not target.is_file():
                result["failed"] += 1
                result["errors"].append(
                    {"path": str(raw), "error": "File tidak ditemukan."})
                continue
            suffix = target.suffix.lower()
            if decrypt:
                # Decrypt only ever processes ``.aimg`` containers.
                if suffix != _AIMG_EXTENSION:
                    result["skipped"] += 1
                    result["errors"].append({
                        "path": str(target),
                        "error": "Bukan file .aimg; dilewati dari dekripsi.",
                    })
                    continue
            else:
                # Encrypt only ever processes plain, supported images.  A
                # ``.aimg`` is never re-encrypted (that would corrupt it).
                if suffix == _AIMG_EXTENSION:
                    result["skipped"] += 1
                    result["errors"].append({
                        "path": str(target),
                        "error": ".aimg tidak dapat dienkripsi; dilewati.",
                    })
                    continue
                if suffix not in image_service.SUPPORTED_EXTENSIONS:
                    result["failed"] += 1
                    result["errors"].append({
                        "path": str(target),
                        "error": "Format image tidak didukung.",
                    })
                    continue
            candidates.append(target)

        if not candidates:
            return Response.json({
                "ok": False,
                "error": ("Tidak ada file .aimg yang valid pada pilihan."
                          if decrypt else
                          "Tidak ada image biasa yang didukung pada pilihan."),
                "found": result["found"], "success": 0,
                "failed": result["failed"], "skipped": result["skipped"],
                "errors": result["errors"],
            })

        for target in candidates:
            try:
                produced = (
                    aimg_service.decrypt_file(target)
                    if decrypt else aimg_service.encrypt_file(target)
                )
                result["success"] += 1
                result["items"].append(
                    {"source": str(target), "target": str(produced)})
            except Exception as exc:  # noqa: BLE001 - isolate per file failure
                result["failed"] += 1
                result["errors"].append({"path": str(target), "error": str(exc)})
        aimg_service.clear_cache()
        return Response.json({"ok": True, **result})

    @router.post("/api/aimg/encrypt-files")
    def aimg_encrypt_files_handler(request, context) -> Response:
        """Encrypt the selected image files (context menu Encrypt)."""
        return _aimg_files(request, decrypt=False)

    @router.post("/api/aimg/decrypt-files")
    def aimg_decrypt_files_handler(request, context) -> Response:
        """Decrypt the selected ``.aimg`` files (context menu Decrypt)."""
        return _aimg_files(request, decrypt=True)

    return router


def create_flask_app(app: BetrayerApplication) -> FlaskAdapter:
    """Create the FlaskAdapter with routes and build the Flask app."""
    router = create_router(app)
    adapter = FlaskAdapter(app, router)
    adapter.build()
    return adapter
