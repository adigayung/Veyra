"""Permanent verification for the Veyra Group-Filter + Sort integration.

Run with the project venv::

    J:\\Veyra\\venv\\Scripts\\python.exe verify_groupsort.py

The Image Grid gained a ``Group`` selector next to ``Sort By``.  ``Normal``
(always first) shows the live File-Explorer listing; every group below it
switches the grid to that group's members (a logical collection that may span
folders/drives).  The last three entries are actions (Add / Edit / Remove).  The
whole pipeline is::

    Normal / Group filter  ->  Sort By  ->  ordered image list  ->  Image View

so the grid and the Image View navigation can never disagree, and the Image View
never leaves the active group.

What this suite locks in:

 1. The toolbar exposes ``#groupFilter`` to the RIGHT of ``#sortBy``, with
    ``Normal`` as the first option and the Add/Edit/Remove actions below.
 2. The Group list + members come from the EXISTING DB-backed Group subsystem
    (``/api/groups`` + ``/api/groups/detail``); no second model / database.
 3. ``activeSource()``/``rebuildImages()`` derive ``state.images`` from the
    active dataset, so Group + Sort compose (all six sort modes work inside a
    group) and returning to Normal restores the explorer listing.
 4. ``groupMemberToImage`` maps a DB member onto the same image shape the grid
    consumes (so Sort By / Image View work identically for groups).
 5. The virtual grid stays bounded on a large group.
 6. The Image-View wheel walks the ordered list forward on wheel-DOWN and
    backward on wheel-UP (the direction fixed in this task) and never flips with
    the sort kind.

Layer 1 is a static wiring audit of ``veyra/index.html``.  Layer 2 runs the
REAL extracted grid + sort + group-filter block under Node with a small DOM stub
so it fails if the shipped code regresses.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "test"))

import verify_ui_sources as ui  # noqa: E402

HTML_PATH = ui.HTML_PATH
STATIC_ROOT = ui.STATIC_ROOT

#: The frontend is modular now: UI audits run against the shell + modules, and
#: the Node harnesses execute the REAL shipped sources.
UI = ui.ui_source()                       # shell + every loaded module
GRID_JS = ui.js_for("modules/grid")       # Image Grid module
SORT_JS = ui.js_for("modules/sort")       # Sort By module
GROUP_JS = ui.js_for("modules/group")     # Group filter module
CORE_JS = ui.js_for("core")               # constants + shared helpers

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
# Node harness: run the REAL grid + sort + group-filter block with a DOM stub   #
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

// PRELUDE-ONLY stubs.  The REAL `state`, `els` and grid constants come from
// core/state.js, which is concatenated right after this prelude (so they must
// not be re-declared here - that would be a hard syntax error).
var els = {
  grid: gridEl,
  sortBy: { value: 'name_asc', onchange: null },
  groupFilter: { innerHTML: '', value: '', onchange: null },
  statusFile: { textContent: '' }, statusFolders: { textContent: '' },
  statusFiles: { textContent: '' }, selectedStatus: { textContent: '' },
  viewCount: { textContent: '' },
};

const document = {
  createElement(tag) {
    return { tagName: tag, className: '', id: '', style: {}, appendChild() {}, querySelector() { return null; } };
  },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {},
  getElementById(id) {
    if (id === 'grid') return gridEl;
    if (id === 'gridInner') return innerEl;
    if (id === 'gridTop') return topPad;
    if (id === 'gridBottom') return bottomPad;
    // core/state.js resolves these from the shell; the toolbar selector and the
    // status bar exist in the real app, so the harness provides them too
    // (otherwise renderGroupFilter()/updateStatus() cannot run).
    if (id === 'groupFilter') return { innerHTML: '', value: '', onchange: null };
    if (id === 'statusFile' || id === 'statusFolders' || id === 'statusFiles'
        || id === 'selectedStatus' || id === 'viewCount') return { textContent: '' };
    return null;
  },
};

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
  return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
function thumbUrl(p) { return '/api/thumb?path=' + encodeURIComponent(p); }
function fmtSize(bytes) { return bytes == null ? '' : String(bytes); }
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
function getSelectedImages() {
  return state.selection.slice().sort(function (a, b) { return a - b; })
    .map(function (i) { return state.images[i]; }).filter(function (x) { return !!x; });
}
// stubs used only by the group-filter block (never exercised in the sync tests)
function renderGroups() {}
function renderGroupDetail() {}
function loadGroups() {}
function postJSON() { return Promise.resolve({ data: {} }); }
function getJSON() { return Promise.resolve({ data: { ok: true, group: { name: 'Gajah', members: [] } } }); }
function prompt() { return null; }
function confirm() { return false; }
"""

