"""Permanent verification for Veyra Delete / clipboard / Batch Rename.

Run with the project venv::

    J:\\Veyra\\venv\\Scripts\\python.exe verify_files_actions.py

This suite proves - against a throw-away SQLite file and **real files on disk**
created in a temp workspace - that:

 1. Delete removes a single file (right-click Remove / keyboard Delete).
 2. Delete removes a multi-selection, reports a partial failure for a missing
    file, and never touches files that were not selected.
 3. Deleting a group member keeps the group references consistent (the stale
    File/pivot rows are gone, the group itself survives).
 4. The keyboard Copy/Cut/Paste actions are backed by the real copy/move
    endpoints and use Veyra's *internal* clipboard state (never the browser
    clipboard).
 5. Batch Rename really renames single + multi selections: prefix/suffix,
    numbering with starting number + zero padding, extension preserved, and the
    new names land on disk.
 6. A batch rename never overwrites: a colliding target is refused and the
    existing file's bytes are untouched.
 7. Batch-renaming a file that belongs to a group keeps its membership
    (reconciled by identity).
 8. The UI declares the real Batch Rename dialog (not a placeholder), the
    Delete wiring and the keyboard guards.
 9. Every frontend JavaScript file (shell + modules) is valid JavaScript
    (``node --check``).

The frontend was refactored from a single ``veyra/index.html`` into a shell
(``index.html``) plus modular ``veyra/static/css`` / ``veyra/static/js`` assets,
so the UI checks concatenate the shell with the loaded module sources.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

WORK = Path(tempfile.mkdtemp(prefix="veyra_files_"))
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


HTML_PATH = Path(__file__).resolve().parent / "veyra" / "index.html"
STATIC_ROOT = Path(__file__).resolve().parent / "veyra" / "static"


def _ui_html() -> str:
    """Return the UI shell + every frontend asset the shell loads.

    The frontend is modular (``index.html`` shell + ``veyra/static/js`` modules),
    so UI wiring checks must look at the concatenation of all loaded sources.
    """
    shell = HTML_PATH.read_text(encoding="utf-8")
    scripts = re.findall(r'<script\s+src="([^"]+)"', shell)
    parts = [shell]
    for src in scripts:
        rel = src.lstrip("/")
        if rel.startswith("static/"):
            rel = rel[len("static/"):]
        asset = STATIC_ROOT / rel
        if asset.is_file():
            parts.append(asset.read_text(encoding="utf-8"))
    return "\n".join(parts)


def j(resp):
    return json.loads(resp.data.decode("utf-8"))


def main() -> int:  # noqa: C901 - a verification script is intentionally linear
    from veyra.application import create_app

    app = create_app()
    client = app._flask_app.test_client()

    # ---------------------------------------------------------------- delete
    section("1. Delete single file (right-click Remove / keyboard Delete)")
    d1 = BROWSE / "del_single"
    d1.mkdir()
    one = d1 / "one.png"
    one.write_bytes(png_bytes((10, 10, 10)))
    body = j(client.post("/api/files/delete", json={"paths": [str(one)]}))
    check(body["ok"] and body["deleted"] == 1 and body["failed"] == 0,
          "delete single: deleted == 1")
    check(not one.exists(), "delete single: file really gone from disk")

    section("2. Delete multi-selection + partial failure + no collateral")
    d2 = BROWSE / "del_multi"
    d2.mkdir()
    m1, m2, keep = d2 / "m1.png", d2 / "m2.png", d2 / "keep.png"
    for p, c in ((m1, (1, 2, 3)), (m2, (4, 5, 6)), (keep, (7, 8, 9))):
        p.write_bytes(png_bytes(c))
    missing = d2 / "ghost.png"
    body = j(client.post("/api/files/delete", json={
        "paths": [str(m1), str(m2), str(missing)]}))
    check(body["ok"] and body["deleted"] == 2, "delete multi: two files deleted")
    check(body["failed"] == 1, "delete multi: missing file reported as a partial failure")
    check(not m1.exists() and not m2.exists(), "delete multi: both selected files gone")
    check(keep.exists() and keep.read_bytes() == png_bytes((7, 8, 9)),
          "delete multi: unselected file untouched")
    body = j(client.post("/api/files/delete", json={"paths": []}))
    check(body["ok"] is False, "delete: empty selection refused")

    section("3. Delete keeps group references consistent")
    d3 = BROWSE / "del_group"
    d3.mkdir()
    member = d3 / "member.png"
    other = d3 / "other.png"
    member.write_bytes(png_bytes((20, 20, 20)))
    other.write_bytes(png_bytes((21, 21, 21)))
    gid = j(client.post("/api/groups", json={"name": "DelGroup"}))["group"]["id"]
    j(client.post("/api/groups/add", json={"id": gid, "path": str(member)}))
    j(client.post("/api/groups/add", json={"id": gid, "path": str(other)}))
    detail = j(client.get("/api/groups/detail?id=%d" % gid))
    check(len(detail["group"]["members"]) == 2, "group has two members before delete")
    j(client.post("/api/files/delete", json={"paths": [str(member)]}))
    detail = j(client.get("/api/groups/detail?id=%d" % gid))
    names = {m["name"] for m in detail["group"]["members"]}
    check(names == {"other.png"},
          "deleted member dropped from the group; group + other member survive")
    check(detail["group"]["member_count"] == 1, "member_count reflects the deletion")

    # ------------------------------------------------------- clipboard ops
    section("4. Keyboard Copy / Cut / Paste use the real copy/move endpoints")
    src = BROWSE / "clip_src"
    src.mkdir()
    c1, c2, x1 = src / "c1.png", src / "c2.png", src / "x1.png"
    c1.write_bytes(png_bytes((30, 30, 30)))
    c2.write_bytes(png_bytes((31, 31, 31)))
    x1.write_bytes(png_bytes((32, 32, 32)))
    paste_dir = BROWSE / "clip_dest"
    paste_dir.mkdir()

    # Ctrl+C then Ctrl+V  -> copy keeps the originals
    body = j(client.post("/api/files/copy", json={"paths": [str(c1)], "target": str(paste_dir)}))
    check(body["ok"] and body["copied"] == 1, "Ctrl+C/Ctrl+V single: copied 1")
    check(c1.is_file() and (paste_dir / "c1.png").is_file(),
          "copy keeps the original and writes the copy")
    body = j(client.post("/api/files/copy", json={"paths": [str(c2)], "target": str(paste_dir)}))
    check(body["copied"] == 1, "Ctrl+C/Ctrl+V second file copied")

    # Ctrl+X then Ctrl+V -> cut moves the file (original removed)
    body = j(client.post("/api/files/move", json={"paths": [str(x1)], "target": str(paste_dir)}))
    check(body["ok"] and body["moved"] == 1, "Ctrl+X/Ctrl+V: moved 1")
    check(not x1.exists() and (paste_dir / "x1.png").is_file(),
          "cut removes the original and writes it to the paste target")

    # -------------------------------------------------------- batch rename
    section("5. Batch Rename: prefix/suffix + numbering/start/padding, ext kept")
    br = BROWSE / "batch"
    br.mkdir()
    b1, b2, b3 = br / "b1.png", br / "b2.png", br / "b3.png"
    b1.write_bytes(png_bytes((40, 0, 0)))
    b2.write_bytes(png_bytes((0, 40, 0)))
    b3.write_bytes(png_bytes((0, 0, 40)))
    body = j(client.post("/api/files/batch-rename", json={
        "paths": [str(b1), str(b2), str(b3)],
        "prefix": "ren_", "suffix": "_x", "numbering": True,
        "start": 5, "padding": 3,
    }))
    check(body["ok"] and body["renamed"] == 3, "batch rename multi: renamed == 3")
    produced = sorted(p.name for p in br.iterdir())
    check(produced == ["ren_005_x.png", "ren_006_x.png", "ren_007_x.png"],
          f"prefix+suffix+number+padding produced the expected names: {produced}")
    check(all(p.suffix == ".png" for p in br.iterdir()), "extension preserved on every file")
    check(not b1.exists() and not b2.exists() and not b3.exists(),
          "originals gone after the batch rename")

    section("5b. Batch Rename single file + numbering disabled + invalid name")
    solo = br / "solo.png"
    solo.write_bytes(png_bytes((50, 50, 50)))
    body = j(client.post("/api/files/batch-rename", json={
        "paths": [str(solo)], "prefix": "solo_", "numbering": True,
        "start": 1, "padding": 2,
    }))
    check(body["ok"] and body["renamed"] == 1 and (br / "solo_01.png").is_file(),
          "batch rename single file with padding works")

    lone = br / "orig.png"
    lone.write_bytes(png_bytes((60, 60, 60)))
    body = j(client.post("/api/files/batch-rename", json={
        "paths": [str(lone)], "prefix": "solo2", "suffix": "", "numbering": False,
    }))
    check(body["ok"] and body["renamed"] == 1 and (br / "solo2.png").is_file(),
          "numbering disabled: prefix only, extension preserved")

    empt = br / "empt.png"
    empt.write_bytes(png_bytes((61, 61, 61)))
    body = j(client.post("/api/files/batch-rename", json={
        "paths": [str(empt)], "prefix": "", "suffix": "", "numbering": False,
    }))
    check(body["ok"] and body["failed"] == 1 and empt.exists(),
          "empty resulting name refused (file left intact)")

    section("6. Batch Rename never overwrites a colliding target")
    col = BROWSE / "collision"
    col.mkdir()
    src_a = col / "a.png"
    clash = col / "clash_001.png"
    src_a.write_bytes(png_bytes((70, 70, 70)))
    clash.write_bytes(png_bytes((99, 99, 99)))
    clash_before = clash.read_bytes()
    body = j(client.post("/api/files/batch-rename", json={
        "paths": [str(src_a)], "prefix": "clash_", "numbering": True,
        "start": 1, "padding": 3,
    }))
    check(body["ok"] and body["failed"] == 1, "collision refused (failed == 1)")
    check(src_a.is_file() and src_a.read_bytes() == png_bytes((70, 70, 70)),
          "colliding source left untouched")
    check(clash.is_file() and clash.read_bytes() == clash_before,
          "existing target never overwritten (bytes identical)")

    section("7. Batch Rename keeps group membership (identity reconcile)")
    grp_dir = BROWSE / "batch_group"
    grp_dir.mkdir()
    gf = grp_dir / "gf.png"
    gf.write_bytes(png_bytes((80, 80, 80)))
    gid2 = j(client.post("/api/groups", json={"name": "BatchGroup"}))["group"]["id"]
    j(client.post("/api/groups/add", json={"id": gid2, "path": str(gf)}))
    body = j(client.post("/api/files/batch-rename", json={
        "paths": [str(gf)], "prefix": "g2_", "numbering": True,
        "start": 1, "padding": 2,
    }))
    check(body["ok"] and body["renamed"] == 1, "grouped file renamed")
    new_gf = grp_dir / "g2_01.png"
    check(new_gf.is_file(), "renamed grouped file exists on disk")
    for_path = j(client.get("/api/groups/for?path=" + str(new_gf)))
    check(for_path["ok"] and any(g["id"] == gid2 for g in for_path["groups"]),
          "renamed file still belongs to its group")
    detail = j(client.get("/api/groups/detail?id=%d" % gid2))
    check({m["name"] for m in detail["group"]["members"]} == {"g2_01.png"},
          "group detail shows the new (renamed) member path/name")

    # ------------------------------------------------------------ UI wiring
    section("8. UI ships the real Batch Rename dialog + Delete + clipboard wiring")
    html = _ui_html()
    for token in (
        # batch rename dialog (real, not a placeholder)
        'function openBatchRenameDialog',
        'function batchPlan',
        'function updateBatchPreview',
        'id="batchPreview"',
        'id="batchPrefix"',
        'id="batchSuffix"',
        'id="batchNumbering"',
        'id="batchStart"',
        'id="batchPad"',
        "'/api/files/batch-rename'",
        # delete
        "'/api/files/delete'",
        'function removeSelectedFiles',
        'data-act="remove"',
        "e.key === 'Delete'",
        # clipboard (internal state)
        'function setClipboard',
        'function pasteClipboard',
        'state.clipboard',
        "setClipboard('copy')",
        "setClipboard('cut')",
        'pasteClipboard()',
        "(e.key === 'c' || e.key === 'C')",
        "(e.key === 'x' || e.key === 'X')",
        "(e.key === 'v' || e.key === 'V')",
        # keyboard guards
        'function isEditableTarget',
        'function anyDialogVisible',
    ):
        check(token in html, f"UI wires {token}")

    check("navigator.clipboard" not in html,
          "file clipboard is Veyra internal state (no navigator.clipboard)")
    check("belum diimplementasikan" not in html,
          "no 'not implemented' placeholder text remains")
    check(html.count("function openBatchRenameDialog") == 1,
          "exactly one openBatchRenameDialog definition (no duplicate)")
    check(html.count("function apiRemoveFiles") == 1,
          "exactly one apiRemoveFiles definition (no duplicate)")

    # The shell is intentionally thin now; the served UI must reference every
    # modular asset so the browser loads the real logic from disk.
    served = client.get("/").data.decode("utf-8")
    for src in re.findall(r'<script\s+src="([^"]+)"', served):
        asset = client.get(src)
        check(asset.status_code == 200,
              f"served asset {src} -> HTTP {asset.status_code}")
    for href in re.findall(r'<link\s+rel="stylesheet"\s+href="([^"]+)"', served):
        asset = client.get(href)
        check(asset.status_code == 200,
              f"served stylesheet {href} -> HTTP {asset.status_code}")
    check("/api/files/batch-rename" in html and 'id="batchPreview"' in html,
          "UI ships the live Batch Rename dialog")
    check("/api/files/delete" in html and "removeSelectedFiles" in html,
          "UI ships the Delete action")

    # ------------------------------------------------------- JS syntax check
    section("9. Frontend JavaScript is syntactically valid (node --check)")
    node = shutil.which("node")
    if not node:
        check(False, "node executable available for the JS syntax check")
    else:
        shell = HTML_PATH.read_text(encoding="utf-8")
        script_srcs = re.findall(r'<script\s+src="([^"]+)"', shell)
        inline = re.findall(r"<script>(.*?)</script>", shell, re.S)
        check(bool(script_srcs), "index.html loads external module <script> files")
        check(len(re.findall(r"<script>", shell)) == 0
              and len(re.findall(r"<script\s+src=", shell)) == len(script_srcs),
              "index.html has no inline <script> logic left")
        js_files = []
        for src in script_srcs:
            rel = src.lstrip("/")
            if rel.startswith("static/"):
                rel = rel[len("static/"):]
            asset = STATIC_ROOT / rel
            check(asset.is_file(), f"module exists on disk: {src}")
            if asset.is_file():
                js_files.append(asset)
        ok = True
        for path in js_files:
            res = subprocess.run([node, "--check", str(path)],
                                 capture_output=True, text=True)
            if res.returncode != 0:
                ok = False
                print("    node --check %s failed:\n%s"
                      % (path.name, res.stderr.strip()[:400]))
        check(ok and bool(js_files), "every frontend module passes node --check (valid JavaScript)")

    # ---------------------------------------------------------------- summary
    print("\n" + "=" * 64)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("RESULT: ALL DELETE / CLIPBOARD / BATCH-RENAME CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    raise SystemExit(code)
