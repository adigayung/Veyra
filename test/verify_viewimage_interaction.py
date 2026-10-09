"""Permanent verification for Veyra's View-Image *interaction* behaviour.

Run with the project venv::

    J:\\Veyra\\venv\\Scripts\\python.exe verify_viewimage_interaction.py

This suite locks in the interaction spec of the opened image view:

* OPEN -> ACTUAL SIZE (native 100%), never fit-to-screen / fit-width.
* a single LEFT click toggles ACTUAL SIZE <-> FIT WIDTH.
* mouse wheel DOWN -> next image, wheel UP -> previous image (walking the ACTIVE
  ordered dataset: Normal/Group filter -> Sort By -> ordered list), and the next
  image opens again at ACTUAL SIZE (no zoom/pan bleed).
* ``+`` / ``-`` zoom in/out gradually while preserving the aspect ratio.
* a DOUBLE click leaves View Image (back to the grid, app stays alive).
* pan/drag is always clamped so the image behaves like a sheet *inside a box*:
  its edges never pull away from the viewport, so panning can never expose
  black space - large images clamp to (displayed-viewport)/2, images smaller
  than the viewport simply stay centred.
* the finished fullscreen *appearance* is untouched (black surface, chrome
  hidden, object-fit:contain, transform-based geometry so the harness DOM stub
  still works).

Layer 1 is a static wiring audit of ``veyra/index.html``.  Layer 2 runs the
*real* extracted View-Image block + keydown handler under Node with a tiny
DOM/window stub (with a real viewport), so it fails if the shipped code
regresses.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML_PATH = ROOT / "veyra" / "index.html"

FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  [PASS] " if cond else "  [FAIL] ") + label)
    if not cond:
        FAILS.append(label)


def section(title: str) -> None:
    print("\n== " + title + " ==")


# --------------------------------------------------------------------------- #
# Node harness: run the REAL View-Image block with a DOM stub                  #
# --------------------------------------------------------------------------- #
NODE_PRELUDE = r"""
'use strict';
let ENTER_CALLS = 0, LEAVE_CALLS = 0;

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

const bodyClassSet = new Set();
const body = { classList: makeClassList(bodyClassSet), appendChild() {} };

// the real #fsImg element the block grabs via getElementById('fsImg')
const fsImgEl = {
  tagName: 'IMG', style: {}, naturalWidth: 0, naturalHeight: 0,
  _attrs: {}, _on: {}, classList: makeClassList(new Set()),
  addEventListener(t, f) { (this._on[t] = this._on[t] || []).push(f); },
  removeEventListener() {},
  getAttribute(k) { return (k in this._attrs) ? this._attrs[k] : null; },
  setAttribute(k, v) { this._attrs[k] = v; },
  removeAttribute(k) { delete this._attrs[k]; },
};
Object.defineProperty(fsImgEl, 'src', {
  get() { return this._attrs.src; },
  set(v) { this._attrs.src = v; },
  configurable: true,
});

// the real .fs-view surface the block creates (createElement('div')), with a
// real viewport so the fit-width / clamp maths can be exercised.
const fsViewEl = {
  tagName: 'DIV', className: '', innerHTML: '', style: {},
  clientWidth: 1280, clientHeight: 720,
  _on: {}, classList: makeClassList(new Set()),
  addEventListener(t, f) { (this._on[t] = this._on[t] || []).push(f); },
  removeEventListener() {}, appendChild() {},
  getBoundingClientRect() { return { width: 0, height: 0, left: 0, top: 0, right: 0, bottom: 0 }; },
  querySelector() { return null; },
};

const document = {
  body,
  _on: {},
  createElement(tag) {
    if (String(tag).toLowerCase() === 'div') return fsViewEl;
    return { tagName: String(tag).toUpperCase(), style: {}, classList: makeClassList(new Set()),
             addEventListener() {}, appendChild() {}, querySelector() { return null; } };
  },
  getElementById(id) { return id === 'fsImg' ? fsImgEl : null; },
  addEventListener(t, f) { (this._on[t] = this._on[t] || []).push(f); },
  querySelector() { return null; },
  querySelectorAll() { return []; },
};

