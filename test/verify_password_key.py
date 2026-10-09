"""Permanent verification for the "password as key source" AIMG model.

Run with the project venv (from the project root):

    venv\\Scripts\\python.exe test\\verify_password_key.py

It verifies, against a throw-away workspace (temp key store / folder / DB):
  * login accepts ANY password (no gate, never a 401 based on the password),
  * the per-file key is derived from the password
    (HKDF(Argon2id(password, dsalt), file_salt, info)) and Argon2id runs only
    once per derivation salt (cached),
  * the new v3 header carries the derivation salt (portable file),
  * a different password cannot open a file (AES-GCM auth failure -> locked),
  * the old v2 (master key) container is refused on the normal path and can
    only be converted through the explicit migration API,
  * auto-lock / lock wipes every bit of session key material,
  * no password / key material is written to the key store or the database.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Isolate every side effect in a temp workspace BEFORE importing the app.
WORK = Path(tempfile.mkdtemp(prefix="veyra_pwkey_"))
os.environ["VEYRA_KEYSTORE"] = str(WORK / "keystore.bin")
os.environ["VEYRA_DEFAULT_PATH"] = str(WORK / "browse")
os.environ["VEYRA_DB"] = str(WORK / "veyra.db")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASSWORD_A = "admin123"
PASSWORD_B = "MasterOk"
LEGACY_PASSWORD = "legacy-pass-2019"

FAILS: list[str] = []


def check(cond, label: str) -> None:
    print(("  [PASS] " if cond else "  [FAIL] ") + label)
    if not cond:
        FAILS.append(label)


def section(title: str) -> None:
    print("\n== " + title + " ==")


def make_image(path: Path, color=(120, 30, 200)) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (48, 36), color).save(path)


def read_header(path: Path) -> dict:
    raw = Path(path).read_bytes()
    assert raw[:4] == b"AIMG", "not an aimg container"
    length = int.from_bytes(raw[4:8], "big")
    return json.loads(raw[8:8 + length].decode("utf-8"))


def build_legacy_v1_keystore(path: Path, legacy_password: str, master_key: bytes) -> None:
    """Write an old (v1, wrapped master key + verifier) key store document."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from veyra.security import native_backend
    from veyra.security.crypto_session import _KEYSTORE_AAD, _argon2_key, _verifier
    from veyra.security.keystore import KeyStore

    salt = native_backend.random_bytes(16)
    nonce = native_backend.random_bytes(12)
    kek = _argon2_key(legacy_password, salt, {})
    wrapped = AESGCM(kek).encrypt(nonce, master_key, _KEYSTORE_AAD)
    document = {
        "format": "veyra-keystore",
        "version": 1,
        "kdf": {
            "algo": "argon2id",
            "time_cost": 3,
            "memory_cost": 64 * 1024,
            "parallelism": 2,
            "hash_len": 32,
        },
        "salt": salt.hex(),
        "nonce": nonce.hex(),
        "wrapped_master_key": wrapped.hex(),
        "verifier": _verifier(master_key).hex(),
    }
    KeyStore(path).save(document)


