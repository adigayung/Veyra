"""Permanent verification for Veyra's large-dataset Image-Grid virtualisation.

Run with the project venv::

    J:\\Veyra\\venv\\Scripts\\python.exe verify_grid_virtualization.py

The grid renders real filesystem images.  With 10.000+ images it must NOT mount
10.000 cards: it keeps a *bounded virtual render window* around the centre of
the viewport and only refreshes the DOM once scrolling has genuinely stopped.

What this suite locks in (the algorithm, not just "a debounce was added"):

 1. The render window is ``centerIndex - 30 .. centerIndex + 30`` (about 60-61
    cards) - never the whole dataset.
 2. A scroll event does NOT render: it only (re)arms a ~250 ms debounce timer,
    so a scroll produces *zero* renders until the user stops.
 3. A far jump is a *direct jump*: it costs exactly ONE re-render that mounts
    the new centre's window - there is no cascade walking through the thousands
    of images in between.
 4. If the new centre is still inside the already mounted window, no re-render
    happens at all.
 5. The DOM stays bounded on every jump, old far-away nodes are dropped, the
    per-item lazy ``loading="lazy"`` is preserved, and the scrollable height is
    kept (top/bottom spacers) so the scrollbar behaves normally.
 6. Selection survives a window change (re-applied from selection state) and an
    off-window selection is brought into view by mounting its window.
 7. The Image-View / fullscreen block (double-click, actual size, fit width,
    zoom, pan, wheel navigation, native fullscreen, `.aimg`) is untouched.

Layer 1 is a static wiring audit of ``veyra/index.html``.  Layer 2 runs the
*real* extracted grid block under Node with a small DOM stub, so it fails if the
shipped algorithm regresses.
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


def _extract(html: str, start_token: str, end_token: str) -> str:
    start = html.find(start_token)
    if start < 0:
        return ""
    end = html.find(end_token, start + len(start_token))
    if end <= start:
        return ""
    return html[start:end]


# --------------------------------------------------------------------------- #
# Node harness: run the REAL grid block with a tiny DOM stub (10.000 items)    #
# --------------------------------------------------------------------------- #
NODE_PRELUDE = r"""
'use strict';

let RENDER_COUNT = 0;
let currentCards = [];

function makeClassList() {
  const s = new Set();
  return { add: (c) => s.add(c), remove: (c) => s.delete(c),
           contains: (c) => s.has(c), has: (c) => s.has(c) };
}
function makeCard(index) {
  return { dataset: { index: String(index) }, classList: makeClassList(),
           onclick: null, ondblclick: null, querySelector() { return null; } };
}
function parseCards(html) {
  const re = /data-index="(\d+)"/g;
  const out = [];
  let m;
  while ((m = re.exec(html)) !== null) { out.push(makeCard(Number(m[1]))); }
  return out;
}

// The real #gridInner element (its innerHTML is what renderWindow writes).
const innerEl = { id: 'gridInner', style: {}, _html: '', querySelector() { return null; } };
Object.defineProperty(innerEl, 'innerHTML', {
  get() { return this._html; },
  set(v) { this._html = v; currentCards = parseCards(v); RENDER_COUNT++; },
  configurable: true,
});
const topPad = { style: {} };
const bottomPad = { style: {} };