const window = {
  __aether_fs: { enter() { ENTER_CALLS++; return true; }, leave() { LEAVE_CALLS++; return true; } },
  addEventListener() {},
};

const state = {
  path: null, images: [], dirs: [], selected: null, selection: [], anchor: null,
  crypto: { unlocked: false },
  fs: { index: null, mode: 'actual', scale: 1, tx: 0, ty: 0, natW: 0, natH: 0,
        dragging: false, moved: false, suppressClick: false,
        startX: 0, startY: 0, origX: 0, origY: 0 },
};

const imgUrl = (p) => '/api/image?path=' + encodeURIComponent(p);
const thumbUrl = (p) => '/api/thumb?path=' + encodeURIComponent(p);
function toast() {}
function applySelectionClasses() {}
function updateStatus() {}
function updateStatusFile() {}
function isEditableTarget() { return false; }
function anyDialogVisible() { return false; }
function computeCols() { return 1; }
function selectIndex() {}
function primarySelectedIndex() { return null; }
"""

NODE_ASSERTIONS = r"""
function assert(cond, label) {
  console.log((cond ? '  [PASS] ' : '  [FAIL] ') + label);
  if (!cond) { process.exitCode = 1; }
}
function loadImg(w, h) { fsImgEl.naturalWidth = w; fsImgEl.naturalHeight = h; fsImgEl._on.load[0](); }
function scaleOf() { const m = (fsImgEl.style.transform || '').match(/scale\(([-\d.]+)\)/); return m ? parseFloat(m[1]) : NaN; }
function txOf() { const m = (fsImgEl.style.transform || '').match(/translate\(([-\d.]+)px/); return m ? parseFloat(m[1]) : NaN; }
const VW = fsViewEl.clientWidth;

// ---- OPEN -> ACTUAL SIZE -------------------------------------------------
state.images = [{ path: 'C:/p/ls.png', name: 'ls.png', encrypted: false }];
state.crypto = { unlocked: false };
openFullScreen(0);
loadImg(2000, 1500);
assert(state.fs.mode === 'actual', 'open: starts in ACTUAL SIZE mode');
assert(Math.abs(scaleOf() - 1) < 1e-9, 'open: actual size => scale 1 (not fit-to-screen)');
assert(fsImgEl.style.width === '2000px' && fsImgEl.style.height === '1500px',
       'open: element box sized to the native pixel size');
assert(fsViewEl.classList.contains('show') && document.body.classList.contains('fs-active'),
       'open: surface shown + app chrome hidden');
assert(ENTER_CALLS === 1, 'open: native fullscreen bridge still called');

// ---- single click toggles ACTUAL <-> FIT WIDTH ---------------------------
fsImgEl._on.click[0]({ button: 0 });
assert(state.fs.mode === 'fitwidth', 'click 1: toggles to FIT WIDTH');
assert(Math.abs(scaleOf() - (VW / 2000)) < 1e-9, 'click 1: fit width => scale vpW/natW');
fsImgEl._on.click[0]({ button: 0 });
assert(state.fs.mode === 'actual' && Math.abs(scaleOf() - 1) < 1e-9, 'click 2: toggles back to ACTUAL SIZE');

// ---- pan clamp: image larger than viewport -------------------------------
state.fs.tx = 9999; fsApply();
assert(Math.abs(txOf() - (2000 - VW) / 2) < 1e-9, 'clamp: positive pan limited to (dispW-vpW)/2');
state.fs.tx = -9999; fsApply();
assert(Math.abs(txOf() + (2000 - VW) / 2) < 1e-9, 'clamp: negative pan limited symmetrically');
state.fs.ty = 9999; fsApply();
assert(Math.abs((fsImgEl.style.transform.match(/translate\([-0-9.]+px,([-0-9.]+)px/) || [0, NaN])[1]
                - (1500 - fsViewEl.clientHeight) / 2) < 1e-9, 'clamp: vertical pan clamped too');

// ---- drag/pan stays clamped + sets suppressClick --------------------------
fsImgEl._on.mousedown[0]({ button: 0, preventDefault() {}, stopPropagation() {}, clientX: 640, clientY: 360 });
document._on.mousemove[0]({ clientX: 640 - 9000, clientY: 360 });
assert(Math.abs(txOf() + (2000 - VW) / 2) < 1e-9, 'drag: pan clamped to the viewport box while dragging');
assert(state.fs.moved === true, 'drag: movement recorded');
document._on.mouseup[0]();
assert(state.fs.dragging === false && state.fs.suppressClick === true, 'drag end: drag stops and marks a suppressed click');

// ---- a suppressed click must NOT toggle -----------------------------------
const modeBefore = state.fs.mode;
fsImgEl._on.click[0]({ button: 0 });
assert(state.fs.mode === modeBefore, 'drag then click: no accidental Actual/Fit toggle');
assert(state.fs.suppressClick === false, 'drag then click: suppression consumed once');

// ---- image smaller than the viewport stays centred ------------------------
state.images = [{ path: 'C:/p/sm.png', name: 'sm.png', encrypted: false }];
openFullScreen(0);
loadImg(300, 200);
state.fs.tx = 9999; state.fs.ty = 9999; fsApply();
assert(Math.abs(txOf()) < 1e-9, 'small image: cannot be panned into black space (tx stays 0)');

// ---- wheel navigation over the active ordered dataset ---------------------
state.images = [
  { path: 'a.png', name: 'a', encrypted: false },
  { path: 'b.png', name: 'b', encrypted: false },
];
openFullScreen(0);
fsViewEl._on.wheel[0]({ deltaY: 120, preventDefault() {} });
assert(state.fs.index === 1, 'wheel down: advances to the next image');
assert(state.fs.mode === 'actual' && state.fs.scale === 1 && state.fs.tx === 0 && state.fs.ty === 0,
       'wheel nav: the next image opens again at ACTUAL SIZE (no leftover zoom/pan)');
fsViewEl._on.wheel[0]({ deltaY: -120, preventDefault() {} });
assert(state.fs.index === 0, 'wheel up: goes back to the previous image');
fsViewEl._on.wheel[0]({ deltaY: -120, preventDefault() {} });
assert(state.fs.index === 0, 'wheel up at the first image is a no-op');

// ---- keyboard + / - zoom --------------------------------------------------
openFullScreen(0);
loadImg(2000, 1500);
document._on.keydown[0]({ key: '+', preventDefault() {}, stopPropagation() {} });
assert(Math.abs(scaleOf() - 1.25) < 1e-9, 'key "+": zoom in');
document._on.keydown[0]({ key: '-', preventDefault() {}, stopPropagation() {} });
assert(Math.abs(scaleOf() - 1.0) < 1e-9, 'key "-": zoom out');

// ---- double-click leaves View Image (app stays alive) ---------------------
openFullScreen(0);
loadImg(2000, 1500);
fsImgEl._on.dblclick[0]({ button: 0, preventDefault() {} });
assert(!fsViewEl.classList.contains('show'), 'double-click: leaves the View-Image surface');
assert(!document.body.classList.contains('fs-active'), 'double-click: app chrome restored');
assert(fsImgEl.getAttribute('src') === null, 'double-click: image src released (no leak)');
assert(LEAVE_CALLS >= 1, 'double-click: native fullscreen bridge left');
assert(state.images.length === 2, 'double-click: viewer state still valid (app not closed)');

// ---- Escape still exits ---------------------------------------------------
openFullScreen(0);
loadImg(2000, 1500);
document._on.keydown[0]({ key: 'Escape', preventDefault() {}, stopPropagation() {} });
assert(!fsViewEl.classList.contains('show'), 'escape: still exits View Image');

// ---- no mandatory Close button in the surface -----------------------------
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
    node = shutil.which("node")
    if not node:
        check(False, "node executable available to run the runtime behaviour test")
        return False

    harness = NODE_PRELUDE + "\n" + block + "\n" + handler + "\n" + NODE_ASSERTIONS
    tmp = Path(tempfile.mkdtemp(prefix="veyra_vi_")) / "harness.js"
    tmp.write_text(harness, encoding="utf-8")
    try:
        proc = subprocess.run([node, str(tmp)], capture_output=True, text=True, timeout=60)
    except Exception as exc:  # pragma: no cover - environment failure
        check(False, f"node harness ran ({exc})")
        return False
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)

    out = proc.stdout + proc.stderr
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("[PASS]"):
            print("  " + s)
        elif s.startswith("[FAIL]"):
            check(False, s.split("[FAIL]", 1)[1].strip())
        elif s.startswith("ERROR"):
            check(False, s)
    return proc.returncode == 0


def main() -> int:
    html = HTML_PATH.read_text(encoding="utf-8")
    block = _extract_fullscreen_block(html)
    handler = _extract_keydown_handler(html)

    section("1. View-Image block exists and is wired")
    check(block != "", "the View-Image block is present in veyra/index.html")
    for token in (
        "function fsSetMode(", "function fsToggleMode(", "function fsClamp(",
        "function fsZoom(", "function fsSizeToNatural(", "function fsApply(",
        "fsImg.addEventListener('click'", "fsImg.addEventListener('dblclick'",
        "fsImg.addEventListener('mousedown'", "fsView.addEventListener('wheel'",
    ):
        check(token in block, f"block defines/wires {token}")

    section("2. OPEN starts at ACTUAL SIZE and clears previous zoom/pan")
    check("state.fs.mode='actual'" in block and "state.fs.scale=1" in block and "state.fs.tx=0" in block,
          "openFullScreen resets mode=actual, scale=1, tx/ty=0 (no fit-to-screen)")

    section("3. single click toggles, double-click closes")
    check("fsToggleMode();" in block, "single click calls the Actual/Fit-Width toggle")
    check("closeFullScreen();" in handler or "closeFullScreen" in block,
          "double-click closes via the shared closeFullScreen")

    section("4. wheel scrolls through the active ordered dataset")
    check("fsStep(1)" in block and "fsStep(-1)" in block, "wheel down -> next, wheel up -> previous")
    check(".sort(" not in block, "no new sorting subsystem introduced (uses the current order)")

    section("5. keyboard +/- zoom stays intact and gradual")
    check("fsZoom(1.25)" in handler and "fsZoom(0.8)" in handler, "keyboard +/- map to gradual zoom steps")

    section("6. pan clamp + appearance preserved")
    check("fsClamp" in block and "Math.max(0,(d.w-vp.w)/2)" in block and "Math.max(0,(d.h-vp.h)/2)" in block,
          "pan is clamped to (displayed-viewport)/2 on both axes (no black leakage)")
    check("object-fit:contain" in html and "width:100%;height:100%" in html,
          "fullscreen appearance unchanged (object-fit:contain + 100% box)")
    check("body.fs-active .app{display:none}" in html and ".fs-view{position:fixed;inset:0;background:#000" in html,
          "black surface + hidden chrome unchanged")
    check("[data-fs]" not in block, "no leftover (button-driven) old fullscreen control handling")

    section("7. runtime behaviour of the real code (Node)")
    ok = run_node_behaviour(block, handler)
    check(ok, "the shipped View-Image interaction code passes every runtime assertion")

    print("\n" + "=" * 64)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("RESULT: ALL VIEW-IMAGE INTERACTION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
