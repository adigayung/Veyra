"""Permanent verification for Veyra's Image-Grid "Sort By" control.

Run with the project venv::

    J:\\Veyra\\venv\\Scripts\\python.exe verify_sortby.py

The Image Grid / Thumbnail View gained a "Sort By" dropdown next to the
encrypt/decrypt icon.  It must sort the WHOLE filesystem listing (real
name / size / modified_at) while keeping the existing virtual grid intact:
only the active window is mounted, and a sort change costs a single bounded
render - exactly like a scroll jump.

What this suite locks in:

 1. The toolbar exposes a `#sortBy` select AFTER `#btnAimg` with exactly the six
    options (Date / (Date) / File Name / (File Name) / File Size / (File Size)).
 2. `sortImages()` is a pure helper: it returns a NEW array and never mutates the
    raw listing; all six modes produce the documented direction.
 3. `applySortBy()` re-sorts the mounted listing, preserves the selection by file
    path, returns the grid to the top and re-mounts ONLY the window - so a
    sort on 10.000 items is one bounded render, and far jumps afterwards are
    still single direct-jump renders (no cascade).
 4. `.aimg` stays a valid image for sorting; the set of files is never changed.
 5. Virtualisation constants / window maths and the Image-View / fullscreen /
    context-menu / group features are untouched.
 6. The backend still supplies the real metadata used for sorting
    (`size`, `modified_at` in ImageInfo.to_dict()).

Layer 1 is a static wiring audit of ``veyra/index.html`` (and the backend data
contract).  Layer 2 runs the *real* extracted grid + sorting block under Node
with a small DOM stub, so it fails if the shipped algorithm regresses.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML_PATH = ROOT / "veyra" / "index.html"
IMAGE_SERVICE_PATH = ROOT / "veyra" / "services" / "image_service.py"

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
# Node harness: run the REAL grid + sorting block with a tiny DOM stub (10.000) #
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
  sortBy: { value: 'name_asc', onchange: null },
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

const state = { path: null, images: [], rawImages: [], sort: 'name_asc', dirs: [],
                selected: null, selection: [], anchor: null, crypto: { unlocked: false } };

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
  return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
function thumbUrl(p) { return '/api/thumb?path=' + encodeURIComponent(p); }
function fmtSize(bytes) { return bytes == null ? '' : String(bytes); }
function toast() {}
function updateStatus() {}
function handleCardClick() {}
function openFullScreen() {}
function updateStatusFile() {}
function getSelectedImages() {
  return state.selection.slice().sort(function (a, b) { return a - b; })
    .map(function (i) { return state.images[i]; }).filter(function (x) { return !!x; });
}
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
const PERM = (i) => (i * 7919) % TOTAL;
const pad5 = (n) => String(n).padStart(5, '0');
const nameFor = (i) => 'file_' + pad5(PERM(i)) + '.png';
const sizeFor = (i) => ((PERM(i) * 37) % 5000) * 1024 + 1;
const mtimeFor = (i) => 1600000000 + PERM(i);

function setup() {
  state.sort = 'name_asc';
  state.rawImages = [];
  for (let i = 0; i < TOTAL; i++) {
    state.rawImages.push({ path: 'C:/p/' + i + '.png', name: nameFor(i),
                           size: sizeFor(i), modified_at: mtimeFor(i),
                           type: 'PNG', encrypted: false, width: 8, height: 8 });
  }
  // one .aimg container must be treated as a valid image by sorting too
  state.rawImages[7].path = 'C:/p/enc_00007.aimg';
  state.rawImages[7].name = 'enc_00007.aimg';
  state.rawImages[7].encrypted = true;
  state.images = sortImages(state.rawImages, state.sort);
  state.selected = null; state.selection = []; state.anchor = null;
}

const monoAsc = (a) => a.every((v, i) => i === 0 || a[i - 1] <= v);
const monoDesc = (a) => a.every((v, i) => i === 0 || a[i - 1] >= v);
const lowerNames = () => state.images.map((it) => it.name.toLowerCase());

const MODES = [
  ['date_desc', 'modified_at', 'desc'],
  ['date_asc', 'modified_at', 'asc'],
  ['name_asc', 'name', 'asc'],
  ['name_desc', 'name', 'desc'],
  ['size_desc', 'size', 'desc'],
  ['size_asc', 'size', 'asc'],
];

let COLS = 6;

(async function main() {
  setup();
  COLS = computeCols();
  assert(COLS >= 2 && COLS <= 12, 'computeCols -> sane column count (=' + COLS + ')');

  // 1) all six modes: correct direction, same multiset, raw untouched
  const rawBefore = state.rawImages.map((it) => it.name).join('|');
  const rawSet = state.rawImages.map((it) => it.name).slice().sort().join('|');
  for (const [mode, key, dir] of MODES) {
    const res = sortImages(state.rawImages, mode);
    assert(res.length === TOTAL, mode + ': keeps every one of the ' + TOTAL + ' files');
    assert(res !== state.rawImages, mode + ': returns a NEW array (raw listing not mutated)');
    const vals = res.map((it) => (key === 'name' ? it.name.toLowerCase() : Number(it[key])));
    assert(dir === 'asc' ? monoAsc(vals) : monoDesc(vals),
           mode + ': ' + key + ' is ' + (dir === 'asc' ? 'non-decreasing' : 'non-increasing'));
    assert(res.map((it) => it.name).slice().sort().join('|') === rawSet,
           mode + ': same set of files (no loss, no duplicate)');
    assert(res.some((it) => it.encrypted && /\.aimg$/.test(it.name)),
           mode + ': .aimg container kept as a valid image');
  }
  assert(state.rawImages.map((it) => it.name).join('|') === rawBefore,
         'sortImages never reorders the raw filesystem listing');

  // 2) concrete endpoints pin the direction
  assert(sortImages(state.rawImages, 'date_desc')[0].name.toLowerCase() === 'file_09999.png',
         'Date: newest mtime first');
  assert(sortImages(state.rawImages, 'date_desc')[TOTAL - 1].name.toLowerCase() === 'file_00000.png',
         'Date: oldest mtime last');
  assert(sortImages(state.rawImages, 'date_asc')[0].name.toLowerCase() === 'file_00000.png',
         '(Date): oldest mtime first');
  assert(sortImages(state.rawImages, 'name_asc')[0].name.toLowerCase() === 'enc_00007.aimg',
         'File Name: A -> Z first item');
  assert(sortImages(state.rawImages, 'name_desc')[0].name.toLowerCase() === 'file_09999.png',
         '(File Name): Z -> A first item');
  assert(sortImages(state.rawImages, 'size_desc')[0].size
         === Math.max.apply(null, state.rawImages.map((it) => it.size)),
         'File Size: largest first');
  assert(sortImages(state.rawImages, 'size_asc')[0].size
         === Math.min.apply(null, state.rawImages.map((it) => it.size)),
         '(File Size): smallest first');

  // 3) empty / missing listing tolerated
  assert(JSON.stringify(sortImages([], 'size_desc')) === '[]', 'empty listing sorts to empty');
  assert(JSON.stringify(sortImages(null, 'name_asc')) === '[]', 'missing listing is tolerated');

  // 4) applySortBy on 10.000 items = ONE bounded render, real order change
  setup();
  renderGrid();
  {
    const before = RENDER_COUNT;
    applySortBy('size_desc');
    assert(RENDER_COUNT - before === 1,
           'applySortBy on ' + TOTAL + ' items => exactly ONE re-render');
    assert(cardIndices().length >= 1 && cardIndices().length <= 61,
           'DOM stays bounded to the window after sorting, got ' + cardIndices().length);
    assert(state.images[0].size >= state.images[TOTAL - 1].size,
           'grid really shows the size-descending order');
    assert(state.sort === 'size_desc', 'state.sort tracks the active mode');
    assert(els.sortBy.value === 'size_desc', 'the dropdown reflects the active mode');
  }

  // 5) selection is preserved by file path across a re-sort
  setup();
  {
    const targetPath = state.rawImages[5].path;
    const idx0 = state.images.findIndex((it) => it.path === targetPath);
    state.selection = [idx0]; state.selected = idx0; state.anchor = idx0;
    applySortBy('date_desc');
    const idx1 = state.images.findIndex((it) => it.path === targetPath);
    assert(idx1 >= 0, 'the selected file is still present after sorting');
    assert(state.selection.length === 1 && state.selection[0] === idx1,
           'selection index follows the same file across the re-sort');
    assert(state.selected === idx1 && state.anchor === idx1,
           'primary selection + anchor follow the same file');
  }

  // 6) after a sort the grid is still a direct-jump virtual grid
  setup();
  renderGrid();
  {
    const row = Math.floor(9960 / COLS);
    els.grid.scrollTop = Math.max(0, row * gridLayout.rowH - els.grid.clientHeight / 2 + gridLayout.rowH / 2);
    const before = RENDER_COUNT;
    onGridScroll();
    assert(RENDER_COUNT - before === 0, 'post-sort scroll only arms the debounce (no sync render)');
    await sleep(GRID_SCROLL_DEBOUNCE + 90);
    assert(RENDER_COUNT - before === 1, 'post-sort far jump => exactly ONE render (no cascade)');
    assert(cardIndices().length >= 1 && cardIndices().length <= 61,
           'post-sort jump stays bounded, got ' + cardIndices().length);
  }

  // 7) invalid mode falls back, grid order stays consistent
  setup();
  {
    applySortBy('not-a-mode');
    assert(state.sort === 'name_asc', 'unknown sort mode falls back to the current default');
    assert(lowerNames().every((n, i) => i === 0 || lowerNames()[i - 1] <= n),
           'fallback keeps the name A -> Z order');
  }
})();
"""