NODE_ASSERTIONS = r"""
function assert(cond, label) {
  console.log((cond ? '  [PASS] ' : '  [FAIL] ') + label);
  if (!cond) process.exitCode = 1;
}
const names = () => state.images.map((it) => it.name);
const cardCount = () => currentCards.length;
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function img(name, extra) {
  return Object.assign({ path: 'C:/p/' + name, name: name, size: 10,
                         modified_at: 0, type: 'PNG', encrypted: false,
                         width: 8, height: 8 }, extra || {});
}

// ---- groupMemberToImage maps a DB member onto the grid image shape ---------
{
  const m = { name: 'C.png', path: 'D:/x/C.png', size: 123, extension: 'png',
              modified: '2024-01-02T03:04:05+00:00', is_aimg: true, id: 9 };
  const out = groupMemberToImage(m);
  assert(out.name === 'C.png' && out.path === 'D:/x/C.png' && out.size === 123,
         'groupMemberToImage: name/path/size carried over');
  assert(out.type === 'PNG', 'groupMemberToImage: extension -> upper-case type');
  assert(out.encrypted === true, 'groupMemberToImage: is_aimg -> encrypted flag');
  assert(out.modified_at === Date.parse('2024-01-02T03:04:05+00:00'),
         'groupMemberToImage: ISO modified -> epoch ms (Sort By Date works)');
}

// ---- renderGroupFilter: Normal first, then groups, then the actions --------
{
  groupsState.list = [
    { id: 7, name: 'Gajah', member_count: 6 },
    { id: 8, name: 'Kucing', member_count: 2 },
  ];
  state.group = 7;
  renderGroupFilter();
  const html = els.groupFilter.innerHTML;
  assert(html.indexOf('<option value="">Normal</option>') === 0,
         'renderGroupFilter: "Normal" is the FIRST option (value "")');
  assert(html.indexOf('Gajah (6)') > 0 && html.indexOf('Kucing (2)') > 0,
         'renderGroupFilter: every DB group is listed with its member count');
  assert(html.indexOf('>Add Group<') > 0 && html.indexOf('>Edit Group<') > 0
         && html.indexOf('>Remove Group<') > 0,
         'renderGroupFilter: Add/Edit/Remove Group actions are present');
  assert((html.match(/disabled/g) || []).length >= 2,
         'renderGroupFilter: separators are disabled placeholders');
  assert(els.groupFilter.value === '7', 'renderGroupFilter: reflects the active group');
  state.group = null; renderGroupFilter();
  assert(els.groupFilter.value === '', 'renderGroupFilter: Normal selected -> value ""');
}

// ---- activeSource switches between Normal and the active group -------------
{
  state.rawImages = [img('A.png'), img('B.png')];
  state.groupImages = [img('Z.png')];
  state.group = null;
  assert(activeSource() === state.rawImages, 'activeSource: Normal -> explorer listing');
  state.group = 3;
  assert(activeSource() === state.groupImages, 'activeSource: group active -> group members');
  state.group = null;
}

// ---- Group + Sort compose; returning to Normal restores the listing --------
{
  const raw = ['A.png', 'B.png', 'C.png', 'D.png', 'E.png'].map(function (n) {
    return img(n, { size: 10, modified_at: 1000 });
  });
  const grp = ['A.png', 'B.png', 'C.png', 'D.png', 'E.png', 'F.png'].map(function (n, i) {
    return img(n, { size: (i + 1) * 100, modified_at: 1000 + i });
  });
  state.rawImages = raw;
  state.groupImages = grp;
  state.sort = 'name_asc';
  state.selection = []; state.selected = null; state.anchor = null;

  // Normal
  state.group = null; rebuildImages();
  assert(eq(names(), ['A.png', 'B.png', 'C.png', 'D.png', 'E.png']),
         'Normal: grid shows the active folder listing (A..E)');

  // Group: only members, A -> Z
  state.group = 1; applySortBy('name_asc');
  assert(eq(names(), ['A.png', 'B.png', 'C.png', 'D.png', 'E.png', 'F.png']),
         'Group + File Name: only the group members (A..F), A -> Z');
  assert(state.images.length === 6 && state.rawImages.length === 5,
         'Group active: the Normal/folder dataset never leaks in');

  // every sort mode inside the group
  applySortBy('name_desc');
  assert(eq(names(), ['F.png', 'E.png', 'D.png', 'C.png', 'B.png', 'A.png']),
         'Group + (File Name): Z -> A (direction not flipped by the sort kind)');
  applySortBy('size_desc');
  assert(eq(names(), ['F.png', 'E.png', 'D.png', 'C.png', 'B.png', 'A.png']),
         'Group + File Size: largest -> smallest');
  applySortBy('size_asc');
  assert(eq(names(), ['A.png', 'B.png', 'C.png', 'D.png', 'E.png', 'F.png']),
         'Group + (File Size): smallest -> largest');
  applySortBy('date_desc');
  assert(eq(names(), ['F.png', 'E.png', 'D.png', 'C.png', 'B.png', 'A.png']),
         'Group + Date: newest -> oldest');
  applySortBy('date_asc');
  assert(eq(names(), ['A.png', 'B.png', 'C.png', 'D.png', 'E.png', 'F.png']),
         'Group + (Date): oldest -> newest');

  // selection preserved by path across a group re-sort
  const target = state.images[3].path;
  state.selection = [3]; state.selected = 3; state.anchor = 3;
  applySortBy('name_desc');
  const idx = state.images.findIndex(function (it) { return it.path === target; });
  assert(idx >= 0 && state.selection.length === 1 && state.selection[0] === idx,
         'Group re-sort keeps the selection on the same file (by path)');

  // back to Normal -> explorer listing + its own sort
  state.group = null; state.sort = 'name_asc'; rebuildImages();
  assert(eq(names(), ['A.png', 'B.png', 'C.png', 'D.png', 'E.png']),
         'back to Normal: the Image Grid follows the explorer dataset again');
}

// ---- the virtual grid stays bounded on a large GROUP -----------------------
{
  const big = [];
  for (let i = 0; i < 900; i++) {
    big.push(img('g_' + String(i).padStart(4, '0') + '.png', { size: i, modified_at: i }));
  }
  state.groupImages = big;
  state.group = 5;
  state.sort = 'name_asc';
  state.images = sortImages(state.groupImages, state.sort);
  renderGrid();
  assert(cardCount() >= 1 && cardCount() <= 61,
         'large group: the Image Grid mounts a bounded window (<= 61), got ' + cardCount());
  const before = RENDER_COUNT;
  applySortBy('size_desc');
  assert(RENDER_COUNT - before === 1,
         'large group: a sort is a single bounded re-render');
  assert(cardCount() >= 1 && cardCount() <= 61,
         'large group: still bounded after sorting, got ' + cardCount());
  state.group = null;
}
"""


