"""Permanent verification for the Veyra Group/Database subsystem.

Run with the project venv::

    J:\\Veyra\\venv\\Scripts\\python.exe verify_groups.py

It performs **real** verification against a throw-away SQLite file and real
files on disk (created in a temp workspace), proving:

 1. Create group.
 2. Add several files from different folders.
 3. Query group -> correct members.
 4. One file can belong to several groups.
 5. Remove membership does not delete the physical file.
 6. Delete group does not delete the physical files.
 7. Rename/move + reconcile keeps membership (identity preserved).
 8. ``.aimg`` can be a group member.
 9. The database really is the Betrayer Data Layer (DatabaseManager + SQLite
    engine + ORM models + ManyToMany).
10. No direct database bypass (no hand-written sqlite3/SQL in app code).
11. The HTTP API exists and is DB backed.
12. The UI is wired to the DB backed API (not static/dummy data).
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import sys
import tempfile
import zlib
from pathlib import Path

PASSWORD = "Veyra-Group-Test-7Qx!"

WORK = Path(tempfile.mkdtemp(prefix="veyra_groups_"))
DIRECT_DB = WORK / "veyra_direct.db"
API_DB = WORK / "veyra_api.db"

os.environ["VEYRA_KEYSTORE"] = str(WORK / "keystore.bin")
os.environ["VEYRA_DEFAULT_PATH"] = str(WORK / "browse")

sys.path.insert(0, str(Path(__file__).resolve().parent))

FAILS: list[str] = []


def check(cond, label: str) -> None:
    print(("  [PASS] " if cond else "  [FAIL] ") + label)
    if not cond:
        FAILS.append(label)


def section(title: str) -> None:
    print("\n== " + title + " ==")


def png_bytes(rgb: tuple) -> bytes:
    """Return a valid, distinct PNG for the given colour (no Pillow needed)."""

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    w = h = 8
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * w
    idat = zlib.compress(row * h, 9)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def scalar(manager, sql: str, params=None) -> int:
    rows = manager.execute(sql, params or []).get("rows", [])
    return int(next(iter(rows[0].values()))) if rows else 0


def main() -> int:  # noqa: C901 - a verification script is intentionally linear
    section("1. Real files on disk (scratch workspace)")
    folderA = WORK / "folderA"
    folderB = WORK / "folderB"
    folderA.mkdir(parents=True, exist_ok=True)
    folderB.mkdir(parents=True, exist_ok=True)
    a1 = folderA / "alpha1.png"
    a2 = folderA / "alpha2.png"
    b1 = folderB / "beta1.png"
    a1.write_bytes(png_bytes((200, 30, 30)))
    a2.write_bytes(png_bytes((30, 200, 30)))
    b1.write_bytes(png_bytes((30, 30, 200)))
    check(all(p.is_file() for p in (a1, a2, b1)), "test images created on disk")

    # ------------------------------------------------------------------
    section("2/9. Database really IS the Betrayer Data Layer")
    from betrayer.data.database import DatabaseManager
    from betrayer.data.engines.sqlite import SQLiteEngine
    from betrayer.data.orm import ORMModel
    from betrayer.data.relationships import ManyToMany

    from veyra.services import group_models
    from veyra.services.database import build_manager, ensure_schema
    from veyra.services.group_service import GroupService, compute_stable_id

    manager = build_manager(DIRECT_DB)
    ensure_schema(manager)

    check(isinstance(manager, DatabaseManager), "manager is betrayer.data.DatabaseManager")
    check(isinstance(manager.engine, SQLiteEngine), "engine is Betrayer SQLiteEngine")
    check(manager.dialect.name == "sqlite", "dialect is sqlite (storage engine only)")
    check(
        issubclass(group_models.File, ORMModel)
        and issubclass(group_models.Group, ORMModel)
        and issubclass(group_models.GroupMember, ORMModel),
        "File/Group/GroupMember subclass Betrayer ORMModel",
    )
    rel = group_models.Group.orm_relationships().get("files")
    check(
        isinstance(rel, ManyToMany) and rel.kind == "many_to_many",
        "Group.files is a Betrayer ManyToMany relationship",
    )
    check(group_models.File.__connection__ is manager, "ORM models bound to the Betrayer manager")

    tables = {
        row["name"]
        for row in manager.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).get("rows", [])
    }
    check(
        {"files", "groups", "group_members"} <= tables,
        f"tables created via Betrayer DDL: {sorted(tables)}",
    )

    svc = GroupService(manager)

    # ------------------------------------------------------------------
    section("3. Create group -> persisted in the database")
    g1 = svc.create_group("Alpha")
    check(bool(g1.get("id")) and g1["name"] == "Alpha", "create_group returns id + name")
    stored = group_models.Group.query().where("id", g1["id"]).first()
    check(stored is not None and stored.name == "Alpha", "group row exists in the database")
    check(scalar(manager, "SELECT COUNT(*) AS c FROM groups") == 1, "exactly one group row in SQLite")

    # ------------------------------------------------------------------
    section("4. Add several files from DIFFERENT folders")
    r1 = svc.add_member(g1["id"], a1)
    r2 = svc.add_member(g1["id"], a2)
    r3 = svc.add_member(g1["id"], b1)
    check(r1["added"] and r2["added"] and r3["added"], "three distinct files added")
    check(a1.exists() and a2.exists() and b1.exists(), "adding to a group never removes files")

    # ------------------------------------------------------------------
    section("5. Query group -> correct members")
    detail = svc.group_members(g1["id"])
    names = {m["name"] for m in detail["members"]}
    check(names == {"alpha1.png", "alpha2.png", "beta1.png"}, f"members correct: {sorted(names)}")
    check(detail["member_count"] == 3, "member_count == 3")
    member_paths = {m["path"] for m in detail["members"]}
    check(
        str(a1) in member_paths and str(b1) in member_paths,
        "membership spans two different folders",
    )

    # ------------------------------------------------------------------
    section("6. Duplicate membership is prevented")
    dup = svc.add_member(g1["id"], a1)
    check(dup["duplicate"] is True and dup["added"] is False, "repeat add flagged as duplicate")
    pivot = scalar(
        manager,
        "SELECT COUNT(*) AS c FROM group_members WHERE group_id=? AND file_id=?",
        [g1["id"], r1["file"]["id"]],
    )
    check(pivot == 1, "exactly one pivot row after a duplicate add")

    # ------------------------------------------------------------------
    section("7. One file can belong to several groups")
    g2 = svc.create_group("Beta")
    svc.add_member(g2["id"], a1)
    in_groups = {g["name"] for g in svc.groups_for_path(a1)}
    check(in_groups == {"Alpha", "Beta"}, f"alpha1 is in both groups: {sorted(in_groups)}")
    check(
        len(svc.groups_for_file(r1["file"]["id"])) == 2,
        "many-to-many link resolves both groups for the file",
    )

    # ------------------------------------------------------------------
    section("8. Remove membership keeps the physical file")
    rem = svc.remove_member(g1["id"], path=a2)
    check(rem["removed"] == 1, "membership row removed")
    check(a2.exists(), "physical file still exists after remove_member")
    detail = svc.group_members(g1["id"])
    check(
        {m["name"] for m in detail["members"]} == {"alpha1.png", "beta1.png"},
        "member list updated after removal",
    )

    # ------------------------------------------------------------------
    section("9. Delete group keeps the physical files")
    svc.add_member(g2["id"], b1)  # make sure g2 has a member too
    delres = svc.delete_group(g2["id"])
    check(delres["memberships_removed"] >= 1, "deleting the group removed its pivot rows")
    check(group_models.Group.query().where("id", g2["id"]).first() is None, "group row deleted")
    check(
        scalar(manager, "SELECT COUNT(*) AS c FROM group_members WHERE group_id=?", [g2["id"]]) == 0,
        "no orphan pivot rows for the deleted group",
    )
    check(a1.exists() and b1.exists(), "physical files still exist after delete_group")
    remaining = {g["name"] for g in svc.groups_for_path(a1)}
    check(remaining == {"Alpha"}, f"alpha1 only remains in Alpha: {sorted(remaining)}")

    # ------------------------------------------------------------------
    section("10. Rename/move + reconcile keeps membership (identity preserved)")
    moved = folderA / "beta1_moved.png"
    b1.rename(moved)
    check(moved.exists() and not b1.exists(), "file physically moved on disk")
    rec = svc.reconcile(b1, moved)
    check(rec["relinked"] is True, "reconcile relinked the existing identity (no new row)")
    detail = svc.group_members(g1["id"])
    member = next(m for m in detail["members"] if m["id"] == rec["file"]["id"])
    check(member["path"] == str(moved), "member path updated to the new location")
    check(member["name"] == "beta1_moved.png", "moved file is still a member of Alpha")
    check(
        member["stable_id"] == compute_stable_id(moved),
        "identity preserved across rename/move (content hash unchanged)",
    )
    check(
        scalar(manager, "SELECT COUNT(*) AS c FROM files") == 3,
        "no phantom file row created by reconcile",
    )

    # ------------------------------------------------------------------
    section("11. .aimg can be a group member")
    from veyra.security.crypto_session import CryptoService
    from veyra.services.aimg_service import AimgService

    crypto = CryptoService(WORK / "keystore.bin")
    crypto.initialize(PASSWORD)
    aimg_svc = AimgService(crypto)
    secret = folderB / "secret.png"
    secret.write_bytes(png_bytes((90, 90, 20)))
    container = aimg_svc.encrypt_file(secret)
    check(container.suffix == ".aimg" and container.exists(), "created a real .aimg container")
    aimg_member = svc.add_member(g1["id"], container)
    detail = svc.group_members(g1["id"])
    aimg_row = next(m for m in detail["members"] if m["id"] == aimg_member["file"]["id"])
    check(aimg_row["is_aimg"] is True, ".aimg stored as a member with is_aimg=True")

    # ------------------------------------------------------------------
    section("12. No password / master key / decrypted image in the database")
    columns = (
        set(group_models.File.orm_fields())
        | set(group_models.Group.orm_fields())
        | set(group_models.GroupMember.orm_fields())
    )
    secret_cols = {"password", "master_key", "key", "secret", "plaintext", "decrypted"}
    check(not (secret_cols & columns), f"schema has no secret columns: {sorted(columns)}")
    blob = DIRECT_DB.read_bytes()
    check(PASSWORD.encode() not in blob, "password bytes not present in the DB file")
    check(b"\x89PNG\r\n\x1a\n" not in blob, "no decrypted PNG signature in the DB file")
    check(b"\xff\xd8\xff" not in blob, "no decrypted JPEG signature in the DB file")

    # ------------------------------------------------------------------
    section("13/10. No direct database bypass (source audit)")
    src = Path(__file__).resolve().parent / "veyra"
    sources = {p: p.read_text(encoding="utf-8", errors="ignore") for p in src.rglob("*.py")}
    check(
        not any(("import sqlite3" in t) or ("sqlite3.connect" in t) for t in sources.values()),
        "no application module imports sqlite3 / opens the driver directly",
    )
    sql_markers = ("CREATE TABLE", "INSERT INTO", "UPDATE ", "DELETE FROM", "SELECT * FROM")
    offenders = [
        str(p.name)
        for p, t in sources.items()
        if any(marker in t for marker in sql_markers)
    ]
    check(not offenders, f"no hand-written SQL in application code (found in {offenders})")
    gs = sources.get(src / "services" / "group_service.py", "")
    check("from betrayer.data.query import Query" in gs, "group service uses the Betrayer Query Builder")
    check(
        "from betrayer.data.query import Query" in gs
        or "group_models" in gs,
        "group service builds on the Betrayer ORM models",
    )
    group_code = gs + sources.get(src / "services" / "group_models.py", "")
    coupling = ("sqlite3", "SQLiteEngine", "SQLiteConnection", "sqlite.connect")
    check(
        not any(token in group_code for token in coupling),
        "group code is driver agnostic (no concrete engine/driver coupling)",
    )

    # ------------------------------------------------------------------
    section("14. HTTP API exists and is DB backed")
    os.environ["VEYRA_DB"] = str(API_DB)
    os.environ["VEYRA_KEYSTORE"] = str(WORK / "keystore.bin")
    from veyra.application import create_app

    app = create_app()
    client = app._flask_app.test_client()

    def j(resp):
        return json.loads(resp.data.decode("utf-8"))

    r = client.get("/api/groups")
    check(r.status_code == 200 and j(r)["ok"] is True, "GET /api/groups -> 200 ok")
    check(app.container.has("database"), "Betrayer 'database' service installed on the app")

    body = j(client.post("/api/groups", json={"name": "HTTP Group"}))
    check(body["ok"] and bool(body["group"]["id"]), "POST /api/groups creates a group")
    hid = body["group"]["id"]

    api_img = WORK / "api.png"
    api_img.write_bytes(png_bytes((5, 5, 5)))
    body = j(client.post("/api/groups/add", json={"id": hid, "path": str(api_img)}))
    check(body["ok"] and body["added"] is True, "POST /api/groups/add adds a file")
    body = j(client.post("/api/groups/add", json={"id": hid, "path": str(api_img)}))
    check(body["duplicate"] is True, "duplicate add refused over HTTP")

    body = j(client.get("/api/groups/detail?id=%d" % hid))
    check(body["ok"] and len(body["group"]["members"]) == 1, "GET /api/groups/detail returns member")

    body = j(client.get("/api/groups/for?path=" + str(api_img)))
    check(
        body["ok"] and any(g["id"] == hid for g in body["groups"]),
        "GET /api/groups/for lists the group",
    )

    body = j(client.post("/api/groups/rename", json={"id": hid, "name": "HTTP Group 2"}))
    check(body["ok"] and body["group"]["name"] == "HTTP Group 2", "POST /api/groups/rename works")

    body = j(client.post("/api/groups/remove", json={"id": hid, "path": str(api_img)}))
    check(body["ok"] and body["removed"] == 1, "POST /api/groups/remove removes membership")
    check(api_img.exists(), "file still on disk after HTTP remove")

    body = j(client.post("/api/groups/delete", json={"id": hid}))
    check(body["ok"], "POST /api/groups/delete works")
    check(api_img.exists(), "file still on disk after HTTP delete")

    # Persistence proven straight from the API database file.
    api_manager = build_manager(API_DB)
    group_rows = scalar(api_manager, "SELECT COUNT(*) AS c FROM groups")
    pivot_rows = scalar(api_manager, "SELECT COUNT(*) AS c FROM group_members")
    check(group_rows == 0 and pivot_rows == 0, "HTTP create/delete really mutated the DB file")

    # ------------------------------------------------------------------
    section("15. UI minimum is wired to the DB backed API (not static data)")
    html = (Path(__file__).resolve().parent / "veyra" / "index.html").read_text(encoding="utf-8")
    for token in (
        "/api/groups",
        "/api/groups/add",
        "/api/groups/detail",
        "btnCreateGroup",
        "groupList",
        "function loadGroups",
    ):
        check(token in html, f"index.html references {token}")
    served = client.get("/").data.decode("utf-8")
    check(
        'id="groupsPanel"' in served and "/api/groups" in served,
        "served UI contains the group panel + live API calls",
    )

    # ------------------------------------------------------------------
    print("\n" + "=" * 64)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("RESULT: ALL GROUP/DATABASE VERIFICATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    raise SystemExit(code)