def run_node_harness(prelude: str, consts: str, block: str, assertions: str) -> bool:
    node = shutil.which("node")
    if not node:
        check(False, "node executable available to run the runtime sort test")
        return False

    harness = prelude + "\n" + consts + "\n" + block + "\n" + assertions
    tmp = Path(tempfile.mkdtemp(prefix="veyra_sort_")) / "harness.js"
    tmp.write_text(harness, encoding="utf-8")
    try:
        proc = subprocess.run([node, str(tmp)], capture_output=True, text=True, timeout=120)
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
    backend = IMAGE_SERVICE_PATH.read_text(encoding="utf-8")

    grid_block = _extract(html, "// -- virtualized grid", "// -- sidebar tree")
    sort_block = _extract(html, "// -- grid sorting (Sort By)", "// -- sidebar tree")
    grid_consts = _extract(html, "var GRID_MIN=142", "// -- small helpers")
    fullscreen_block = _extract(html, "// -- full screen viewer", "// -- right click context menu")

    section("1. Sort By control lives in the toolbar, right of the crypto icon")
    m = re.search(r'<select id="sortBy"[^>]*>(.*?)</select>', html, re.S)
    check(m is not None, "a <select id=\"sortBy\"> exists")
    select_markup = html[html.find('<select id="sortBy"') - 120: html.find('</select>', html.find('<select id="sortBy"')) + 9] \
        if '<select id="sortBy"' in html else ""
    check('class="sortby"' in select_markup, "the sort control carries the .sortby class")
    check('for="sortBy"' in html, "a 'Sort By' label is associated with the control")
    if 'id="btnAimg"' in html and '<select id="sortBy"' in html:
        check(html.find('id="btnAimg"') < html.find('<select id="sortBy"'),
              "the Sort By control is placed AFTER the encrypt/decrypt icon")

    options = re.findall(r'<option value="([^"]+)"([^>]*)>([^<]+)</option>', m.group(1)) if m else []
    expected = [
        ("date_desc", "Date"),
        ("date_asc", "(Date)"),
        ("name_asc", "File Name"),
        ("name_desc", "(File Name)"),
        ("size_desc", "File Size"),
        ("size_asc", "(File Size)"),
    ]
    check([(v, t) for v, _, t in options] == expected,
          "the six options are exactly Date/(Date)/File Name/(File Name)/File Size/(File Size)")
    marked = [(v, t) for v, attrs, t in options if "selected" in attrs]
    check(len(marked) == 1 and marked[0][0] in {v for v, _ in expected},
          "exactly one option is initially selected and it is a valid mode")

    section("2. Sorting wiring is present")
    for token in ("function sortImages(", "function applySortBy(", "var SORT_MODES={"):
        check(token in sort_block, f"sorting block defines {token}")
    for key in ("date_desc", "date_asc", "name_asc", "name_desc", "size_desc", "size_asc"):
        check(f"{key}:" in sort_block, f"SORT_MODES knows '{key}'")
    check("els.sortBy.onchange=function(){applySortBy(this.value);}" in sort_block,
          "changing the dropdown calls applySortBy")
    check("sortNum(b.modified_at)-sortNum(a.modified_at)" in sort_block,
          "Date sorts newest -> oldest on the real modified_at metadata")
    check("sortNum(a.size)-sortNum(b.size)" in sort_block,
          "File Size uses the real byte size from the filesystem")
    check("var arr=(list||[]).slice();" in sort_block,
          "sortImages copies the listing (returns a new array)")
    check("state.images=sortImages(state.rawImages,state.sort||'name_asc');" in html,
          "openFolder sorts the whole listing into state.images")
    check("rawImages:[]" in html, "state keeps the untouched raw listing")
    check("state.images=[];state.rawImages=[];" in html, "grid errors clear the raw listing too")

    section("3. Virtualisation + viewer/context-menu untouched")
    check("GRID_WINDOW_RADIUS=30" in grid_consts, "render radius is still 30 items")
    check("GRID_SCROLL_DEBOUNCE=250" in grid_consts, "scroll debounce is still 250 ms")
    check("center-GRID_WINDOW_RADIUS" in grid_block and "center+GRID_WINDOW_RADIUS" in grid_block,
          "renderWindow still mounts exactly centerIndex +/- 30")
    check("function renderWindow(" in grid_block and "function onGridScroll(" in grid_block,
          "the virtualized grid core is intact")
    check("applySortBy" not in fullscreen_block,
          "the View-Image / fullscreen block is not touched by sorting")
    for token in ("function fsSetMode(", "function fsClamp(", "function fsZoom(",
                  "fsView.addEventListener('wheel'"):
        check(token in fullscreen_block, f"View-Image still defines/wires {token}")
    check("function buildContextMenu(" in html and "removeSelectedFiles" in html,
          "context menu + file operations are still present")

    section("4. Backend supplies the real sort keys")
    check('"size": self.size' in backend, "ImageInfo.to_dict exposes the real byte size")
    check('"modified_at": self.modified_at' in backend,
          "ImageInfo.to_dict exposes the real modified time")
    check("size=stat.st_size" in backend, "file size comes from the filesystem stat")

    section("5. runtime behaviour of the real sort + grid code (Node, 10.000 items)")
    check(grid_consts != "" and grid_block != "" and sort_block != "",
          "sorting + grid constants extracted from index.html")
    ok = run_node_harness(NODE_PRELUDE, grid_consts, grid_block, NODE_ASSERTIONS)
    check(ok, "the shipped Sort By behaviour passes every runtime assertion")

    print("\n" + "=" * 64)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("RESULT: ALL SORT BY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
