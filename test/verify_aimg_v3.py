"""Verify the AIMG v3 display path end-to-end (real image + real UI).

Checks the task's mandatory list:
  1. encrypt a v3 .aimg and reopen it with the SAME password (bytes match);
  2. the image AND its thumbnail really render in the Veyra UI (Chromium);
  3. a DIFFERENT password still fails to open it;
  4. a legacy v2 container is reported as migration_required.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import urllib.request
import urllib.parse
import urllib.error
import zlib
import struct
from pathlib import Path

WORK = Path(r"J:\Veyra\test\_v3verify")
shutil.rmtree(WORK, ignore_errors=True)
BROWSE = WORK / "browse"
BROWSE.mkdir(parents=True, exist_ok=True)

os.environ["VEYRA_KEYSTORE"] = str(WORK / "keystore.bin")
os.environ["VEYRA_DEFAULT_PATH"] = str(BROWSE)
os.environ["VEYRA_DB"] = str(WORK / "veyra.db")
os.environ["VEYRA_IDLE_TIMEOUT"] = "0"

REAL_FOLDER = Path(r"J:\Program AndroidX\b\ai\StableDiffusion\face\knl\Revi Lia")
PASSWORD = "correct-horse"
WRONG = "battery-staple"

FAILS: list[str] = []


def check(cond, label):
    print(("  [PASS] " if cond else "  [FAIL] ") + label)
    if not cond:
        FAILS.append(label)


def _png(rgb):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    w = h = 8
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * w
    idat = zlib.compress(row * h, 9)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# ---------------------------------------------------------------- fixtures
real = next((p for p in REAL_FOLDER.iterdir() if p.suffix.lower() in (".jpg", ".jpeg"))
            if REAL_FOLDER.is_dir() else None, None)
if real is not None:
    shutil.copy2(real, BROWSE / "real.jpg")
    src_name = "real.jpg"
else:
    (BROWSE / "real.png").write_bytes(_png((200, 40, 40)))
    src_name = "real.png"
original = (BROWSE / src_name).read_bytes()

# legacy v2 container (must stay migration_required)
meta = {"version": 2, "ext": ".png", "salt": "00" * 16, "nonce": "11" * 12,
        "tnonce": "", "thumb": 0, "w": 8, "h": 8}
hb = json.dumps(meta, separators=(",", ":"), sort_keys=True).encode()
(BROWSE / "legacy.aimg").write_bytes(b"AIMG" + len(hb).to_bytes(4, "big") + hb + b"\x00" * 40)

from veyra.application import create_app
from werkzeug.serving import make_server
from veyra.security.crypto_session import CryptoService
from veyra.services.aimg_service import AimgService

app = create_app()
server = make_server("127.0.0.1", 0, app._flask_app, threaded=True)
port = server.server_port
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"


def http_get(path):
    try:
        with urllib.request.urlopen(base + path, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def http_post_json(path, obj):
    req = urllib.request.Request(
        base + path, data=json.dumps(obj).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, json.loads(r.read())


# ---------------------------------------------------- backend round trip
print("\n== 1. v3 round trip (same password) ==")
if not (WORK / "keystore.bin").exists():
    CryptoService(WORK / "keystore.bin").unlock(PASSWORD)  # bootstrap the vault
svc = AimgService(CryptoService(WORK / "keystore.bin"))
svc.unlock(PASSWORD)
enc = svc.encrypt_file(BROWSE / src_name)
check(enc.suffix == ".aimg", "encrypt -> .aimg")
data, mime = svc.payload(enc)
check(data == original, "same-password payload == original bytes")
check(svc.thumbnail(enc) is not None, "embedded thumbnail decrypts")

print("\n== 3. wrong password fails ==")
svc2 = AimgService(CryptoService(WORK / "keystore.bin"))
svc2.unlock(WRONG)
try:
    svc2.payload(enc)
    check(False, "wrong password must NOT decrypt")
except Exception as exc:  # noqa: BLE001
    check(type(exc).__name__ == "AimgAuthError", f"wrong password refused ({type(exc).__name__})")

print("\n== 4. legacy v2 -> migration_required ==")
st, body = http_get("/api/image?path=" + urllib.parse.quote(str(BROWSE / "legacy.aimg")))
check(st == 409 and json.loads(body).get("status") == "migration_required",
      f"v2 image -> 409 migration_required (got {st})")

print("\n== 2. HTTP: image + thumb served when unlocked ==")
ust, ubody = http_post_json("/api/aimg/unlock", {"password": PASSWORD})
check(ust == 200 and ubody.get("unlocked") is True,
      f"HTTP login unlocks the crypto session (got {ust})")
st, body = http_get("/api/image?path=" + urllib.parse.quote(str(enc)))
check(st == 200 and body == original, f"/api/image 200 + exact bytes (got {st})")
st, body = http_get("/api/thumb?path=" + urllib.parse.quote(str(enc)))
check(st == 200 and len(body) > 0, f"/api/thumb 200 (got {st})")

# ------------------------------------------------------------ real UI
print("\n== 2b. UI: thumbnail + full image render ==")
http_post_json("/api/session/lock", {})  # browser starts from a locked session
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(base + "/", wait_until="load")
    page.wait_for_selector("#loginOverlay", timeout=15000)
    page.fill("#loginPass", PASSWORD)
    page.click("#loginBtn")
    page.wait_for_timeout(2500)

    enc_url = "/api/thumb?path=" + urllib.parse.quote(str(enc))
    thumb = page.evaluate("""(s) => {
        const img = [...document.querySelectorAll('.thumb img')]
            .find(i => decodeURIComponent(i.getAttribute('src')) === decodeURIComponent(s));
        return img ? {w: img.naturalWidth, h: img.naturalHeight} : null;
    }""", enc_url)
    check(bool(thumb) and thumb["w"] > 0, f"grid thumbnail rendered (got {thumb})")

    page.dblclick(".card .thumb img")
    page.wait_for_timeout(2000)
    fs = page.evaluate("""() => {
        const im = document.getElementById('fsImg');
        return {w: im.naturalWidth, h: im.naturalHeight};
    }""")
    check(fs["w"] > 0 and fs["h"] > 0, f"full image rendered in viewer (got {fs})")
    browser.close()

server.shutdown()
shutil.rmtree(WORK, ignore_errors=True)
for junk in ("_xproc", "_uisrv", "_uisrv2", "_uisrv3"):
    shutil.rmtree(Path(r"J:\Veyra\test") / junk, ignore_errors=True)

print("\n" + "=" * 60)
if FAILS:
    print(f"RESULT: {len(FAILS)} FAILURE(S)")
    for f in FAILS:
        print("  - " + f)
    raise SystemExit(1)
print("RESULT: ALL AIMG v3 CHECKS PASSED")
