"""Permanent verification for the Veyra right-click context menu.

Run with the project venv::

    J:\\Veyra\\venv\\Scripts\\python.exe verify_contextmenu.py

The context menu itself is UI, but every action it exposes is backed by a real
HTTP endpoint.  This suite therefore proves, against a throw-away SQLite file
and **real files on disk** (created in a temp workspace):

 1. New Folder creates a folder in the active folder (and refuses invalid names
    / existing folders).
 2. Copy to Folder copies one and many files, originals kept, no overwrite.
 3. Move to Folder moves one and many files, originals removed, and keeps group
    membership (identity) intact by re-linking the stored path.
 4. Rename renames a single file (extension preserved) and re-links membership.
 5. Encrypt / Decrypt act on the *selected* files and stay behind the crypto
    session (403 while locked); ``.aimg`` files round-trip byte-identically.
 6. ``.aimg`` containers are copied/moved as opaque bytes (no decryption).
 7. The UI (index.html) declares the full context-menu structure, the
    context-aware Encrypt/Decrypt visibility, the full-screen viewer and the
    wiring to every file-operation endpoint - and suppresses the native menu.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import sys
import tempfile
import zlib
from pathlib import Path

PASSWORD = "Veyra-Ctx-Test-4Qz!"

WORK = Path(tempfile.mkdtemp(prefix="veyra_ctx_"))
API_DB = WORK / "veyra.db"

BROWSE = WORK / "browse"
BROWSE.mkdir(parents=True, exist_ok=True)

os.environ["VEYRA_KEYSTORE"] = str(WORK / "keystore.bin")
os.environ["VEYRA_DEFAULT_PATH"] = str(BROWSE)
os.environ["VEYRA_DB"] = str(API_DB)

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


def j(resp):
    return json.loads(resp.data.decode("utf-8"))


def main() -> int:  # noqa: C901 - a verification script is intentionally linear
    # -- real files on disk ------------------------------------------------
    section("1. Real files on disk (scratch workspace)")
    src = BROWSE / "src"
    src.mkdir(parents=True, exist_ok=True)
    a1 = src / "alpha1.png"
    a2 = src / "alpha2.png"
    b1 = src / "beta1.png"
    a1.write_bytes(png_bytes((210, 40, 40)))
    a2.write_bytes(png_bytes((40, 210, 40)))
    b1.write_bytes(png_bytes((40, 40, 210)))
    check(all(p.is_file() for p in (a1, a2, b1)), "three test images created")

    from veyra.application import create_app

    app = create_app()
    client = app._flask_app.test_client()

    # -- New Folder --------------------------------------------------------
    section("2. New Folder (active folder)")
    body = j(client.post("/api/files/newfolder", json={"parent": str(BROWSE), "name": "Album"}))
    check(body["ok"] and body.get("folder", {}).get("name") == "Album", "creates a new folder")
    check((BROWSE / "Album").is_dir(), "folder physically exists on disk")
    body = j(client.post("/api/files/newfolder", json={"parent": str(BROWSE), "name": "Album"}))
    check(body["ok"] is False, "creating an existing folder is refused")
    body = j(client.post("/api/files/newfolder", json={"parent": str(BROWSE), "name": "bad/name"}))
    check(body["ok"] is False, "invalid folder name refused")
    body = j(client.post("/api/files/newfolder", json={"parent": str(WORK / "nope"), "name": "x"}))
    check(body["ok"] is False, "non-existent parent refused")

    album = BROWSE / "Album"

    # -- Copy to Folder ----------------------------------------------------
    section("3. Copy to Folder (single + multi selection)")
    body = j(client.post("/api/files/copy", json={"paths": [str(a1)], "target": str(album)}))
    check(body["ok"] and body["copied"] == 1, "single file copied")
    check(a1.exists() and (album / "alpha1.png").is_file(), "original kept, copy present")
    body = j(client.post("/api/files/copy", json={"paths": [str(a2), str(b1)], "target": str(album)}))
    check(body["ok"] and body["copied"] == 2, "two files copied (multi selection)")
    check((album / "alpha2.png").is_file() and (album / "beta1.png").is_file(), "both copies present")
    body = j(client.post("/api/files/copy", json={"paths": [str(a1)], "target": str(album)}))
    check(body["failed"] == 1 and body["copied"] == 0, "existing target is never overwritten")
    body = j(client.post("/api/files/copy", json={"paths": [], "target": str(album)}))
    check(body["ok"] is False, "empty selection refused")

    # -- Move to Folder + group membership --------------------------------
    section("4. Move to Folder keeps group membership")
    body = j(client.post("/api/groups", json={"name": "Moves"}))
    gid = body["group"]["id"]
    body = j(client.post("/api/groups/add", json={"id": gid, "path": str(a2)}))
    check(body["ok"] and body["added"], "image added to a group")

    moved_dir = BROWSE / "Moved"
    moved_dir.mkdir()
    body = j(client.post("/api/files/move", json={"paths": [str(a2)], "target": str(moved_dir)}))
    check(body["ok"] and body["moved"] == 1, "single file moved")
    check(not a2.exists() and (moved_dir / "alpha2.png").is_file(), "original removed, moved copy present")

    still = j(client.get("/api/groups/for?path=" + str(moved_dir / "alpha2.png")))
    check(
        still["ok"] and any(g["id"] == gid for g in still["groups"]),
        "moved file is still a member of its group (identity preserved)",
    )

    section("5. Move to Folder (multi selection)")
    two = BROWSE / "Two"
    two.mkdir()
    body = j(client.post("/api/files/move", json={
        "paths": [str(b1), str(album / "alpha1.png")], "target": str(two)}))
    check(body["ok"] and body["moved"] == 2, "two files moved at once")
    check((two / "beta1.png").is_file() and (two / "alpha1.png").is_file(), "both moved present")

    # -- Rename ------------------------------------------------------------
    section("6. Rename (single file, membership re-linked)")
    target_file = two / "alpha1.png"
    body = j(client.post("/api/groups/add", json={"id": gid, "path": str(target_file)}))
    check(body["ok"], "file registered to group before rename")
    body = j(client.post("/api/files/rename", json={"path": str(target_file), "name": "renamed"}))
    check(body["ok"] and (two / "renamed.png").is_file(), "rename without ext preserves .png")
    check(not target_file.exists(), "old name gone after rename")
    body = j(client.post("/api/files/rename", json={
        "path": str(two / "renamed.png"), "name": "beta1.png"}))
    check(body["ok"] is False, "rename to an existing name is refused")
    body = j(client.post("/api/files/rename", json={
        "path": str(two / "renamed.png"), "name": "renamed2.png"}))
    check(body["ok"] and (two / "renamed2.png").is_file(), "explicit extension rename works")
    groups_now = j(client.get("/api/groups/for?path=" + str(two / "renamed2.png")))
    check(
        groups_now["ok"] and any(g["id"] == gid for g in groups_now["groups"]),
        "renamed file still belongs to its group",
    )

    # -- Encrypt / Decrypt (context aware) ---------------------------------
    section("7. Encrypt / Decrypt stay behind the crypto session")
    body = j(client.post("/api/aimg/encrypt-files", json={"paths": [str(two / "beta1.png")]}))
    check(body["ok"] is False, "encrypt refused while the session is locked (403)")

    body = j(client.post("/api/aimg/unlock", json={"password": PASSWORD}))
    check(body["ok"], "crypto session unlocked")

    plain_a = two / "beta1.png"
    plain_bytes = plain_a.read_bytes()
    body = j(client.post("/api/aimg/encrypt-files", json={"paths": [str(plain_a)]}))
    check(body["ok"] and body["success"] == 1, "selected plain image encrypted")
    container = two / "beta1.aimg"
    check(container.is_file() and not plain_a.exists(), ".aimg produced, original replaced")
    check(container.read_bytes()[:4] == b"AIMG", ".aimg magic present")

    body = j(client.post("/api/aimg/decrypt-files", json={"paths": [str(container)]}))
    check(body["ok"] and body["success"] == 1, "selected .aimg decrypted")
    check(plain_a.is_file() and plain_a.read_bytes() == plain_bytes, "round-trip byte identical")
    check(not container.exists(), ".aimg removed after decrypt")

    section("8. Multi-file encrypt then decrypt")
    multi = BROWSE / "Multi"
    multi.mkdir()
    m1, m2 = multi / "m1.png", multi / "m2.png"
    m1.write_bytes(png_bytes((11, 22, 33)))
    m2.write_bytes(png_bytes((44, 55, 66)))
    body = j(client.post("/api/aimg/encrypt-files", json={"paths": [str(m1), str(m2)]}))
    check(body["ok"] and body["success"] == 2, "two files encrypted at once")
    body = j(client.post("/api/aimg/decrypt-files", json={
        "paths": [str(multi / "m1.aimg"), str(multi / "m2.aimg")]}))
    check(body["ok"] and body["success"] == 2, "two .aimg decrypted at once")
    check(m1.is_file() and m2.is_file(), "both originals restored")

    section("9. .aimg moved/copied as opaque bytes (no crypto)")
    body = j(client.post("/api/aimg/lock", json={}))
    check(body["ok"], "session locked again")
    container2 = multi / "m1.aimg"
    # re-encrypt one file so a container exists again
    j(client.post("/api/aimg/unlock", json={"password": PASSWORD}))
    j(client.post("/api/aimg/encrypt-files", json={"paths": [str(m1)]}))
    j(client.post("/api/aimg/lock", json={}))
    check(container2.is_file(), "container present for the opaque test")
    opaque_dir = BROWSE / "Opaque"
    opaque_dir.mkdir()
    body = j(client.post("/api/files/move", json={
        "paths": [str(container2)], "target": str(opaque_dir)}))
    check(body["ok"] and body["moved"] == 1, "locked .aimg moved without unlocking")
    check((opaque_dir / "m1.aimg").read_bytes()[:4] == b"AIMG", "moved container bytes intact")

    # -- UI wiring ---------------------------------------------------------
    section("10. UI declares the full context-menu structure + wiring")
    # The frontend is modular: the shell loads the sources from /static/js, so
    # the audit concatenates the shell with every loaded module.
    shell = (Path(__file__).resolve().parent / "veyra" / "index.html").read_text(encoding="utf-8")
    static_root = Path(__file__).resolve().parent / "veyra" / "static"
    parts = [shell]
    for src in re.findall(r'<script\s+src="([^"]+)"', shell):
        rel = src.lstrip("/")
        if rel.startswith("static/"):
            rel = rel[len("static/"):]
        asset = static_root / rel
        if asset.is_file():
            parts.append(asset.read_text(encoding="utf-8"))
    html = "\n".join(parts)
    # Scope the ordering check to the context-menu definition block so tokens
    # that also exist elsewhere (e.g. "Tools" in the top menu bar) don't skew it.
    block_start = html.find("ctxMenu.className = 'ctx-menu'")
    block_end = html.find("document.body.appendChild(ctxMenu);", block_start)
    block = html[block_start:block_end] if block_start >= 0 and block_end > block_start else ""

    ordered_tokens = [
        "Open Full Screen",
        "Batch Rename...",
        ">Encrypt<",
        ">Decrypt<",
        "Add to Group",
        "Remove from Group",
        "Copy to Folder...",
        "Move to Folder...",
        ">Copy<",
        ">Cut<",
        "New Folder",
        ">Rename<",
    ]
    positions = []
    for token in ordered_tokens:
        pos = block.find(token)
        positions.append(pos)
        check(pos >= 0, f"menu contains '{token}'")
    check(block != "" and positions == sorted(positions),
          "menu items appear in the spec order")

    for token in (
        "function showContextMenu",
        "function hideContextMenu",
        "function handleCardClick",
        "function buildContextMenu",
        "function openFullScreen",
        "ctxMenu",
        "e.preventDefault()",
        "'Escape'",
        "ctrlKey",
        "shiftKey",
        "'/api/files/copy'",
        "'/api/files/move'",
        "'/api/files/rename'",
        "'/api/files/newfolder'",
        "'/api/aimg/encrypt-files'",
        "'/api/aimg/decrypt-files'",
        "'/api/groups/add'",
        "'/api/groups/remove'",
        "contextmenu",
    ):
        check(token in html, f"index.html wires {token}")

    # context-aware visibility of Encrypt/Decrypt
    check(
        "sel.every(function (x) { return !x.encrypted; })" in html
        and "sel.every(function (x) { return x.encrypted; })" in html,
        "Encrypt/Decrypt visibility is context aware (plain vs .aimg)",
    )
    check("__CTX__" not in html, "no placeholder left behind")

    served = client.get("/").data.decode("utf-8")
    check("menu-row" in html and "/api/files/copy" in html,
          "UI ships the context menu + live file-op calls")
    check("/static/js/modules/contextmenu.js" in served,
          "served UI loads the context-menu module")

    # -- summary -----------------------------------------------------------
    print("\n" + "=" * 64)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("RESULT: ALL CONTEXT-MENU VERIFICATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    raise SystemExit(code)
