"""Permanent verification for Veyra's grid marquee selection + drag-out.

Run with the project venv::

    J:\\Veyra\\venv\\Scripts\\python.exe test\\verify_marquee_drag.py

What this suite locks in (and, importantly, the REGRESSIONS it must catch):

 1. A *plain* press on a card never captures the pointer and never calls
    preventDefault.  Capturing the pointer on .grid retargets the later
    click / dblclick to the grid element in WebView2/Chromium, which silently
    broke "double-click opens Image View".  This suite FAILS if a plain
    pointerdown calls setPointerCapture() or preventDefault().
 2. The marquee is LAZY: a plain press only *arms* it; the overlay + selection
    only activate once the pointer moves past the threshold.  A click without
    movement therefore leaves ``justDragged`` false and the card's own click
    handler still replaces the selection (card.onclick -> handleCardClick), and
    card.ondblclick -> openFullScreen is still wired and invoked.
 3. Marquee (rubber-band) selection uses state.selection + state.anchor, is
    driven through the real shipped selection.js handlers, and extrapolates
    card geometry from a mounted card so unmounted (virtualized) cards are
    still selectable by index.  It also unions with the base selection.
 4. Drag-out builds a VALID web payload: real File objects attached through
    ``dataTransfer.items.add()`` (bytes fetched via /api/image).  It must NOT
    call ``setData('Files', string)`` (that type is reserved) and must skip
    encrypted .aimg files (a toast is shown instead).
 5. Virtualization behaviour is untouched.

The harness executes the REAL shipped JS modules under Node with a small DOM
stub, following the same pattern as verify_grid_virtualization.py.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "test"))

import verify_ui_sources as ui  # noqa: E402

GRID_JS = ui.js_for("modules/grid", "modules/sort", "modules/selection")

FAILS: list[str] = []


def strip_js_comments(src: str) -> str:
    """Drop /* ... */ and // ... comments so the token guards below match CODE,
    not prose (comments are allowed to *explain* the regression we fixed)."""
    import re
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?m)//.*$", "", src)
    return src


def check(cond: bool, label: str) -> None:
    print(("  [PASS] " if cond else "  [FAIL] ") + label)
    if not cond:
        FAILS.append(label)


def section(title: str) -> None:
    print("\n== " + title + " ==")


NODE_PRELUDE = r"""
'use strict';

let RENDER_COUNT = 0;
let currentCards = [];

/* Spies that let us PROVE the regression is caught. */
let CAPTURE_CALLS = 0;
let RELEASE_CALLS = 0;
let FULLSCREEN_CALLS = [];
let TOASTS = [];
let FETCH_CALLS = [];

function makeClassList() {
  const s = new Set();
  return { add: (c) => s.add(c), remove: (c) => s.delete(c),
           contains: (c) => s.has(c), has: (c) => s.has(c) };
}

function makeCard(index) {
  return {
    dataset: { index: String(index) },
    classList: makeClassList(),
    onclick: null, ondblclick: null, ondragstart: null,
    draggable: false,
    querySelector() { return null; },
    getBoundingClientRect() {
      const cols = 6;
      const col = index % cols, row = Math.floor(index / cols);
      return { left: 10 + col * 150, top: 10 + row * 178, width: 150, height: 170 };
    },
  };
}

function parseCards(html) {
  const re = /data-index="(\d+)"/g;
  const out = [];
  let m;
  while ((m = re.exec(html)) !== null) { out.push(makeCard(Number(m[1]))); }
  return out;
}

const innerEl = { id: 'gridInner', style: {}, _html: '', querySelector() { return null; } };
Object.defineProperty(innerEl, 'innerHTML', {
  get() { return this._html; },
  set(v) { this._html = v; currentCards = parseCards(v); RENDER_COUNT++; },
  configurable: true,
});
const topPad = { style: {} };
const bottomPad = { style: {} };

const gridEl = {
  scrollTop: 0, clientWidth: 918, clientHeight: 720, onscroll: null, style: {}, _html: '',
  classList: makeClassList(),
  get innerHTML() { return this._html; },
  set innerHTML(v) { this._html = v; },
  appendChild() {},
  contains() { return true; },
  setPointerCapture() { CAPTURE_CALLS++; },
  releasePointerCapture() { RELEASE_CALLS++; },
  querySelector(sel) {
    const m = /data-index="(\d+)"/.exec(sel || '');
    if (m) {
      const idx = Number(m[1]);
      for (const c of currentCards) { if (Number(c.dataset.index) === idx) return c; }
    }
    return null;
  },
  querySelectorAll(sel) { return sel === '.card' ? currentCards : []; },
};