def build_legacy_v2_aimg(path: Path, master_key: bytes, payload: bytes,
                         ext: str = ".jpg") -> None:
    """Write an old (v2, master-key keyed) .aimg container."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    from veyra.security import native_backend
    from veyra.services.aimg_service import MAGIC, _PAYLOAD_INFO_V2

    salt = native_backend.random_bytes(16)
    nonce = native_backend.random_bytes(12)
    meta = {
        "version": 2,
        "ext": ext,
        "salt": salt.hex(),
        "nonce": nonce.hex(),
        "tnonce": "",
        "thumb": 0,
        "w": 0,
        "h": 0,
        "alg": "AES-256-GCM",
        "kdf": "HKDF-SHA256",
    }
    header_bytes = json.dumps(meta, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=salt, info=_PAYLOAD_INFO_V2
    ).derive(master_key)
    payload_ct = AESGCM(payload_key).encrypt(nonce, payload, header_bytes)
    blob = MAGIC + len(header_bytes).to_bytes(4, "big") + header_bytes + payload_ct
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)


def main() -> int:
    # --------------------------------------------------------------- audit
    section("Source audit: no hardcoded secrets")
    src = Path(__file__).resolve().parent.parent / "veyra"
    blob = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in src.rglob("*.py")
    )
    check(PASSWORD_B not in blob and PASSWORD_A not in blob,
          "test passwords not present in source")
    check("hash_secret_raw" in blob, "Argon2id KDF present")
    check("AESGCM" in blob, "AES-256-GCM authenticated encryption present")

    from veyra.security.crypto_session import (
        CryptoService,
        CryptoError,
        LockedError,
        _argon2_key,
    )
    from veyra.services.aimg_service import (
        AimgAuthError,
        AimgMigrationRequired,
        AimgService,
        MAGIC,
        SCHEME,
        VERSION,
        _PAYLOAD_INFO,
    )

    # ------------------------------------------------- 1. login gate removed
    section("1. Login accepts ANY password (no gate)")
    svc = CryptoService(WORK / "direct1.bin")
    check(svc.is_unlocked() is False, "session starts locked")
    check(svc.unlock("ngawur123") is True, "unlock('ngawur123') returns True")
    check(svc.is_unlocked() is True, "session unlocked with an arbitrary password")
    svc.lock()
    check(svc.is_unlocked() is False, "lock() closes the session")
    svc._unlocked = True            # classic fake flag
    svc.crypto_unlocked = True
    check(svc.is_unlocked() is False, "python flags do not unlock")
    try:
        svc.unlock("")
        check(False, "empty password rejected")
    except CryptoError:
        check(True, "empty password rejected")
    # A fresh password on an already initialised store is always accepted.
    check(svc.unlock("another-totally-different") is True,
          "a second, different password also unlocks")
    # Migration without a legacy (v1) store must fail cleanly (CryptoError, not
    # an unexpected exception that would become an HTTP 500).
    try:
        svc.unwrap_legacy_master_key("whatever")
        check(False, "migration without a legacy store is refused")
    except CryptoError:
        check(True, "migration without a legacy store is refused (CryptoError)")

    # --------------------------------------- 2. password key derivation/cache
    section("2. Per-file key = HKDF(Argon2id(password, dsalt), file_salt, info)")
    svc2 = CryptoService(WORK / "direct2.bin")
    svc2.unlock(PASSWORD_B)
    dsalt = svc2.derivation_salt
    check(len(dsalt) == 16, "keystore exposes a 16 byte derivation salt")
    pk = svc2.password_key_for(dsalt)
    expected = _argon2_key(PASSWORD_B, dsalt, svc2._kdf_params)
    check(pk == expected, "password_key == Argon2id(password, dsalt)")

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    fsalt = b"\x01" * 16
    sub = svc2.derive_subkey(dsalt, fsalt, _PAYLOAD_INFO)
    want = HKDF(algorithm=hashes.SHA256(), length=32, salt=fsalt,
                info=_PAYLOAD_INFO).derive(pk)
    check(sub == want, "derive_subkey == HKDF(password_key, file_salt, info)")

    check(svc2.argon2_derivations == 1, "Argon2id ran exactly once on unlock")
    for _ in range(50):
        svc2.password_key_for(dsalt)
    check(svc2.argon2_derivations == 1, "password_key_for is cached per dsalt")

    # --------------------------------------------- 3. v3 container + opener
    section("3. AIMG v3 (password keyed) round trip + header portability")
    vault_b = CryptoService(WORK / "vault_b.bin")
    aimg_b = AimgService(vault_b)
    aimg_b.unlock(PASSWORD_B)

    src_img = WORK / "browse_b" / "photo.jpg"
    make_image(src_img)
    original = src_img.read_bytes()
    enc = aimg_b.encrypt_file(src_img)
    check(enc.suffix == ".aimg", "encrypt -> .aimg")
    header = read_header(enc)
    check(header.get("version") == VERSION, f"header version == {VERSION}")
    check(header.get("scheme") == SCHEME, f"header scheme == {SCHEME!r}")
    check(len(header.get("dsalt", "")) == 32, "header carries the 16 byte dsalt (hex)")
    check(header.get("dsalt") == vault_b.derivation_salt.hex(),
          "header dsalt matches the vault derivation salt")
    check(aimg_b.payload(enc)[0] == original, "payload decrypts to the exact bytes")
    thumb = aimg_b.thumbnail(enc)
    check(thumb is not None and thumb[0][:2] == b"\xff\xd8", "embedded thumbnail available")

    # AC7: opening many files/thumbnails must not re-run Argon2id.
    before = vault_b.argon2_derivations
    many = WORK / "browse_b" / "many"
    for i in range(8):
        make_image(many / f"p{i}.png", color=(i * 10, 40, 90))
    for i in range(8):
        aimg_b.encrypt_file(many / f"p{i}.png")
    for i in range(8):
        aimg_b.payload(many / f"p{i}.aimg")
        aimg_b.thumbnail(many / f"p{i}.aimg")
    check(vault_b.argon2_derivations == before,
          "many encryptions/reads do not re-run Argon2id (cache per dsalt)")

    # ----------------------------------------- 4. wrong password cannot open
    section("4. A different password cannot open the file (locked)")
    vault_b.lock()
    check(vault_b.is_unlocked() is False, "session locked")
    aimg_b.unlock(PASSWORD_A)
    check(vault_b.is_unlocked() is True, "logged in with the 'wrong' password")
    try:
        aimg_b.payload(enc)
        check(False, "payload read refused for a mismatching password")
    except AimgAuthError:
        check(True, "payload read refused for a mismatching password")
    try:
        aimg_b.thumbnail(enc)
        check(False, "thumbnail read refused for a mismatching password")
    except AimgAuthError:
        check(True, "thumbnail read refused for a mismatching password")

    # ----------------------------------------------- 5. portability (vault C)
    section("5. Header is portable (new vault, same password)")
    vault_c = CryptoService(WORK / "vault_c.bin")   # different derivation salt
    aimg_c = AimgService(vault_c)
    aimg_c.unlock(PASSWORD_B)
    check(vault_c.derivation_salt != vault_b.derivation_salt,
          "vault C has a different derivation salt")
    check(aimg_c.payload(enc)[0] == original,
          "file from vault B opens in vault C with the same password")
    vault_c.lock()
    aimg_c.unlock(PASSWORD_A)
    try:
        aimg_c.payload(enc)
        check(False, "file from vault B refuses the wrong password in vault C")
    except AimgAuthError:
        check(True, "file from vault B refuses the wrong password in vault C")

    # --------------------------------------------- 6. legacy v2 needs migration
    section("6. Legacy v2 (master key) refused + explicit migration")
    from veyra.security import native_backend

    master_key = native_backend.random_bytes(32)
    build_legacy_v1_keystore(WORK / "keystore.bin", LEGACY_PASSWORD, master_key)

    legacy_file = WORK / "browse" / "legacy.aimg"
    payload = b"\xff\xd8\xff\xe0-legacy-image-bytes-" + bytes(range(64))
    build_legacy_v2_aimg(legacy_file, master_key, payload, ext=".jpg")
    check(legacy_file.read_bytes()[:4] == b"AIMG", "legacy v2 container written")

    from veyra.application import create_app
    client = create_app()._flask_app.test_client()

    def j(resp):
        return json.loads(resp.data.decode("utf-8"))

    # wrong/any password must NEVER be answered with 401
    for passwd in ["ngawur123", PASSWORD_A, "x", "  spaced  "]:
        r = client.post("/api/aimg/unlock", json={"password": passwd})
        check(r.status_code == 200 and j(r).get("ok") is True
              and j(r).get("unlocked") is True,
              f"unlock({passwd!r}) -> 200 ok/unlocked (no 401)")

    # the v1 store can no longer be read on the normal path -> 403 while locked
    # (here we are unlocked) -> migration_required
    r = client.get("/api/image?path=" + str(legacy_file))
    check(r.status_code == 409 and j(r).get("status") == "migration_required",
          "legacy v2 image -> 409 migration_required")
    r = client.get("/api/thumb?path=" + str(legacy_file))
    check(r.status_code == 409 and j(r).get("status") == "migration_required",
          "legacy v2 thumb -> 409 migration_required")

    # --------------------------------------------- 7. AC2 password match works
    section("7. Encrypt/open with the matching password (AC2)")
    client.post("/api/aimg/unlock", json={"password": PASSWORD_B})
    plain = WORK / "browse" / "secret.jpg"
    make_image(plain, color=(10, 200, 40))
    plain_bytes = plain.read_bytes()
    r = j(client.post("/api/aimg/encrypt-files", json={"paths": [str(plain)]}))
    secret = WORK / "browse" / "secret.aimg"
    check(r.get("ok") and r.get("success") == 1 and secret.is_file(),
          "encrypt-files produced secret.aimg")
    r = client.get("/api/image?path=" + str(secret))
    check(r.status_code == 200 and r.data == plain_bytes,
          "/api/image 200 with the matching password")
    r = client.get("/api/thumb?path=" + str(secret))
    check(r.status_code == 200, "/api/thumb 200 with the matching password")

    # --------------------------------------------- 8. AC3 mismatching password
    section("8. Open with a different password -> locked (AC3)")
    client.post("/api/aimg/unlock", json={"password": PASSWORD_A})
    r = client.get("/api/image?path=" + str(secret))
    check(r.status_code == 422 and j(r).get("status") == "locked",
          "/api/image mismatching password -> 422 locked")
    r = client.get("/api/thumb?path=" + str(secret))
    check(r.status_code == 422 and j(r).get("status") == "locked",
          "/api/thumb mismatching password -> 422 locked")
    listing = j(client.get("/api/browse?path=" + str(WORK / "browse")))
    check(listing.get("ok") is True, "grid listing still works (navigation intact)")
    check(any(i["name"] == "secret.aimg" and i["encrypted"] for i in listing["images"]),
          "locked .aimg still listed (grid not broken)")

    # --------------------------------------------- 9. AC4 auto/manual lock
    section("9. Lock wipes session material (AC4)")
    r = j(client.post("/api/session/lock", json={}))
    check(r.get("unlocked") is False, "session lock -> unlocked False")
    check(j(client.get("/api/aimg/status")).get("unlocked") is False,
          "crypto status reports locked")
    r = client.get("/api/image?path=" + str(secret))
    check(r.status_code == 403, "locked .aimg refused (403) after lock")
    client.post("/api/aimg/unlock", json={"password": PASSWORD_A})
    r = client.get("/api/image?path=" + str(secret))
    check(r.status_code == 422, "re-login with a different password: still locked")

    # --------------------------------------------- 10. AC5 migrate one sample
    section("10. Migrate one legacy sample (AC5)")
    r = j(client.post("/api/aimg/migrate", json={
        "path": str(legacy_file),
        "legacy_password": LEGACY_PASSWORD,
        "target_password": PASSWORD_A,
    }))
    check(r.get("ok") is True and r.get("success") == 1, "sample migration ok")
    header = read_header(legacy_file)
    check(header.get("version") == VERSION, "migrated file is v3")
    check(header.get("dsalt", "") != "", "migrated file carries a dsalt")
    r = client.get("/api/image?path=" + str(legacy_file))
    check(r.status_code == 200 and r.data == payload,
          "migrated file reads back with its target password")
    client.post("/api/aimg/unlock", json={"password": PASSWORD_B})
    r = client.get("/api/image?path=" + str(legacy_file))
    check(r.status_code == 422, "migrated file refuses another password")

    # a wrong legacy password must not corrupt/convert anything
    bad = WORK / "browse" / "legacy2.aimg"
    build_legacy_v2_aimg(bad, master_key, payload, ext=".jpg")
    r = j(client.post("/api/aimg/migrate", json={
        "path": str(bad),
        "legacy_password": "not-the-legacy-pass",
        "target_password": PASSWORD_B,
    }))
    check(r.get("ok") is False, "wrong legacy password -> refused")
    check(read_header(bad).get("version") == 2, "un-migrated v2 file left intact")

    # migrating an already-current (v3) file is refused cleanly (no 500)
    r = j(client.post("/api/aimg/migrate", json={
        "path": str(secret),
        "legacy_password": LEGACY_PASSWORD,
        "target_password": PASSWORD_B,
    }))
    check(r.get("ok") is False, "migrating an already-v3 file is refused cleanly")
    check(read_header(secret).get("version") == VERSION, "v3 file left intact")

    # --------------------------------------------- 11. folder batch migration
    section("11. Batch migration (folder) + sample-first default")
    folder = WORK / "browse" / "legacy_dir"
    for name in ("a.aimg", "b.aimg", "c.aimg"):
        build_legacy_v2_aimg(folder / name, master_key, payload + name.encode(),
                             ext=".jpg")
    client.post("/api/aimg/unlock", json={"password": PASSWORD_A})
    sample = j(client.post("/api/aimg/migrate", json={
        "path": str(folder),
        "legacy_password": LEGACY_PASSWORD,
        "target_password": PASSWORD_A,
    }))
    check(sample.get("sample") is True and sample.get("success") == 1,
          "folder default migrates one sample only")
    leftover = [p.name for p in folder.glob("*.aimg")
                if read_header(p).get("version") == 2]
    check(len(leftover) == 2, "two legacy files remain after the sample run")
    full = j(client.post("/api/aimg/migrate", json={
        "path": str(folder),
        "legacy_password": LEGACY_PASSWORD,
        "target_password": PASSWORD_A,
        "batch": True,
    }))
    check(full.get("ok") is True and full.get("success") == 2
          and full.get("sample") is False,
          "batch run migrates the remaining files")
    migrated = [read_header(p).get("version") for p in folder.glob("*.aimg")]
    check(migrated == [VERSION, VERSION, VERSION], "all folder files now v3")
    for p in folder.glob("*.aimg"):
        r = client.get("/api/image?path=" + str(p))
        check(r.status_code == 200, f"migrated {p.name} reads back")

    # --------------------------------------------- 12. no plaintext at rest
    section("12. No password / key material at rest (R8)")
    store = (WORK / "keystore.bin").read_bytes()
    legacy_store = (WORK / "keystore.legacy.bin").read_bytes()
    for secret_val in (PASSWORD_A, PASSWORD_B, LEGACY_PASSWORD):
        check(secret_val.encode() not in store, f"'{secret_val}' not in keystore")
        check(secret_val.encode() not in legacy_store,
              f"'{secret_val}' not in legacy keystore")
    db_file = WORK / "veyra.db"
    if db_file.is_file():
        db = db_file.read_bytes()
        for secret_val in (PASSWORD_A, PASSWORD_B, LEGACY_PASSWORD):
            check(secret_val.encode() not in db, f"'{secret_val}' not in database")

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
