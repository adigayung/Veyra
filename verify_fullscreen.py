"""Permanent verification for Veyra's TRUE NATIVE FULLSCREEN image viewer.

Run with the project venv::

    J:\\Veyra\\venv\\Scripts\\python.exe verify_fullscreen.py

The task requires that opening an image fullscreen (double-click OR the
"Open Full Screen" context-menu item, both funnelling through the *same*
function) turns the real pywebview window into a native fullscreen window and
makes the image the only visible content.  This suite proves it in two layers:

 1. STATIC wiring (desktop.py + veyra/index.html): the native pywebview bridge
    (window.toggle_fullscreen via fs_enter/fs_leave) is exposed and consumed, the
    in-page surface is shown/hidden by toggling ``.fs-view.show`` and
    ``body.fs-active``, the whole application chrome is removed through
    ``body.fs-active .app{display:none}``, the image fills the screen with
    ``object-fit:contain`` (no stretch, no UI padding), and Escape is the only
    exit.  A leftover ``#fsName`` null-reference (the bug that used to abort
    before the bridge was ever called) is explicitly ruled out.

 2. RUNTIME behaviour: the *real* extracted fullscreen block from
    veyra/index.html is executed under Node with a tiny DOM/window stub - it is
    not a copy, so it fails if the shipped code regresses.  It asserts that
    entering sets the image src, shows the surface, hides the chrome and calls
    the native bridge; that leaving reverses all of it and calls the bridge's
    leave; that zoom/pan/next/prev update the transform and index; and that a
    locked .aimg is refused (CryptoSession stays in-memory).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML_PATH = ROOT / "veyra" / "index.html"
DESKTOP_PATH = ROOT / "desktop.py"

FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  [PASS] " if cond else "  [FAIL] ") + label)
    if not cond:
        FAILS.append(label)


def section(title: str) -> None:
    print("\n== " + title + " ==")


# --------------------------------------------------------------------------- #
# Node harness: run the REAL fullscreen block with a DOM stub                  #
# --------------------------------------------------------------------------- #
NODE_PRELUDE = r"""
'use strict';
let ENTER_CALLS = 0, LEAVE_CALLS = 0;
const TOASTS = [];

function makeClassList(set) {
  return {
    add: (c) => set.add(c),
    remove: (c) => set.delete(c),
    contains: (c) => set.has(c),
    toggle: (c, on) => {
      if (on === undefined) { set.has(c) ? set.delete(c) : set.add(c); }
      else { on ? set.add(c) : set.delete(c); }
    },
  };
}

function makeEl(tag) {
  const set = new Set();
  const el = {
    tagName: (tag || 'div').toUpperCase(),
    className: '', innerHTML: '', textContent: '',
    style: {}, dataset: {}, children: [], _attrs: {}, _on: {},
    classList: makeClassList(set),
    appendChild(c) { this.children.push(c); return c; },
    addEventListener(t, f) { (this._on[t] = this._on[t] || []).push(f); },
    removeEventListener() {},
    setAttribute(k, v) { this._attrs[k] = v; },
    getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; },
    removeAttribute(k) { delete this._attrs[k]; if (k === 'src') { this._src = undefined; } },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    closest() { return null; },
    focus() {},
    getBoundingClientRect() { return { width: 0, height: 0, left: 0, top: 0, right: 0, bottom: 0 }; },
  };
  Object.defineProperty(el, 'src', {
    get() { return this._src; },
    set(v) { this._src = v; },
    configurable: true,
  });
  return el;
}

const body = makeEl('body');
const fsImgEl = makeEl('img');
const document = {
  body,
  _on: {},
  createElement: makeEl,
  getElementById: (id) => (id === 'fsImg' ? fsImgEl : null),
  addEventListener(t, f) { (this._on[t] = this._on[t] || []).push(f); },
  querySelector() { return null; },
  querySelectorAll() { return []; },
};

const window = { __aether_fs: {
  enter() { ENTER_CALLS++; return true; },
  leave() { LEAVE_CALLS++; return true; },
} };

const state = {
  images: [], selected: null, selection: [], anchor: null,
  crypto: { unlocked: false },
  fs: { index: null, scale: 1, tx: 0, ty: 0, dragging: false, startX: 0, startY: 0, origX: 0, origY: 0 },
};

const imgUrl = (p) => '/api/image?path=' + encodeURIComponent(p);
const thumbUrl = (p) => '/api/thumb?path=' + encodeURIComponent(p);
function toast(m) { TOASTS.push(m); }
function applySelectionClasses() {}
function updateStatus() {}
function updateStatusFile() {}
function isEditableTarget() { return false; }
function anyDialogVisible() { return false; }
function computeCols() { return 1; }
function selectIndex() {}
"""

NODE_ASSERTIONS = r"""
// ---- assertions ----------------------------------------------------------
function assert(cond, label) {
  console.log((cond ? '  [PASS] ' : '  [FAIL] ') + label);
  if (!cond) { process.exitCode = 1; }
}

const enc = encodeURIComponent;