const stubEls = {};
function stubEl(id) {
  if (!stubEls[id]) stubEls[id] = {
    id: id, textContent: '', value: '', style: {}, classList: makeClassList(),
    addEventListener() {}, querySelector() { return null; },
  };
  return stubEls[id];
}

const document = {
  createElement(tag) {
    return {
      tagName: tag, className: '', id: '', style: {}, _removed: false,
      appendChild() {}, remove() { this._removed = true; },
      parentNode: null, querySelector() { return null; },
    };
  },
  body: {
    children: [],
    appendChild(el) { el.parentNode = document.body; document.body.children.push(el); },
    removeChild(el) {
      const i = document.body.children.indexOf(el);
      if (i >= 0) document.body.children.splice(i, 1);
      el.parentNode = null;
    },
  },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  getElementById(id) {
    if (id === 'grid') return gridEl;
    if (id === 'gridInner') return innerEl;
    if (id === 'gridTop') return topPad;
    if (id === 'gridBottom') return bottomPad;
    return stubEl(id);
  },
};

/* fetch + File stubs: the drag payload must carry REAL bytes, so we simulate
   the /api/image bytes as a tiny object with a non-zero byteLength. */
function fetch(url) {
  FETCH_CALLS.push(url);
  return Promise.resolve({
    ok: true,
    blob: () => Promise.resolve({ type: 'image/png', byteLength: 42 }),
  });
}

function File(parts, name, opts) {
  this.parts = parts;
  this.name = name;
  this.type = (opts && opts.type) || '';
  this.size = parts.reduce((a, p) => a + ((p && (p.byteLength || p.size)) || 0), 0);
}

function openFullScreen(i) { FULLSCREEN_CALLS.push(i); }
function updateStatus() {}
function activeSource() { return state.images; }
function sortImages(arr) { return (arr || []).slice(); }
"""

NODE_ASSERTIONS = r"""
function assert(cond, label) {
  console.log((cond ? '  [PASS] ' : '  [FAIL] ') + label);
  if (!cond) process.exitCode = 1;
}
const tick = () => new Promise((r) => setTimeout(r, 0));
const TOTAL = 250;

function setup() {
  state.images = [];
  for (let i = 0; i < TOTAL; i++) {
    state.images.push({ path: 'C:/pics/i' + i + '.png', name: 'img_' + i + '.png',
                        type: 'PNG', encrypted: false, width: 8, height: 8, size: 2048 });
  }
  state.rawImages = state.images.slice();
}

function cardAt(i) { return currentCards.find((c) => Number(c.dataset.index) === i); }

function downEvent(card, x, y, mods) {
  mods = mods || {};
  return {
    button: mods.button || 0,
    ctrlKey: !!mods.ctrl, metaKey: !!mods.meta, shiftKey: !!mods.shift,
    clientX: x, clientY: y, pointerId: mods.pointerId || 1,
    prevented: false,
    preventDefault() { this.prevented = true; },
    target: { closest(sel) { return sel === '.card' ? card : null; } },
  };
}

function makeDT() {
  const dt = { effectAllowed: '', files: [], _setData: [] };
  dt.items = { add(f) { dt.files.push(f); } };
  dt.setData = function (k, v) { dt._setData.push([k, v]); };
  return dt;
}

/* The real browser order for a click: pointerdown -> pointerup -> click. */
function clickSequence(i, opts) {
  const card = cardAt(i);
  const down = downEvent(card, 20, 20);
  els.grid.onpointerdown(down);
  els.grid.onpointerup({});
  if (card.onclick) card.onclick(opts || {});
  return down;
}