def run_node_harness(prelude: str, grid_consts: str, grid_block: str, group_block: str,
                     assertions: str) -> bool:
    node = shutil.which("node")
    if not node:
        check(False, "node executable available to run the runtime group-filter test")
        return False

    harness = (prelude + "\n" + grid_consts + "\n" + grid_block + "\n"
               + group_block + "\n" + assertions)
    tmp = Path(tempfile.mkdtemp(prefix="veyra_gs_")) / "harness.js"
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
    html = UI

    grid_consts = CORE_JS
    grid_block = GRID_JS + "\n" + SORT_JS
    group_block = GROUP_JS

    section("1. Group selector lives in the toolbar, right of Sort By")
    check('<select id="groupFilter"' in html, "a <select id=\"groupFilter\"> exists")
    if '<select id="groupFilter"' in html and '<select id="sortBy"' in html:
        check(html.find('<select id="sortBy"') < html.find('<select id="groupFilter"'),
              "the Group control is placed AFTER the Sort By control")
    check('for="groupFilter"' in html, "a 'Group' label is associated with the control")
    gf = html[html.find('<select id="groupFilter"'): html.find('</select>', html.find('<select id="groupFilter"')) + 9] \
        if '<select id="groupFilter"' in html else ""
    check('<option value="">Normal</option>' in gf,
          "the control ships 'Normal' as its initial (first) option")
    check('class="groupfilter"' in gf or 'class="groupfilter"' in html,
          "the Group control carries its own class")

    section("2. Group list + members come from the EXISTING DB subsystem")
    check("/api/groups" in html, "the selector is fed by GET /api/groups")
    check("/api/groups/detail?id=" in html, "members come from GET /api/groups/detail")
    check("/api/groups/add" in html and "/api/groups/remove" in html,
          "Add/Remove membership reuse the existing group API")
    for token in ("function renderGroupFilter(", "function selectGroupDataset(",
                  "function resetToNormalDataset(", "function applyGroupFilter(",
                  "function groupMemberToImage("):
        check(token in group_block, f"group-filter module defines {token}")
    check("renderGroupFilter();" in html, "loadGroups refreshes the Group selector")

    section("3. Normal / Group -> Sort pipeline")
    check("function activeSource(" in grid_block and "function rebuildImages(" in grid_block,
          "the active-source + rebuild pipeline is present")
    check("if (state.group != null) { state.images = sortImages(activeSource(), state.sort || 'name_asc'); }" in html,
          "openFolder keeps the active group dataset (Normal listing never leaks in)")
    check("state.images = sortImages(state.rawImages, state.sort || 'name_asc');" in html,
          "the Normal listing is still sorted into state.images (regression guard)")
    check("group: null" in html and "groupImages: []" in html,
          "state tracks the active group + its member images")
    check("els.sortBy.onchange = function () { applySortBy(this.value); }" in html,
          "Sort By still re-sorts the active dataset")

    section("4. Image-View wheel walks the ordered list the right way")
    wheel = re.search(r"fsView\.addEventListener\('wheel'.*?\},\s*\{ passive: false \}\);", html, re.S)
    check(wheel is not None, "the Image-View wheel handler is present")
    check(wheel is not None and "if (e.deltaY > 0) fsStep(1); else fsStep(-1);" in wheel.group(0),
          "wheel DOWN -> next (fsStep(1)); wheel UP -> previous (fsStep(-1))")
    check(".sort(" not in (wheel.group(0) if wheel else ""),
          "the wheel does not re-sort: it follows the active ordered list")

    section("5. runtime behaviour of the real grid + sort + group code (Node)")
    check(grid_consts != "" and grid_block != "" and group_block != "",
          "grid + sort + group-filter modules loaded from the shipped sources")
    ok = run_node_harness(NODE_PRELUDE, grid_consts, grid_block, group_block, NODE_ASSERTIONS)
    check(ok, "the shipped Group-Filter + Sort behaviour passes every runtime assertion")

    print("\n" + "=" * 64)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("RESULT: ALL GROUP-FILTER + SORT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