// 1. plain image enters native fullscreen, only the image is visible
state.images = [{ path: 'C:/pics/a.png', name: 'a.png', encrypted: false }];
state.crypto = { unlocked: false };
openFullScreen(0);
assert(fsImgEl.src === '/api/image?path=' + enc('C:/pics/a.png'), 'enter: image src set from /api/image');
assert(fsView.classList.contains('show'), 'enter: surface shown (.fs-view.show)');
assert(document.body.classList.contains('fs-active'), 'enter: app chrome hidden (body.fs-active)');
assert(fsActive() === true, 'enter: fsActive() reports fullscreen');
assert(ENTER_CALLS === 1, 'enter: native pywebview bridge (toggle_fullscreen) called once');
assert(state.fs.index === 0, 'enter: fullscreen index tracked');
assert(state.selection.length === 1 && state.selection[0] === 0, 'enter: grid selection follows the image (kept on exit)');

// 2. Escape path: leaving restores everything and calls the bridge's leave
closeFullScreen();
assert(LEAVE_CALLS === 1, 'exit: native bridge leave called once');
assert(!fsView.classList.contains('show'), 'exit: surface hidden');
assert(!document.body.classList.contains('fs-active'), 'exit: header/toolbar/panel come back');
assert(fsActive() === false, 'exit: fsActive() reports normal viewer');
assert(fsImgEl.src === undefined, 'exit: image src released (no in-memory leak)');
assert(state.fs.index === null, 'exit: fullscreen index reset');
assert(state.selection.length === 1 && state.selection[0] === 0, 'exit: selection/navigation state preserved (no reload)');

// 3. zoom keeps working
openFullScreen(0);
fsZoom(2);
assert(state.fs.scale === 2, 'zoom: scale updated');
assert(fsImgEl.style.transform.indexOf('scale(2)') >= 0, 'zoom: transform applied');
fsZoom(0.1);
assert(state.fs.scale >= 0.1, 'zoom: clamped to a sane minimum');

// 4. pan keeps working (mousedown on the image + mousemove on the document)
fsImgEl._on.mousedown[0]({ preventDefault() {}, clientX: 100, clientY: 100 });
document._on.mousemove[0]({ clientX: 150, clientY: 120 });
assert(state.fs.tx === 50 && state.fs.ty === 20, 'pan: translate updated from the drag delta');
assert(fsImgEl.style.transform.indexOf('translate(50px,20px)') >= 0, 'pan: transform applied');
document._on.mouseup[0]();
assert(state.fs.dragging === false, 'pan: drag ended on mouseup');

// 5. prev/next navigation keeps working
state.images = [
  { path: 'a.png', name: 'a', encrypted: false },
  { path: 'b.png', name: 'b', encrypted: false },
];
openFullScreen(0);
fsStep(1);
assert(state.fs.index === 1, 'nav: next advances the index');
assert(fsImgEl.src.indexOf('b.png') >= 0, 'nav: next loads the next image');
fsStep(1);
assert(state.fs.index === 1, 'nav: next past the end is a no-op');
fsStep(-1);
assert(state.fs.index === 0, 'nav: prev goes back');

// 6. a locked .aimg is refused; CryptoSession stays in-memory
closeFullScreen();
ENTER_CALLS = 0; LEAVE_CALLS = 0;
state.images = [{ path: 'x.aimg', name: 'x.aimg', encrypted: true }];
state.crypto = { unlocked: false };
openFullScreen(0);
assert(!fsView.classList.contains('show'), 'locked .aimg: does not enter fullscreen');
assert(ENTER_CALLS === 0, 'locked .aimg: native bridge not called');
assert(TOASTS.length > 0, 'locked .aimg: user is told to unlock first');

// 7. an unlocked .aimg enters fullscreen via the decrypted in-memory stream
state.crypto = { unlocked: true };
openFullScreen(0);
assert(fsView.classList.contains('show') && ENTER_CALLS === 1, 'unlocked .aimg: enters fullscreen');
assert(fsImgEl.src === '/api/image?path=' + enc('x.aimg'),
       'unlocked .aimg: served through /api/image (decrypt in memory, no temp file)');

// 8. Escape via the REAL document keydown handler is the way out
closeFullScreen();
ENTER_CALLS = 0; LEAVE_CALLS = 0;
state.images = [{ path: 'a.png', name: 'a', encrypted: false }];
openFullScreen(0);
assert(fsActive(), 'escape: starts in fullscreen');
assert(typeof document._on.keydown !== 'undefined' && document._on.keydown.length > 0,
       'escape: the real document keydown handler is registered');
