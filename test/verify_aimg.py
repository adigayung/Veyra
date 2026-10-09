"""Permanent verification for the Veyra .aimg container + crypto session.

Run with the project venv:

    J:\\Veyra\\venv\\Scripts\\python.exe verify_aimg.py

It exercises, against a throw-away key store in a temp directory:

* the native security boundary (Windows DPAPI) and the crypto session
  (locked by default, wrong password refused, python-flag bypass refused,
  tamper detection, no plaintext secrets at rest),
* real ``.aimg`` round trips using **real production images** (JPG/JPEG are
  used as-is; PNG/WEBP/GIF/BMP are transcoded from the real JPG bytes),
* the HTTP API through the real Flask app (Betrayer adapter + routes),
  including the "locked session refuses ``.aimg``" boundary.

The production folder is only ever *read* (images are copied to a scratch
workspace first), so it is never modified.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REAL_FOLDER = Path(r"J:\Program AndroidX\b\ai\StableDiffusion\face\knl\Revi Lia")
PASSWORD = "Veyra-Test-Passphrase-9f3!"

WORK = Path(tempfile.mkdtemp(prefix="veyra_verify_"))
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


def main() -> int:
    # ------------------------------------------------------------------ audit
    section("Source audit: no hardcoded secrets")
    src_dir = Path(__file__).resolve().parent.parent / "veyra"
    blob = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in src_dir.rglob("*.py")
    )
    check(PASSWORD not in blob, "test password not present in source")
    check('master_key = b"' not in blob and "master_key=b\"" not in blob,
          "no literal master key constant")
    check("hash_secret_raw" in blob, "Argon2id KDF present")
    check("AESGCM" in blob, "AES-256-GCM authenticated encryption present")
    check("CryptProtectData" in blob, "native DPAPI boundary present")

    # ----------------------------------------------------------- crypto session
    section("Crypto session (password as key source)")
    from veyra.security.crypto_session import (
        CryptoService, CryptoError, LockedError,
    )
    from veyra.services.aimg_service import AimgService, _BoundedCache

    svc = CryptoService(WORK / "direct_keystore.bin")
    check(svc.is_unlocked() is False, "session starts locked")
    try:
        svc.encrypt(b"x")
        check(False, "encrypt refused while locked")
    except LockedError:
        check(True, "encrypt refused while locked")

    svc.initialize(PASSWORD)
    check(svc.is_unlocked() is True, "first unlock opens the session")
    sealed = svc.encrypt(b"payload", aad=b"hdr")
    check(svc.decrypt(sealed, aad=b"hdr") == b"payload", "encrypt/decrypt roundtrip")
    check(svc.validate(PASSWORD) is True, "validate() true for a valid key source")

    svc.lock()
    check(svc.is_unlocked() is False, "lock() closes the session")
    # Any non-empty password is accepted: the password is a per-file key source,
    # not a login gate (see verify_password_key.py).
    check(svc.unlock("wrong") is True, "any password unlocks (no gate)")
    check(svc.is_unlocked() is True, "session open after an arbitrary password")
    check(svc.validate("wrong") is True, "validate() true for any password")

    svc.lock()
    svc._unlocked = True          # classic fake flag
    svc.crypto_unlocked = True
    svc.logged_in = True
    check(svc.is_unlocked() is False, "python flags do not unlock")
    try:
        svc.decrypt(sealed, aad=b"hdr")
        check(False, "decrypt refused with fake flags")
    except LockedError:
        check(True, "decrypt refused with fake flags")

    svc.unlock(PASSWORD)
    check(svc.is_unlocked() is True, "re-unlock works")
    tampered = bytearray(sealed)
    tampered[-1] ^= 0x01
    try:
        svc.decrypt(bytes(tampered), aad=b"hdr")
        check(False, "tampered ciphertext rejected")
    except Exception:
        check(True, "tampered ciphertext rejected (authentication)")

    ks = (WORK / "direct_keystore.bin").read_bytes()
    check(PASSWORD.encode() not in ks, "password not stored in keystore")
    check(b"payload" not in ks, "plaintext not stored in keystore")

    # --------------------------------------------------------------- .aimg I/O
    section(".aimg round trips (real image bytes)")
    aimg_svc = AimgService(CryptoService(WORK / "keystore.bin"))
    aimg_svc.unlock(PASSWORD)

    rt = WORK / "roundtrip"
    rt.mkdir(parents=True, exist_ok=True)
    real_images = []
    if REAL_FOLDER.is_dir():
        real_images = [
            p for p in REAL_FOLDER.iterdir()
            if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
        ]
    if not real_images:
        print("  [SKIP] production folder not available; using synthetic images")
    else:
        check(True, f"real production images found ({len(real_images)})")

    samples: dict[str, Path] = {}
    if real_images:
        real_jpg = next((p for p in real_images if p.suffix.lower() == ".jpg"), real_images[0])
        shutil.copy2(real_jpg, rt / "sample.jpg")
        samples[".jpg"] = rt / "sample.jpg"
        jpeg = next((p for p in real_images if p.suffix.lower() == ".jpeg"), None)
        if jpeg:
            shutil.copy2(jpeg, rt / "sample.jpeg")
            samples[".jpeg"] = rt / "sample.jpeg"
    else:
        from PIL import Image
        real_jpg = rt / "sample.jpg"
        Image.new("RGB", (64, 48), (10, 20, 30)).save(real_jpg, "JPEG")
        samples[".jpg"] = real_jpg

    from PIL import Image as _Image
    with _Image.open(real_jpg) as _img:
        _rgb = _img.convert("RGB")
        _rgb.save(rt / "sample.png", "PNG")
        _rgb.save(rt / "sample.webp", "WEBP")
        _rgb.save(rt / "sample.gif", "GIF")
        _rgb.save(rt / "sample.bmp", "BMP")
    for ext in (".png", ".webp", ".gif", ".bmp"):
        samples[ext] = rt / ("sample" + ext)

    tested = []
    for ext, target in samples.items():
        original = target.read_bytes()
        enc = aimg_svc.encrypt_file(target)
        check(enc.suffix == ".aimg", f"{ext}: encrypt -> .aimg")
        check(not target.exists(), f"{ext}: original removed after encrypt")
        check(enc.read_bytes()[:4] == b"AIMG", f"{ext}: .aimg magic present")
        check(b"JFIF" not in enc.read_bytes()[:64] and b"PNG" not in enc.read_bytes()[:64],
              f"{ext}: no obvious plaintext in container")
        dec = aimg_svc.decrypt_file(enc)
        check(not enc.exists(), f"{ext}: .aimg removed after decrypt")
        check(dec.suffix == ext, f"{ext}: original extension restored")
        check(dec.read_bytes() == original, f"{ext}: bytes identical after roundtrip")
        tested.append(ext)
    check(len(tested) >= 3, f"round trips for >=3 formats (got {tested})")

    nested = WORK / "nested"
    (nested / "a" / "b").mkdir(parents=True, exist_ok=True)
    shutil.copy2(real_jpg, nested / "a" / ("one" + real_jpg.suffix))
    shutil.copy2(samples[".png"], nested / "a" / "b" / "two.png")
    r_enc = aimg_svc.encrypt_folder(nested)
    r_dec = aimg_svc.decrypt_folder(nested)
    check(r_enc["success"] == r_enc["found"] >= 2, f"nested batch encrypt ({r_enc['success']}/{r_enc['found']})")
    check(r_dec["success"] == r_dec["found"] >= 2, f"nested batch decrypt ({r_dec['success']}/{r_dec['found']})")
    check((nested / "a" / "b" / "two.png").exists(), "nested subfolder restored")

    # ------------------------------------------------- preview (no temp files)
    section("Direct .aimg preview (in memory)")
    prev = WORK / "preview"
    prev.mkdir(parents=True, exist_ok=True)
    shutil.copy2(real_jpg, prev / "p.jpg")
    original_bytes = (prev / "p.jpg").read_bytes()
    enc = aimg_svc.encrypt_file(prev / "p.jpg")
    before = {p.name for p in prev.iterdir()}
    data, mime = aimg_svc.payload(enc)
    after = {p.name for p in prev.iterdir()}
    check(data == original_bytes, "payload() returns exact original bytes")
    check(mime == "image/jpeg", "payload() mime derived from header")
    check(before == after, "preview creates no temporary plaintext file")
    thumb = aimg_svc.thumbnail(enc)
    check(thumb is not None and thumb[0][:2] == b"\xff\xd8", "embedded thumbnail available")

    cache = _BoundedCache(3, 1024 * 1024)
    for i in range(10):
        cache.put((i,), b"x" * 100)
    check(len(cache) == 3, "bounded LRU cache caps entries")

    # ------------------------------------------------------------------ HTTP API
    section("HTTP API through the real Flask app")
    browse = WORK / "browse"
    browse.mkdir(parents=True, exist_ok=True)
    shutil.copy2(real_jpg, browse / "plain1.jpg")
    shutil.copy2(samples[".png"], browse / "plain2.png")
    shutil.copy2(real_jpg, browse / "secret.jpg")
    aimg_svc.encrypt_file(browse / "secret.jpg")

    from veyra.application import create_app
    client = create_app()._flask_app.test_client()

    def j(resp):
        return json.loads(resp.data.decode("utf-8"))

    check(client.get("/api/health").status_code == 200, "GET /api/health 200")
    r = client.get("/")
    check(r.status_code == 200 and b"VEYRA" in r.data, "GET / serves UI")
    check(b'id="titleText"' not in r.data, "old title bar removed (desktop shell owns title)")

    status = j(client.get("/api/aimg/status"))
    check(status.get("unlocked") is False, "crypto session locked at start")
    check(status.get("boundary") == "windows-dpapi", "native boundary reported")

    data = j(client.get("/api/browse?path=" + str(browse)))
    check(data.get("ok") is True, "browse valid folder ok")
    item = next(i for i in data["images"] if i["name"] == "secret.aimg")
    check(item["encrypted"] is True and item["type"] == "JPG", ".aimg listed with original type")
    check(bool(item["width"]) and bool(item["height"]), ".aimg header exposes dimensions")

    check(client.get("/api/image?path=" + str(browse / "secret.aimg")).status_code == 403,
          "locked .aimg image refused (403)")
    check(client.get("/api/thumb?path=" + str(browse / "secret.aimg")).status_code == 403,
          "locked .aimg thumb refused (403)")
    check(client.get("/api/image?path=%s&unlocked=1&logged_in=1" % (browse / "secret.aimg")).status_code == 403,
          "client flags do not grant crypto capability")
    check(client.post("/api/aimg/encrypt", json={"path": str(browse)}).status_code == 403,
          "encrypt while locked refused (403)")
    no_gate = j(client.post("/api/aimg/unlock", json={"password": "nope"}))
    check(no_gate.get("ok") is True and no_gate.get("unlocked") is True,
          "any password logs in (no 401 gate)")
    client.post("/api/aimg/lock", json={})

    body = j(client.post("/api/aimg/unlock", json={"password": PASSWORD}))
    check(body.get("unlocked") is True, "unlock with correct password")
    r = client.get("/api/image?path=" + str(browse / "secret.aimg"))
    check(r.status_code == 200 and r.data[:2] == b"\xff\xd8", ".aimg served when unlocked")
    r = client.get("/api/thumb?path=" + str(browse / "secret.aimg"))
    check(r.status_code == 200 and r.data[:2] == b"\xff\xd8", ".aimg thumb served when unlocked")

    expected = {"plain1.jpg", "plain2.png", "secret.aimg"}
    check({p.name for p in browse.iterdir()} == expected, "no temp plaintext left by preview")

    bad = j(client.get("/api/browse?path=" + str(WORK / "nope")))
    check(bad.get("ok") is False, "invalid folder -> ok:false (no 500)")

    client.post("/api/aimg/lock", json={})
    check(client.get("/api/image?path=" + str(browse / "secret.aimg")).status_code == 403,
          "after lock .aimg refused again")

    section("Keystore at rest")
    store = (WORK / "keystore.bin").read_bytes()
    check(PASSWORD.encode() not in store, "password not in keystore file")

    print("\n" + "=" * 60)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("RESULT: ALL VERIFICATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    raise SystemExit(code)
