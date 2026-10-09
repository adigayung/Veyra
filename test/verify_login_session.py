"""Permanent verification for Veyra's centralized login + idle auto-lock.

Run with the project venv::

    J:\\Veyra\\venv\\Scripts\\python.exe verify_login_session.py

What it proves (the task's mandatory verification list)
-------------------------------------------------------
The suite drives the **real** Veyra backend (the same Flask app used by
``desktop.py``, on a throw-away key store / DB / browse folder) with **real
Chromium via Playwright**, plus direct HTTP checks for the crypto boundary:

 1. startup with no login      -> the login screen is shown (app body gated);
 2. correct password           -> Veyra opens;
 3. wrong password             -> stays locked, never enters the app;
 4. Encrypt while the session is active asks for NO second password;
 5. Decrypt while the session is active asks for NO second password;
 6. ``.aimg`` can be previewed while the session is unlocked;
 7. 5 minutes of no keyboard/mouse -> automatic session lock;
 8. after auto-lock            -> the login password screen returns;
 9. ``.aimg`` is refused while locked (403, even with stale UI state);
10. login again               -> the session unlocks and the app is usable;
11. keyboard/mouse activity before the timeout RESETS the idle timer;
12. no console errors and no failed security request;
13. the existing AIMG regression suite (``verify_aimg.py``) still passes.

The 5-minute timeout is real: the backend default is 300 s and the test asserts
that number.  To keep the run short, *elapsed idle* is simulated by winding the
module-level activity clock back in time
(``veyra.web.routes._session_activity_ts``) — the shipped UI polling code and
the shipped backend timeout logic run completely unmodified.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

PASSWORD = "Veyra-Login-Test-5Min!2"

ROOT = Path(__file__).resolve().parent
WORK = Path(tempfile.mkdtemp(prefix="veyra_login_"))
BROWSE = WORK / "browse"
BROWSE.mkdir(parents=True, exist_ok=True)

os.environ["VEYRA_KEYSTORE"] = str(WORK / "keystore.bin")
os.environ["VEYRA_DEFAULT_PATH"] = str(BROWSE)
os.environ["VEYRA_DB"] = str(WORK / "veyra.db")

sys.path.insert(0, str(ROOT))

FAILS: list[str] = []


def check(cond, label: str) -> None:
    print(("  [PASS] " if cond else "  [FAIL] ") + label)
    if not cond:
        FAILS.append(label)


def section(title: str) -> None:
    print("\n== " + title + " ==")


# --------------------------------------------------------------------------- #
# tiny PNG helper (no Pillow needed for the browse listing)                     #
# --------------------------------------------------------------------------- #
def _png(rgb) -> bytes:
    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    w = h = 8
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * w
    idat = zlib.compress(row * h, 9)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


# --------------------------------------------------------------------------- #
# backend: real Flask app served by a real WSGI server on a local port          #
# --------------------------------------------------------------------------- #
class Server:
    def __init__(self) -> None:
        from veyra.application import create_app
        from werkzeug.serving import make_server

        self.app = create_app()
        self._server = make_server("127.0.0.1", 0, self.app._flask_app,
                                   threaded=True)
        self.port = self._server.server_port
        self.url = f"http://127.0.0.1:{self.port}/"
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        try:
            self._server.shutdown()
        except Exception:  # noqa: BLE001
            pass


def http(method: str, url: str, payload: dict | None = None):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def http_json(method: str, url: str, payload: dict | None = None):
    status, body = http(method, url, payload)
    try:
        return status, json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeError):
        return status, {}


# --------------------------------------------------------------------------- #
# Playwright helpers                                                            #
# --------------------------------------------------------------------------- #
class ConsoleWatcher:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.failed_security: list[str] = []
        self._prefixes = ("/api/aimg", "/api/session")

    def attach(self, page) -> None:
        page.on("console", self._on_console)
        page.on("pageerror", self._on_pageerror)
        page.on("requestfailed", self._on_failed)

    def _on_console(self, msg) -> None:
        if msg.type == "error":
            self.errors.append(msg.text)

    def _on_pageerror(self, exc) -> None:
        self.errors.append(f"pageerror: {exc}")

    def _on_failed(self, request) -> None:
        url = request.url or ""
        if any(p in url for p in self._prefixes):
            self.failed_security.append(url)


def login_screen_visible(page) -> bool:
    return page.evaluate(
        "() => { var o = document.getElementById('loginOverlay');"
        " return !!o && !o.classList.contains('hidden'); }"
    )


def wait_login_hidden(page, timeout_s: float = 15.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not login_screen_visible(page):
            return True
        time.sleep(0.1)
    return not login_screen_visible(page)


def wait_login_visible(page, timeout_s: float = 15.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if login_screen_visible(page):
            return True
        time.sleep(0.1)
    return login_screen_visible(page)


def type_login(page, password: str) -> None:
    page.fill("#loginPass", password)
    page.click("#loginBtn")


def login(page, password: str) -> None:
    type_login(page, password)
    wait_login_hidden(page)


def force_idle(seconds: float) -> None:
    """Pretend the app has been idle for ``seconds`` (no key/mouse activity)."""
    import veyra.web.routes as routes

    routes._session_activity_ts = time.monotonic() - float(seconds)


def app_still_alive(page) -> bool:
    """The explorer/grid remain mounted behind the login overlay."""
    return page.evaluate(
        "() => !!document.querySelector('.app') && !!document.getElementById('grid')"
    )


# The shipped app keeps its state inside a module IIFE, so the test drives the
# UI through real DOM interaction instead of reaching into private JS objects:
# clicking a card selects the file, and the context menu is opened with a real
# right-click on that card.
def card_locator(page, name: str):
    return page.locator("#grid .card", has_text=name).first


def select_by_name(page, name: str) -> None:
    card = card_locator(page, name)
    card.wait_for(state="visible", timeout=15000)
    card.click()
    page.wait_for_timeout(200)


def open_crypto(page, op: str) -> None:
    """Open Tools -> Encrypt/Decrypt for the current selection (real UI path)."""
    label = "Encrypt" if op == "encrypt" else "Decrypt"
    # Prefer the shipped context-menu entry; fall back to the Tools modal.
    card = page.locator("#grid .card.selected").first
    card.click(button="right")
    page.wait_for_timeout(200)
    item = page.locator(f".ctx-item:has-text('{label}')").first
    if item.count() and item.is_visible():
        item.click()
        return
    # Fallback: the toolbar Tools button opens the crypto dialog directly.
    page.click("#btnAimg")
    page.wait_for_timeout(200)


def confirm_crypto_dialog(page) -> None:
    dialog = page.locator(".modal-dialog[data-key=\"cryptofiles\"]")
    dialog.locator("[data-dlg-ok]").click()


def refresh_folder(page) -> None:
    page.click("#refreshBtn")
    page.wait_for_timeout(900)


# --------------------------------------------------------------------------- #
def main() -> int:  # noqa: C901 - a linear verification script is intentional
    # -- fixtures ----------------------------------------------------------
    section("0. Fixtures (real files on disk)")
    plain = BROWSE / "plain.png"
    plain.write_bytes(_png((10, 120, 200)))
    secret = BROWSE / "secret.png"
    secret.write_bytes(_png((200, 40, 40)))
    check(plain.is_file() and secret.is_file(), "plain + secret image created")

    # Create the vault with the centralized password, then lock it again so the
    # browser genuinely starts from a locked session (the startup case).
    from veyra.security.crypto_session import CryptoService
    from veyra.services.aimg_service import AimgService

    aimg_svc = AimgService(CryptoService(Path(os.environ["VEYRA_KEYSTORE"])))
    aimg_svc.unlock(PASSWORD)                     # bootstraps the vault
    aimg_svc.encrypt_file(secret)                 # -> secret.aimg
    aimg_svc.lock()
    secret_aimg = BROWSE / "secret.aimg"
    check(secret_aimg.is_file(), "secret.aimg created by the existing crypto")
    check(not aimg_svc.is_unlocked(), "crypto session locked before the UI starts")

    server = Server()
    server.start()
    base = server.url.rstrip("/")

    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    watcher = ConsoleWatcher()
    watcher.attach(page)

    try:
        # ---------------------------------------------------------------- 1
        section("1. Startup without login -> login screen is shown")
        page.goto(server.url, wait_until="load")
        page.wait_for_selector("#loginOverlay", timeout=15000)
        check(login_screen_visible(page), "login overlay is visible at startup")
        check(page.is_visible("#loginPass"), "password input is visible (no username)")
        check(page.evaluate("() => !document.getElementById('loginUser')"),
              "there is no username field")
        # The app body boots behind the overlay but is gated by it.
        check(app_still_alive(page), "app body mounted behind the login overlay")
        status, body = http_json("GET", base + "/api/aimg/status")
        check(body.get("unlocked") is False, "backend crypto session locked at startup")

        # ---------------------------------------------------------------- 3
        section("3. Wrong password -> stays locked, never enters the app")
        type_login(page, "definitely-wrong")
        page.wait_for_timeout(1200)
        check(login_screen_visible(page), "login overlay still shown after wrong password")
        check(page.inner_text("#loginStatus") != "", "an error message is shown")
        check(page.input_value("#loginPass") != "", "password field keeps focus for a retry")
        status, body = http_json("GET", base + "/api/aimg/status")
        check(body.get("unlocked") is False, "crypto session still locked (wrong password)")
        r = http("GET", base + f"/api/image?path={secret_aimg}")
        check(r[0] == 403, "locked .aimg preview refused (HTTP 403)")

        # ---------------------------------------------------------------- 2
        section("2. Correct password -> Veyra opens")
        login(page, PASSWORD)
        check(not login_screen_visible(page), "login overlay hidden after correct password")
        check(page.evaluate("() => document.getElementById('loginPass').value === ''"),
              "password is NOT kept in the DOM after login")
        status, body = http_json("GET", base + "/api/aimg/status")
        check(body.get("unlocked") is True, "crypto session unlocked with the login password")
        # The grid listing loaded (the app is usable).
        page.wait_for_timeout(600)
        check(page.evaluate("() => document.querySelectorAll('#grid .card, #grid article').length >= 1"),
              "image grid rendered after login")

        # ---------------------------------------------------------------- 6
        section("6. .aimg preview while the session is unlocked")
        r = http("GET", base + f"/api/image?path={secret_aimg}")
        check(r[0] == 200 and r[1][:4] == b"\x89PNG", ".aimg served (decrypted in memory) when unlocked")
        r = http("GET", base + f"/api/thumb?path={secret_aimg}")
        check(r[0] == 200, ".aimg thumbnail served when unlocked")

        # ---------------------------------------------------------------- 4/5
        section("4/5. Encrypt/Decrypt while active asks for NO second password")
        # Select the plain image exactly like the UI does (by clicking its card
        # text/name through the shipped grid selection helpers), then open the
        # Encrypt modal through the real context-menu code path.
        select_by_name(page, "plain.png")
        open_crypto(page, "encrypt")
        page.wait_for_timeout(300)
        check(page.evaluate("() => !!document.querySelector('.modal-dialog[data-key=\"cryptofiles\"]')"),
              "Encrypt modal opened")
        check(page.evaluate("() => !document.getElementById('cryptoFilesPass')"),
              "Encrypt modal has NO password field while the session is active")
        confirm_crypto_dialog(page)
        page.wait_for_timeout(2000)
        check((BROWSE / "plain.aimg").is_file(),
              "Encrypt ran with only the login password (plain.aimg created)")

        # Now decrypt it again (select the .aimg file).
        refresh_folder(page)
        select_by_name(page, "plain.aimg")
        open_crypto(page, "decrypt")
        page.wait_for_timeout(300)
        check(page.evaluate("() => !document.getElementById('cryptoFilesPass')"),
              "Decrypt modal has NO password field while the session is active")
        confirm_crypto_dialog(page)
        page.wait_for_timeout(2000)
        check(plain.is_file() and not (BROWSE / "plain.aimg").exists(),
              "Decrypt ran with only the login password (plain.png restored)")

        # --------------------------------------------------------------- 11
        section("11. Activity before the timeout RESETS the idle timer")
        import veyra.web.routes as routes

        # Pretend 4 minutes elapsed, then send real activity; the clock resets.
        force_idle(240)
        check(routes.seconds_idle() >= 239, "idle clock wound back to ~4 minutes")
        status, body = http_json("GET", base + "/api/session/status")
        check(body.get("should_lock") is False, "no auto-lock at 4 minutes (timeout is 5 minutes)")
        check(body.get("idle_timeout") == 300, "backend timeout is exactly 300 s (5 minutes)")
        # Real keyboard activity through the page (listener -> heartbeat).
        page.keyboard.press("Shift")
        page.mouse.move(400, 400)
        page.wait_for_timeout(600)
        status, body = http_json("GET", base + "/api/session/status")
        check(body.get("idle_seconds", 999) < 60,
              f"keyboard/mouse activity reset the idle clock (idle={body.get('idle_seconds')})")
        check(body.get("unlocked") is True, "session still unlocked after the reset")

        # ---------------------------------------------------------------- 7
        section("7. 5 minutes idle -> automatic session lock")
        force_idle(301)   # > 300 s
        status, body = http_json("GET", base + "/api/session/status")
        check(body.get("should_lock") is True, "backend reports should_lock after 5 minutes idle")
        # The UI polls /api/session/status and locks on its own (no user action).
        check(wait_login_visible(page, timeout_s=15), "UI auto-locked and re-showed the login screen")
        check(app_still_alive(page), "app body was NOT destroyed by the auto-lock")
        status, body = http_json("GET", base + "/api/aimg/status")
        check(body.get("unlocked") is False, "CryptoSession really locked after auto-lock")

        # ---------------------------------------------------------------- 9
        section("9. .aimg cannot be accessed while locked")
        r = http("GET", base + f"/api/image?path={secret_aimg}")
        check(r[0] == 403, "locked .aimg preview refused (HTTP 403)")
        r = http("GET", base + f"/api/thumb?path={secret_aimg}")
        check(r[0] == 403, "locked .aimg thumb refused (HTTP 403)")
        status, body = http_json("POST", base + "/api/aimg/encrypt-files",
                                 {"paths": [str(plain)]})
        check(status == 403, "encrypt-files refused while locked (403)")
        status, body = http_json("POST", base + "/api/aimg/decrypt-files",
                                 {"paths": [str(secret_aimg)]})
        check(status == 403, "decrypt-files refused while locked (403)")

        # ---------------------------------------------------------------- 8
        section("8. After auto-lock the login screen returns (and is usable)")
        check(login_screen_visible(page), "login overlay visible after the auto-lock")
        # wrong password on the auto-lock screen keeps it locked
        type_login(page, "still-wrong")
        page.wait_for_timeout(1000)
        check(login_screen_visible(page), "wrong password after auto-lock keeps it locked")

        # --------------------------------------------------------------- 10
        section("10. Login again -> session unlocks and the app is usable")
        login(page, PASSWORD)
        check(not login_screen_visible(page), "login overlay hidden after re-login")
        status, body = http_json("GET", base + "/api/aimg/status")
        check(body.get("unlocked") is True, "session unlocked again with the SAME password")
        r = http("GET", base + f"/api/image?path={secret_aimg}")
        check(r[0] == 200, ".aimg preview works again after re-login")
        page.wait_for_timeout(600)
        check(page.evaluate("() => document.querySelectorAll('#grid .card, #grid article').length >= 1"),
              "image grid usable after re-login")

        # --------------------------------------------------------------- 12
        section("12. No console errors / no failed security requests")
        # Give the polling loop a couple of cycles before reading the buffer.
        page.wait_for_timeout(2500)
        check(not watcher.errors,
              "no console errors" + (f": {watcher.errors[:3]}" if watcher.errors else ""))
        check(not watcher.failed_security,
              "no failed /api/aimg or /api/session requests"
              + (f": {watcher.failed_security[:3]}" if watcher.failed_security else ""))

        # --------------------------------------------------------------- 13
        section("13. Existing AIMG regression suite still passes")
        proc = subprocess.run([sys.executable, str(ROOT / "verify_aimg.py")],
                              capture_output=True, text=True, timeout=300)
        tail = (proc.stdout or "") + (proc.stderr or "")
        check("ALL VERIFICATION CHECKS PASSED" in tail,
              "verify_aimg.py reports all checks passed")
    finally:
        try:
            context.close()
            browser.close()
            playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        server.stop()

    print("\n" + "=" * 64)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("RESULT: ALL LOGIN / SESSION / IDLE-LOCK CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    raise SystemExit(code)