// The real #grid scroll container.
const gridEl = {
  scrollTop: 0, clientWidth: 918, clientHeight: 720, onscroll: null, style: {}, _html: '',
  get innerHTML() { return this._html; },
  set innerHTML(v) { this._html = v; },
  appendChild() {},
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

const els = {
  grid: gridEl,
  statusFile: { textContent: '' }, statusFolders: { textContent: '' },
  statusFiles: { textContent: '' }, selectedStatus: { textContent: '' },
  viewCount: { textContent: '' },
};

const document = {
  createElement(tag) {
    return { tagName: tag, className: '', id: '', style: {}, appendChild() {}, querySelector() { return null; } };
  },
  getElementById(id) {
    if (id === 'gridInner') return innerEl;
    if (id === 'gridTop') return topPad;
    if (id === 'gridBottom') return bottomPad;
    return null;
  },
};

const state = { path: null, images: [], dirs: [], selected: null, selection: [], anchor: null,
                crypto: { unlocked: false } };

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
  return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
function thumbUrl(p) { return '/api/thumb?path=' + encodeURIComponent(p); }
function toast() {}
function updateStatus() {}
function updateStatusFile() {}
function handleCardClick() {}
function openFullScreen() {}
function applySelectionClasses() {
  const has = {}; state.selection.forEach(function (i) { has[i] = true; });
  els.grid.querySelectorAll('.card').forEach(function (c) {
    if (has[Number(c.dataset.index)]) c.classList.add('selected'); else c.classList.remove('selected');
  });
}
"""

NODE_ASSERTIONS = r"""
function assert(cond, label) {
  console.log((cond ? '  [PASS] ' : '  [FAIL] ') + label);
  if (!cond) process.exitCode = 1;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const cardIndices = () => currentCards.map((c) => Number(c.dataset.index));

const TOTAL = 10000;
const ROWH = gridLayout.rowH;
let COLS = 6;   // resolved in main() from the real computeCols()

function setup() {
  state.images = [];
  for (let i = 0; i < TOTAL; i++) {
    state.images.push({ path: 'C:/p/i' + i + '.png', name: 'img_' + i + '.png',
                        type: 'PNG', encrypted: false, width: 8, height: 8, size: 2048 });
  }
}

async function jumpTo(target) {
  const before = RENDER_COUNT;
  const row = Math.floor(target / COLS);
  els.grid.scrollTop = Math.max(0, row * ROWH - els.grid.clientHeight / 2 + ROWH / 2);
  const htmlBefore = innerEl._html;
  onGridScroll();  // the scroll event itself
  assert(innerEl._html === htmlBefore, 'jump ' + target + ': scroll event does NOT render synchronously');
  assert(RENDER_COUNT - before === 0, 'jump ' + target + ': zero renders before the debounce elapses');
  await sleep(GRID_SCROLL_DEBOUNCE + 90);
  const center = computeCenterIndex();
  const idx = cardIndices();
  const start = Math.max(0, center - GRID_WINDOW_RADIUS);
  const end = Math.min(TOTAL - 1, center + GRID_WINDOW_RADIUS);
  assert(RENDER_COUNT - before === 1, 'jump ' + target + ': direct jump => exactly ONE re-render (no cascade)');
  assert(Math.abs(center - target) <= COLS, 'jump ' + target + ': centre lands on the target row (' + center + ')');
  assert(idx.length >= 1 && idx.length <= 61, 'jump ' + target + ': DOM bounded to a window (<= 61), got ' + idx.length);
  assert(idx[0] === start && idx[idx.length - 1] === end,
         'jump ' + target + ': window is exactly [center-30 .. center+30] (' + start + '..' + end + ')');
  const firstArticle = innerEl._html.indexOf('<article');
  const leadPads = (innerEl._html.slice(0, firstArticle).match(/cell-pad/g) || []).length;
  assert(leadPads === start % COLS,
         'jump ' + target + ': spacer cells align the first rendered row to its columns');
  return { center: center, idx: idx };
}

(async function main() {
  setup();
  COLS = computeCols();
  assert(COLS >= 2 && COLS <= 12, 'computeCols -> sane column count (=' + COLS + ')');
  assert(GRID_WINDOW_RADIUS === 30, 'render window radius is 30 items');
  assert(GRID_SCROLL_DEBOUNCE === 250, 'scroll debounce is 250 ms');

  renderGrid();
  let idx = cardIndices();
  const topCenter = computeCenterIndex();
  assert(idx.length >= 1 && idx.length <= 61, 'initial DOM bounded to a window, got ' + idx.length);
  assert(idx[0] === 0, 'initial window starts at item 0');
  assert(idx[idx.length - 1] === topCenter + GRID_WINDOW_RADIUS,
         'initial window reaches centre+30 (top window clamped at 0)');

  // 1) start near the end of a 10k dataset
  const a = await jumpTo(9960);
  assert(a.center >= 9950 && a.center <= 9975, 'position ~9960 reached (' + a.center + ')');
  assert(a.idx.length <= 61, 'still bounded after the first big jump');

  // 2) jump far away to ~5630
  const b = await jumpTo(5630);
  assert(b.center >= 5600 && b.center <= 5660, 'jump 5630 lands around 5630 (' + b.center + ')');
  assert(b.idx[0] >= 5570 && b.idx[0] <= 5600 && b.idx[b.idx.length - 1] <= 5660 + 3,
         'render window ~5600..5660');

  // 3) jump again to ~2100
  const c = await jumpTo(2100);
  assert(c.center >= 2070 && c.center <= 2130, 'jump 2100 lands around 2100 (' + c.center + ')');
  assert(c.idx[0] >= 2040 && c.idx[c.idx.length - 1] <= 2133, 'render window ~2070..2130');
  assert(c.idx[0] > 0 && c.idx[c.idx.length - 1] < TOTAL - 1, 'window stays strictly around the centre');

  // 4) repeated far jumps stay bounded and cheap
  let prevStart = -1;
  for (const t of [8000, 500, 9990, 1500, 4000]) {
    const r = await jumpTo(t);
    assert(r.idx.length <= 61, 'jump ' + t + ': DOM still bounded');
    assert(r.idx[0] !== prevStart, 'jump ' + t + ': window actually moved');
    prevStart = r.idx[0];
  }

  // 5) centre inside the active window => no re-render
  {
    const before = RENDER_COUNT;
    els.grid.scrollTop += ROWH;   // ~one row; centre still inside center+/-30
    applyScrollWindow();
    assert(RENDER_COUNT - before === 0, 'centre still inside the active window => no re-render');
  }

  // 6) centre leaves the active window => exactly one re-render
  {
    const before = RENDER_COUNT;
    els.grid.scrollTop += ROWH * 40;   // far outside the window
    applyScrollWindow();
    assert(RENDER_COUNT - before === 1, 'centre leaves the active window => exactly one re-render');
  }

  // 7) selection survives a window change and off-window selection is shown
  {
    const center = computeCenterIndex();
    state.selection = [center]; state.selected = center; state.anchor = center;
    renderWindow(center);
    let sel = currentCards.filter((c) => c.classList.contains('selected')).map((c) => Number(c.dataset.index));
    assert(sel.length === 1 && sel[0] === center, 'selection re-applied inside the rendered window');

    const target = TOTAL - 3;
    state.selection = [target]; state.selected = target; state.anchor = target;
    ensureVisible(target);
    assert(cardIndices().indexOf(target) >= 0, 'ensureVisible mounts the window around an off-window selection');
  }

  // 8) lazy loading is preserved in the shipped card markup
  assert(innerEl._html.indexOf('loading="lazy"') >= 0 || currentCards.length === 0,
         'lazy loading attribute kept on thumbnails');
})();
"""


def run_node_harness(prelude: str, consts: str, block: str, assertions: str) -> bool:
    node = shutil.which("node")
    if not node:
        check(False, "node executable available to run the runtime grid test")
        return False

    harness = prelude + "\n" + consts + "\n" + block + "\n" + assertions
    tmp = Path(tempfile.mkdtemp(prefix="veyra_grid_")) / "harness.js"
    tmp.write_text(harness, encoding="utf-8")
    try:
        proc = subprocess.run([node, str(tmp)], capture_output=True, text=True, timeout=90)
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
        elif "Error" in s or "error" in s:
            print("  " + s)
    return proc.returncode == 0


def main() -> int:
    html = HTML_PATH.read_text(encoding="utf-8")

    grid_block = _extract(html, "// -- virtualized grid", "// -- sidebar tree")
    grid_consts = _extract(html, "var GRID_MIN=142", "// -- small helpers")
    fullscreen_block = _extract(html, "// -- full screen viewer", "// -- right click context menu")

    section("1. Algorithm wiring present in veyra/index.html")
    check("GRID_WINDOW_RADIUS=30" in grid_consts, "render radius is 30 items")
    check("GRID_SCROLL_DEBOUNCE=250" in grid_consts, "scroll debounce is 250 ms")
    for token in (
        "function computeCenterIndex(",
        "function renderWindow(",
        "function onGridScroll(",
        "function applyScrollWindow(",
    ):
        check(token in grid_block, f"grid block defines {token}")
    check("Math.max(0,center-GRID_WINDOW_RADIUS)" in grid_block
          and "Math.min(total-1,center+GRID_WINDOW_RADIUS)" in grid_block,
          "renderWindow mounts exactly centerIndex +/- 30")
    check("start=Math.max(0,center-GRID_WINDOW_RADIUS)" in grid_block.replace(" ", ""),
          "window starts at centerIndex - 30")

    section("2. Scroll is debounced (no per-event re-render)")
    check("gridLayout.scrollTimer=setTimeout(applyScrollWindow,GRID_SCROLL_DEBOUNCE)" in grid_block,
          "a scroll event only arms the debounce timer")
    check("clearTimeout(gridLayout.scrollTimer)" in grid_block,
          "every further scroll event resets (cancels) the timer")
    check("els.grid.onscroll=onGridScroll;" in grid_block,
          "the grid scroll handler is the debounced onGridScroll")
    check("onscroll=function(){layoutGrid();}" not in html and "function layoutGrid(" not in html,
          "the old per-scroll re-render cascade is gone")

    section("3. Direct jump decision")
    check("if(gridLayout.winStart>=0&&center>=gridLayout.winStart&&center<=gridLayout.winEnd){return;}" in grid_block,
          "no re-render when the centre is still inside the active window")

    section("4. Bounded DOM + lazy loading preserved")
    check('loading="lazy"' in html, "thumbnails keep loading=\"lazy\"")
    check("renderWindow(computeCenterIndex());" in grid_block,
          "renderGrid opens with the centre window (not the whole dataset)")
    check("ensureVisible" in grid_block and "renderWindow(i);" in grid_block,
          "ensureVisible mounts the window around an off-window selection")

    section("5. Image View / fullscreen untouched")
    check(fullscreen_block != "", "the View-Image block is still present")
    for token in (
        "function fsSetMode(", "function fsToggleMode(", "function fsClamp(",
        "function fsZoom(", "function fsApply(", "fsImg.addEventListener('dblclick'",
        "fsView.addEventListener('wheel'",
    ):
        check(token in fullscreen_block, f"View-Image block still defines/wires {token}")

    section("6. runtime behaviour of the real grid code (Node, 10.000 items)")
    prelude, consts, block, assertions = NODE_PRELUDE, grid_consts, grid_block, NODE_ASSERTIONS
    check(consts != "" and block != "", "grid constants + block extracted from index.html")
    ok = run_node_harness(prelude, consts, block, assertions)
    check(ok, "the shipped grid virtualisation passes every runtime assertion")

    print("\n" + "=" * 64)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("RESULT: ALL GRID VIRTUALISATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
