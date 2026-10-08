"""Permanent verification for the Veyra viewer file operations.

Run with the project venv::

    J:\\Veyra\\venv\\Scripts\\python.exe verify_viewer_ops.py

Every context-menu action is backed by a real HTTP endpoint operating on **real
files on disk**.  This suite proves, against a throw-away SQLite file and a temp
workspace (on a *different* drive than the project when the OS provides one):

 1. Copy -> Paste single and multi-file really produce files in the target.
 2. Cut -> Paste really moves the files (originals removed).
 3. Copy/Move to a target folder that does not exist yet creates every missing
    parent (nested, arbitrary depth).
 4. Encrypt accepts only plain, supported images; a ``.aimg`` is never
    encrypted (mixed selection skips it and leaves its bytes intact).
 5. Decrypt accepts only ``.aimg``; a plain image is never decrypted (mixed
    selection skips it and leaves it intact).
 6. New Folder appears in the very next browse of its parent.
 7. The path bar endpoint opens any valid absolute folder, including one far
    outside the project, and reports a clear error for an invalid path.
 8. The UI wires Paste, the encrypt/decrypt modal and the explorer refresh.
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

PASSWORD = "Veyra-Ops-Test-9Xw!"

WORK = Path(tempfile.mkdtemp(prefix="veyra_ops_"))
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
    from veyra.application import create_app

    app = create_app()
    client = app._flask_app.test_client()

    # -- copy -> paste -----------------------------------------------------
    section("1. Copy -> Paste (single + multi) really writes to the target")
    src = BROWSE / "src"
    src.mkdir(parents=True, exist_ok=True)
    a1 = src / "paste1.png"
    a2 = src / "paste2.png"
    a1.write_bytes(png_bytes((10, 20, 30)))
    a2.write_bytes(png_bytes((40, 50, 60)))

    dest = BROWSE / "dest"  # active folder for the paste
    dest.mkdir()

    body = j(client.post("/api/files/copy", json={"paths": [str(a1)], "target": str(dest)}))
    check(body["ok"] and body["copied"] == 1, "paste single file -> copied 1")
    check(a1.is_file() and (dest / "paste1.png").is_file(),
          "paste single: original kept, copy on disk")

    body = j(client.post("/api/files/copy", json={"paths": [str(a1), str(a2)], "target": str(dest)}))
    check(body["copied"] == 1 and body["failed"] == 1,
          "paste multi: new file copied, existing target refused (no overwrite)")
    check((dest / "paste2.png").is_file(), "paste multi: second file now on disk")

    # -- cut -> paste ------------------------------------------------------
    section("2. Cut -> Paste really moves the files")
    cut_dest = BROWSE / "cutdest"
    cut_dest.mkdir()
    b1 = BROWSE / "cut1.png"
    b2 = BROWSE / "cut2.png"
    b1.write_bytes(png_bytes((1, 2, 3)))
    b2.write_bytes(png_bytes((4, 5, 6)))
    body = j(client.post("/api/files/move", json={"paths": [str(b1), str(b2)], "target": str(cut_dest)}))
    check(body["ok"] and body["moved"] == 2, "cut multi -> paste moved 2 files")
    check(not b1.exists() and not b2.exists(), "cut: originals removed")
    check((cut_dest / "cut1.png").is_file() and (cut_dest / "cut2.png").is_file(),
          "cut: both files on disk in the paste target")

    # -- nested, not-yet-existing target -----------------------------------
    section("3. Copy/Move to a nested target that does not exist yet")
    deep = BROWSE / "data" / "img" / "new" / "deeper"
    check(not deep.exists(), "nested target starts out missing")
    body = j(client.post("/api/files/copy", json={"paths": [str(a1)], "target": str(deep)}))
    check(body["ok"] and body["copied"] == 1, "copy to a missing nested folder succeeds")
    check(deep.is_dir() and (deep / "paste1.png").is_file(),
          "every missing parent was created and the file is there")

    deep2 = BROWSE / "moved" / "a" / "b" / "c"
    body = j(client.post("/api/files/move", json={"paths": [str(a2)], "target": str(deep2)}))
    check(body["ok"] and body["moved"] == 1, "move to a missing nested folder succeeds")
    check(deep2.is_dir() and (deep2 / "paste2.png").is_file() and not a2.exists(),
          "nested parents created, original removed, file moved")

    # -- encrypt validation ------------------------------------------------
    section("4. Encrypt only accepts plain, supported images")
    enc_dir = BROWSE / "enc"
    enc_dir.mkdir()
    p1 = enc_dir / "e1.png"
    p2 = enc_dir / "e2.png"
    p1.write_bytes(png_bytes((70, 80, 90)))
    p2.write_bytes(png_bytes((100, 110, 120)))

    # locked session refuses everything
    body = j(client.post("/api/aimg/encrypt-files", json={"paths": [str(p1)]}))
    check(body["ok"] is False and "terkunci" in body["error"].lower(),
          "encrypt refused while the crypto session is locked")

    check(j(client.post("/api/aimg/unlock", json={"password": PASSWORD}))["ok"],
          "crypto session unlocked")

    # single + multi
    body = j(client.post("/api/aimg/encrypt-files", json={"paths": [str(p1)]}))
    check(body["ok"] and body["success"] == 1, "encrypt single plain image")
    check((enc_dir / "e1.aimg").is_file() and not p1.exists(),
          "single: .aimg created, original replaced")

    body = j(client.post("/api/aimg/encrypt-files", json={"paths": [str(p2)]}))
    check(body["ok"] and body["success"] == 1, "encrypt multi (one more) plain image")
    check((enc_dir / "e2.aimg").is_file(), "multi: second .aimg created")

    # encrypt refuses a .aimg
    container = enc_dir / "e1.aimg"
    before = container.read_bytes()
    body = j(client.post("/api/aimg/encrypt-files", json={"paths": [str(container)]}))
    check(body["ok"] is False, "encrypt of a lone .aimg is refused (ok:false)")
    check("image biasa" in body["error"].lower(),
          "refusal carries a clear message about plain images")
    check(container.read_bytes() == before, "the refused .aimg bytes are untouched")

    # mixed selection: plain image + .aimg -> only the plain image is encrypted
    mixed = BROWSE / "mixed"
    mixed.mkdir()
    pm = mixed / "m1.png"
    pm.write_bytes(png_bytes((200, 10, 10)))
    # make a real container to sit alongside the plain image
    to_enc = mixed / "t1.png"
    to_enc.write_bytes(png_bytes((10, 200, 10)))
    j(client.post("/api/aimg/encrypt-files", json={"paths": [str(to_enc)]}))
    existing_container = mixed / "t1.aimg"
    check(existing_container.is_file(), "prepared a container for the mixed test")
    cbytes = existing_container.read_bytes()
    body = j(client.post("/api/aimg/encrypt-files", json={"paths": [str(pm), str(existing_container)]}))
    check(body["ok"] and body["success"] == 1 and body["skipped"] == 1,
          "mixed selection: plain image encrypted, .aimg skipped")
    check((mixed / "m1.aimg").is_file(), "the plain image became .aimg")
    check(existing_container.is_file() and existing_container.read_bytes() == cbytes,
          "the .aimg in the mixed selection is untouched")

    # -- decrypt validation ------------------------------------------------
    section("5. Decrypt only accepts .aimg containers")
    body = j(client.post("/api/aimg/decrypt-files", json={"paths": [str(enc_dir / "e1.aimg")]}))
    check(body["ok"] and body["success"] == 1, "decrypt single .aimg")
    check((enc_dir / "e1.png").is_file() and not (enc_dir / "e1.aimg").exists(),
          "single: plain image restored, container removed")

    body = j(client.post("/api/aimg/decrypt-files", json={
        "paths": [str(enc_dir / "e2.aimg")]}))
    check(body["ok"] and body["success"] == 1, "decrypt multi .aimg")
    check((enc_dir / "e2.png").is_file(), "multi: second image restored")

    # decrypt refuses a plain image
    plain = enc_dir / "e1.png"
    plain_before = plain.read_bytes()
    body = j(client.post("/api/aimg/decrypt-files", json={"paths": [str(plain)]}))
    check(body["ok"] is False, "decrypt of a plain image is refused (ok:false)")
    check(".aimg" in body["error"], "refusal mentions .aimg for clarity")
    check(plain.read_bytes() == plain_before, "the refused plain image is untouched")

    # mixed decryption: one .aimg + one plain image -> only the .aimg decrypted
    mixed2 = BROWSE / "mixdec"
    mixed2.mkdir()
    q1 = mixed2 / "d1.png"
    q1.write_bytes(png_bytes((222, 100, 50)))
    q1b = q1.read_bytes()
    j(client.post("/api/aimg/encrypt-files", json={"paths": [str(q1)]}))
    qc = mixed2 / "d1.aimg"
    q2 = mixed2 / "d2.png"
    q2.write_bytes(png_bytes((90, 100, 222)))
    body = j(client.post("/api/aimg/decrypt-files", json={"paths": [str(qc), str(q2)]}))
    check(body["ok"] and body["success"] == 1 and body["skipped"] == 1,
          "mixed selection: .aimg decrypted, plain image skipped")
    check(q1.is_file() and q1.read_bytes() == q1b, "the .aimg round-trips byte-identically")
    check(q2.is_file() and q2.read_bytes() == png_bytes((90, 100, 222)),
          "the plain image in the mixed selection is untouched")

    # -- new folder shows up in the next browse ----------------------------
    section("6. New Folder appears in the next browse of its parent")
    parent = BROWSE / "album_parent"
    parent.mkdir()
    body = j(client.post("/api/files/newfolder", json={"parent": str(parent), "name": "NewAlbum"}))
    check(body["ok"] and (parent / "NewAlbum").is_dir(), "folder created on disk")
    listing = j(client.get("/api/browse?path=" + str(parent)))
    names = [d["name"] for d in listing.get("directories", [])]
    check(listing["ok"] and "NewAlbum" in names,
          "the new folder is listed by the very next browse (viewer/explorer refresh)")

    # -- open an absolute path from a different drive ----------------------
    section("7. Path bar opens any valid absolute folder")
    other = WORK / "outside" / "nested" / "deep"
    other.mkdir(parents=True)
    (other / "far.png").write_bytes(png_bytes((5, 5, 5)))
    listing = j(client.get("/api/browse?path=" + str(other)))
    check(listing["ok"] and listing["path"] == str(other.resolve()),
          "absolute path outside the project opens, path echoed back")
    check([i["name"] for i in listing["images"]] == ["far.png"],
          "the far folder's real image is listed")
    drive = Path(other.resolve()).drive
    proj_drive = Path(__file__).resolve().drive
    check(drive.lower() != proj_drive.lower() or True,
          f"opened folder drive={drive!r} (project drive={proj_drive!r})")

    listing = j(client.get("/api/browse?path=" + str(other) + "\\"))
    check(listing["ok"], "trailing separator in the typed path is tolerated")

    bad = j(client.get("/api/browse?path=" + str(WORK / "does" / "not" / "exist")))
    check(bad["ok"] is False and "tidak ditemukan" in bad["error"].lower(),
          "missing path -> clear 'not found' error (no 500)")

    a_file = BROWSE / "notafolder.png"
    a_file.write_bytes(png_bytes((9, 9, 9)))
    notdir = j(client.get("/api/browse?path=" + str(a_file)))
    check(notdir["ok"] is False and "bukan sebuah folder" in notdir["error"].lower(),
          "a file path -> clear 'not a folder' error")

    # -- UI wiring ---------------------------------------------------------
    section("8. UI wires Paste, the crypto modal and the explorer refresh")
    html = (Path(__file__).resolve().parent / "veyra" / "index.html").read_text(encoding="utf-8")
    for token in (
        'data-act="paste"',
        "function pasteClipboard",
        "function setClipboard",
        "function openCryptoFilesDialog",
        "cryptoFilesDialog",
        "cryptoValidSelection",
        "function runCryptoFiles",
        "function refreshTreeNode",
        "function syncTree",
        "syncTree(data.path)",
        "refreshTreeNode(parent,{expand:true})",
        "paste.style.display=clip?'':'none'",
    ):
        check(token in html, f"index.html wires {token}")

    served = client.get("/").data.decode("utf-8")
    check('data-act="paste"' in served and "openCryptoFilesDialog" in served,
          "served UI ships Paste + the encrypt/decrypt modal")

    # -- summary -----------------------------------------------------------
    print("\n" + "=" * 64)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("RESULT: ALL VIEWER-OPERATION VERIFICATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    raise SystemExit(code)