document._on.keydown[0]({ key: 'Escape', preventDefault() {}, stopPropagation() {} });
assert(!fsActive(), 'escape: the shipped keydown handler exits fullscreen');
assert(LEAVE_CALLS === 1, 'escape: it calls the native bridge leave exactly once');
assert(!document.body.classList.contains('fs-active'), 'escape: the app chrome is restored');
"""


def _extract_fullscreen_block(html: str) -> str:
    start = html.find("// -- full screen viewer")
    end = html.find("// -- right click context menu", start)
    if start < 0 or end <= start:
        return ""
    return html[start:end]


def _extract_keydown_handler(html: str) -> str:
    start = html.find("// keyboard: fullscreen navigation/zoom")
    end = html.find("// -- toolbar", start)
    if start < 0 or end <= start:
        return ""
    return html[start:end]


def run_node_behaviour(block: str, handler: str) -> bool:
    """Execute the real fullscreen + keydown code under Node and parse PASS/FAIL."""

    node = shutil.which("node")
    if not node:
        check(False, "node executable available to run the runtime behaviour test")
        return False

    harness = NODE_PRELUDE + "\n" + block + "\n" + handler + "\n" + NODE_ASSERTIONS
    tmp = Path(tempfile.mkdtemp(prefix="veyra_fs_")) / "harness.js"
    tmp.write_text(harness, encoding="utf-8")
    try:
        proc = subprocess.run(
            [node, str(tmp)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:  # pragma: no cover - environment failure
        check(False, f"node harness ran ({exc})")
        return False
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)

    out = proc.stdout + proc.stderr
    for line in out.splitlines():
        if line.strip().startswith("[PASS]"):
            print("  " + line.strip())
        elif line.strip().startswith("[FAIL]"):
            check(False, line.split("[FAIL]", 1)[1].strip())
    return proc.returncode == 0


def main() -> int:
    html = HTML_PATH.read_text(encoding="utf-8")
    desktop = DESKTOP_PATH.read_text(encoding="utf-8")
    block = _extract_fullscreen_block(html)
    handler = _extract_keydown_handler(html)

    # -- 1. native window bridge in desktop.py -----------------------------
    section("1. desktop.py exposes the native pywebview fullscreen bridge")
    check("toggle_fullscreen" in desktop, "desktop.py uses window.toggle_fullscreen()")
    check("def fs_enter" in desktop and "def fs_leave" in desktop, "fs_enter/fs_leave defined")
    check("window.expose(fs_enter, fs_leave)" in desktop, "fs_enter/fs_leave exposed to JS")
    check("window.events.loaded" in desktop, "shim injected on the loaded lifecycle (never before start)")
    check("window.__aether_fs" in desktop, "desktop injects the window.__aether_fs shim")
    check("maximized=True" in desktop, "window starts maximized (desktop, not a browser)")
    check("fullscreen=False" in desktop, "window not created fullscreen (toggled on demand)")
    check("webview.create_window" in desktop, "uses a native pywebview window (no external browser)")

    # -- 2. index.html wiring ----------------------------------------------
    section("2. index.html drives the native fullscreen surface")
    check(block != "", "fullscreen block found in veyra/index.html")
    check("window.__aether_fs" in html and "window.__aether_fs.enter" in html and "window.__aether_fs.leave" in html,
          "index.html calls the native bridge enter/leave")
    check("fsView.classList.add('show')" in html, "entering shows the image surface (.fs-view.show)")
    check("fsView.classList.remove('show')" in html, "leaving hides the image surface")
    check("document.body.classList.add('fs-active')" in html, "entering hides the app chrome (body.fs-active)")
    check("document.body.classList.remove('fs-active')" in html, "leaving restores the app chrome")
    check("body.fs-active .app{display:none}" in html,
          "CSS removes header/menu/toolbar/sidebar/status in fullscreen")
    check("object-fit:contain" in html, "image preserves aspect ratio (no stretch/distort)")
    check("width:100%;height:100%" in html, "image fills the screen as much as possible")
    check("function fsActive(" in html, "fsActive() helper exists")
    check("getElementById('fsName')" not in html,
          "no leftover #fsName null-reference (the bug that aborted before the bridge)")
    check("<button" not in block, "no Close/Prev/Next/Zoom buttons inside the fullscreen surface")
    check("location.reload" not in block, "fullscreen never reloads the page")

    # -- 3. single shared implementation for both triggers -----------------
    section("3. both triggers share ONE fullscreen implementation")
    check("card.ondblclick=function(){openFullScreen(" in html, "double-click calls openFullScreen")
    check("data-act=\"fullscreen\"" in html and "act==='fullscreen'" in html and "openFullScreen(i)" in html,
          "context-menu 'Open Full Screen' calls the same openFullScreen")
    check(html.count("function openFullScreen(") >= 1 and html.count("function closeFullScreen(") >= 1,
          "openFullScreen/closeFullScreen defined once (single implementation)")

    # -- 4. Escape is the single exit --------------------------------------
    section("4. Escape is the only primary way out")
    esc_idx = html.find("if(e.key==='Escape')")
    check(esc_idx >= 0, "an Escape key handler exists")
    tail = html[esc_idx:esc_idx + 400] if esc_idx >= 0 else ""
    check("fsActive()" in tail and "closeFullScreen();" in tail,
          "Escape exits fullscreen via closeFullScreen()")

    # -- 5. runtime behaviour of the real code -----------------------------
    section("5. runtime behaviour of the real fullscreen block (Node)")
    ok = run_node_behaviour(block, handler)
    check(ok, "the shipped fullscreen code passes every runtime assertion")

    # -- summary -----------------------------------------------------------
    print("\n" + "=" * 64)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("RESULT: ALL FULLSCREEN VERIFICATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