(async function main() {
  setup();
  renderGrid();
  gridLayout.calibrated = true;

  // ---------------------------------------------------------------- 0. reset
  toast = function (msg) { TOASTS.push(msg); };
  state.marquee.justDragged = false;

  // ---- 1. A plain press NEVER captures the pointer / preventDefaults ------
  state.selection = []; state.selected = null; state.anchor = null;
  CAPTURE_CALLS = 0; RELEASE_CALLS = 0;
  const plainDown = downEvent(cardAt(0), 20, 20);
  els.grid.onpointerdown(plainDown);
  assert(plainDown.prevented === false,
         'plain pointerdown does NOT call preventDefault (click/dblclick survive)');
  assert(CAPTURE_CALLS === 0,
         'plain pointerdown does NOT call setPointerCapture (no click retargeting)');
  assert(state.marquee.armed === true, 'plain pointerdown ARMS the lazy marquee');
  assert(state.marquee.active === false, 'plain pointerdown does NOT activate the marquee');

  // pointerup without movement -> not a drag -> justDragged stays false.
  els.grid.onpointerup({});
  assert(state.marquee.armed === false, 'pointerup without movement disarms the marquee');
  assert(RELEASE_CALLS === 0, 'pointerup does NOT call releasePointerCapture');
  assert(state.marquee.justDragged === false, 'a plain click never sets justDragged');

  // ---- 2. Plain click still replaces the selection (card.onclick wiring) --
  state.selection = []; state.selected = null; state.anchor = null;
  clickSequence(7, {});
  assert(JSON.stringify(state.selection) === JSON.stringify([7]),
         'plain click (via card.onclick -> handleCardClick) replaces the selection');
  assert(state.marquee.armed === false, 'click left the marquee disarmed');

  // CTRL-click toggles, SHIFT-click extends (still intact).
  clickSequence(9, { ctrlKey: true });
  assert(state.selection.indexOf(7) >= 0 && state.selection.indexOf(9) >= 0,
         'Ctrl+click toggles an extra card into the selection');
  state.anchor = 7;
  clickSequence(11, { shiftKey: true });
  assert(JSON.stringify(state.selection) === JSON.stringify([7, 8, 9, 10, 11]),
         'Shift+click range-selects from the anchor');

  // ---- 3. Double-click still opens Image View via card.ondblclick ---------
  FULLSCREEN_CALLS = [];
  const dc = clickSequence(4, {});            // first click of the dbl-click
  assert(dc.prevented === false && CAPTURE_CALLS === 0,
         'first click of a double-click is a normal click (no capture)');
  cardAt(4).ondblclick();                     // browser fires dblclick on the card
  assert(FULLSCREEN_CALLS.length === 1 && FULLSCREEN_CALLS[0] === 4,
         'double-click invokes openFullScreen with the card index (Image View opens)');

  // ---- 4. Marquee: lazy activation + geometry (virtualized by index) ------
  state.selection = []; state.selected = null; state.anchor = null;
  const dragDown = downEvent(cardAt(0), 20, 20);
  els.grid.onpointerdown(dragDown);
  assert(state.marquee.active === false, 'marquee is NOT active immediately after the press');
  // A sub-threshold move is still a click, not a marquee.
  const thumb = downEvent(cardAt(0), 20, 20);
  els.grid.onpointerdown(thumb);
  els.grid.onpointermove({ clientX: 23, clientY: 22, pointerId: thumb.pointerId });
  assert(state.marquee.active === false, 'a <= threshold move does NOT start the marquee');
  els.grid.onpointerup({});

  els.grid.onpointerdown(downEvent(cardAt(0), 20, 20, { pointerId: 5 }));
  els.grid.onpointermove({ clientX: 400, clientY: 400, pointerId: 5 });
  assert(state.marquee.active === true, 'a move > threshold activates the marquee');
  assert(state.marquee.dragged === true, 'the marquee records a drag');
  assert(gridEl.classList.contains('marquee-active'),
         'the grid carries the marquee-active class (text-select suppressed via CSS)');
  const ended = els.grid.onpointerup({});
  assert(state.marquee.active === false, 'marquee deactivated after pointerup');
  assert(state.marquee.overlay === null, 'marquee overlay removed after pointerup');
  assert(state.marquee.justDragged === true, 'a completed drag sets justDragged');
  assert(gridEl.classList.contains('marquee-active') === false,
         'marquee-active class removed when the drag ends');

  // 6 cols, pitch 158, row height 178, card 0 at (10,10): rect (20,20)-(400,400)
  // covers cols 0..2 x rows 0..2 => 9 cards.
  assert(JSON.stringify(state.selection) === JSON.stringify([0,1,2,6,7,8,12,13,14]),
         'marquee selected every card overlapped by the rectangle (by index)');
  assert(state.selection.every((v, i, a) => i === 0 || a[i-1] < v),
         'marquee kept state.selection sorted');

  // A fresh press clears the post-drag flag, so the NEXT genuine click works
  // even when the browser skipped the synthetic click after the drag.
  state.marquee.justDragged = true;   // simulate a drag that emitted no synthetic click
  clickSequence(6, {});
  assert(JSON.stringify(state.selection) === JSON.stringify([6]),
         'a fresh click after a marquee drag still replaces the selection (stale flag cleared)');
  assert(state.marquee.justDragged === false, 'a fresh press clears justDragged');

  // The synthetic click that immediately follows a real drag IS swallowed.
  els.grid.onpointerdown(downEvent(cardAt(0), 20, 20, { pointerId: 11 }));
  els.grid.onpointermove({ clientX: 300, clientY: 300, pointerId: 11 });
  els.grid.onpointerup({});
  assert(state.marquee.justDragged === true, 'a completed drag sets justDragged');
  handleCardClick(99, {});
  assert(JSON.stringify(state.selection) !== JSON.stringify([99]),
         'the post-marquee synthetic click does NOT reselect a single card');
  assert(state.marquee.justDragged === false, 'justDragged consumed after one click');
  // ...and click / dblclick still work right after a marquee drag.
  clickSequence(8, {});
  assert(JSON.stringify(state.selection) === JSON.stringify([8]),
         'a plain click right after a marquee drag still replaces the selection');

  // ---- 5. Marquee unions with the base selection -------------------------
  state.selection = [5]; state.selected = 5; state.anchor = 5;
  els.grid.onpointerdown(downEvent(cardAt(0), 20, 20, { pointerId: 7 }));
  els.grid.onpointermove({ clientX: 180, clientY: 200, pointerId: 7 });
  els.grid.onpointerup({});
  assert(state.selection.indexOf(5) >= 0, 'marquee kept the base selection (union)');
  assert(state.selection.length > 1, 'marquee added the cards the rectangle covered');
  assert(state.selection.every((v, i, a) => i === 0 || a[i-1] < v), 'union result stays sorted');
  state.marquee.justDragged = false;

  // ---- 6. Modifier keys are NOT marquee ----------------------------------
  let startedCtrl = marqueeStart(downEvent(cardAt(0), 0, 0, { ctrl: true }));
  assert(startedCtrl === false, 'Ctrl+pointerdown does not start a marquee');
  let startedShift = marqueeStart(downEvent(cardAt(0), 0, 0, { shift: true }));
  assert(startedShift === false, 'Shift+pointerdown does not start a marquee');

  // ---- 7. Drag-out: single file -> real File object, no 'Files' string ----
  assert(cardAt(0).draggable === true, 'grid cards are draggable (native drag-and-drop)');
  state.selection = [0]; state.selected = 0; state.anchor = 0;
  FETCH_CALLS = [];
  els.grid.onpointerdown(downEvent(cardAt(0), 5, 5));
  await tick();
  assert(FETCH_CALLS.some((u) => u.indexOf('/api/image') >= 0),
         'pressing a card warms the drag byte cache via /api/image');
  let dt = makeDT();
  cardAt(0).ondragstart({ dataTransfer: dt, preventDefault() {} });
  assert(dt._setData.length === 0,
         "drag-out does NOT call setData('Files', ...) nor any reserved string type");
  assert(dt.files.length === 1 && dt.files[0].name === 'img_0.png',
         'drag single card -> exactly one File object with the real filename');
  assert(dt.files[0].size > 0 && dt.files[0].size === 42,
         'the File carries REAL bytes (non-empty payload)');
  assert(dt.effectAllowed === 'copy', "drag effectAllowed is 'copy'");

  // ---- 8. Multi-selection drag exports ALL selected files ----------------
  state.selection = [2, 5, 9]; state.selected = 9; state.anchor = 9;
  els.grid.onpointerdown(downEvent(cardAt(9), 5, 5));
  await tick();
  let dt2 = makeDT();
  cardAt(9).ondragstart({ dataTransfer: dt2, preventDefault() {} });
  const names2 = dt2.files.map((f) => f.name).sort();
  assert(JSON.stringify(names2) === JSON.stringify(['img_2.png','img_5.png','img_9.png']),
         'dragging the primary selected card exports ALL selected files as File objects');
  assert(dt2._setData.length === 0, 'multi drag still uses no string data type');

  // ---- 9. Dragging a NON-primary selected card exports only that file -----
  state.selection = [2, 5, 9]; state.selected = 2; state.anchor = 2;
  els.grid.onpointerdown(downEvent(cardAt(5), 5, 5));
  await tick();
  let dt3 = makeDT();
  cardAt(5).ondragstart({ dataTransfer: dt3, preventDefault() {} });
  assert(dt3.files.length === 1 && dt3.files[0].name === 'img_5.png',
         'dragging a non-primary selected card exports only that file');

  // ---- 10. Encrypted .aimg is never exported (toast instead) -------------
  state.images[3].encrypted = true;
  state.images[3].name = 'secret.aimg';
  state.selection = [3]; state.selected = 3; state.anchor = 3;
  TOASTS = [];
  els.grid.onpointerdown(downEvent(cardAt(3), 5, 5));
  await tick();
  let dt4 = makeDT();
  cardAt(3).ondragstart({ dataTransfer: dt4, preventDefault() {} });
  assert(dt4.files.length === 0 && dt4._setData.length === 0,
         'an encrypted .aimg is NOT exported (no File, no string payload)');
  assert(TOASTS.some((m) => m.toLowerCase().indexOf('.aimg') >= 0),
         'dragging a .aimg shows a clear toast explaining it cannot be dropped out');

  // ---- 11. Virtualization behaviour is untouched -------------------------
  assert(GRID_WINDOW_RADIUS === 30, 'render window radius is still 30 items');
  assert(GRID_SCROLL_DEBOUNCE === 250, 'scroll debounce is still 250 ms');
  assert(currentCards.length <= 61, 'DOM stays bounded to a window, got ' + currentCards.length);
})().catch((e) => { console.error('HARNESS ERROR:', e); process.exitCode = 1; });
"""


def run_node_harness() -> None:
    node = shutil.which("node")
    check(node is not None, "node executable available to run the runtime marquee/drag test")
    if not node:
        return
    section("runtime behaviour of the real grid + selection code (Node)")
    script = "\n".join([
        NODE_PRELUDE,
        ui.js_for("core/state"),
        GRID_JS,
        NODE_ASSERTIONS,
    ])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
        if out.stderr.strip():
            print(out.stderr.strip())
        ok = out.returncode == 0
        for line in out.stdout.splitlines():
            print(line)
        check(ok, "all marquee + drag-out runtime assertions passed")
    except Exception as e:  # noqa: BLE001
        print(f"    harness crashed: {e}")
        check(False, "harness crashed")
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    section("1. Marquee + drag-out shipped in the real modules")
    ui_all = ui.ui_source()
    check("function marqueeStart(" in ui_all, "marqueeStart is shipped in selection.js")
    check("function marqueeMove(" in ui_all, "marqueeMove is shipped in selection.js")
    check("function marqueeEnd(" in ui_all, "marqueeEnd is shipped in selection.js")
    check("function marqueePreview(" in ui_all, "marqueePreview extrapolates unmounted card geometry")
    check("marquee-selection" in ui.ui_css(), "marquee overlay has dedicated CSS styling")

    grid = ui.js_for("modules/grid")
    grid_code = strip_js_comments(grid)
    check("card.draggable = true" in grid, "cards are marked draggable")
    check("card.ondragstart" in grid, "cards ship an ondragstart handler")
    check("onpointerdown" in grid and "onpointermove" in grid and "onpointerup" in grid,
          "grid wires the marquee pointer handlers")

    section("2. Regression guards on the click/dblclick path")
    check("setPointerCapture" not in grid_code,
          "grid NEVER calls setPointerCapture (the click/dblclick retargeting bug)")
    check("releasePointerCapture" not in grid_code,
          "grid NEVER calls releasePointerCapture")
    check('"marquee-active"' not in ui.ui_css() and ".grid.marquee-active" in ui.ui_css(),
          "marquee drag suppresses text selection via CSS (.grid.marquee-active)")
    selection = ui.js_for("modules/selection")
    check("MARQUEE_THRESHOLD" in selection,
          "the marquee is lazy: it only activates past MARQUEE_THRESHOLD")
    state = ui.js_for("core/state")
    check("armed" in state, "state carries the lazy marquee 'armed' flag")

    section("3. Drag-out uses a VALID web payload (real File objects)")
    check("setData('Files'" not in grid_code,
          "drag-out no longer calls setData('Files', <string>) (reserved/invalid)")
    check("'text/uri-list'" not in grid_code,
          "drag-out no longer fabricates file:// 'paths' for the drop target")
    check("dataTransfer.items.add" in grid_code or "items.add(" in grid_code,
          "drag-out attaches File objects via dataTransfer.items.add()")
    check("new File(" in grid_code and "effectAllowed" in grid_code,
          "drag-out builds real File objects with effectAllowed")
    check("getSelectedImages()" in grid, "multi-selection drag reuses getSelectedImages()")
    check("encrypted" in grid, "drag-out refuses to export encrypted (.aimg) files")

    run_node_harness()

    print("\n" + "=" * 64)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("RESULT: ALL MARQUEE + DRAG-OUT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
