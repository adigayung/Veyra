"""Permanent verification for the File-Explorer's "centre the active node" fix.

Run with the project venv (from the project root)::

    venv\\Scripts\\python.exe test\\verify_tree_scroll_active.py

Task locked in by this suite
----------------------------
Changing the active path from the address / path bar already SELECTED the
matching tree node, but the tree's own scroll viewport was never moved: when
the folder sat deep in the tree (a lazily / asynchronously expanded level) the
selection changed *off-screen*, so the visual focus no longer followed the
active path.

Fix: after the node is marked selected, ``syncTree`` now also calls
``focusActiveNode`` which scrolls that node to the vertical CENTRE of the tree
viewport (``scrollIntoView({ block: 'center', inline: 'nearest' })``), deferred
one animation frame so a just-mounted node is laid out first.  It is guarded so
a node that is not yet in the DOM (or an empty path) is a silent no-op, and it
never touches the Image Grid - only the node's own scroll ancestor
(``.explorer-body``) moves.

What this suite checks:

 A. ``modules/explorer.js`` wires the behaviour: ``focusActiveNode`` exists,
    scrolls with ``block: 'center'`` / ``inline: 'nearest'`` on a rAF tick, is
    guarded, and ``syncTree`` calls it (after ``markSelectedDir``) on BOTH the
    refresh and the reveal branches - while still expanding ancestors.
 B. The REAL shipped ``modules/explorer.js`` is executed under Node with a DOM
    stub, proving at runtime that the node is centred exactly once, that a
    missing node / empty path never throws, and that ``syncTree`` both selects
    and centres the active node.
 C. The stable explorer wiring is untouched (``grid.js`` still selects + syncs
    the tree, the shell still loads the module).

Like the other ``verify_*.py`` suites this is a static audit of the real shipped
sources plus a small Node runtime harness; stdlib only.
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

FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  [PASS] " if cond else "  [FAIL] ") + label)
    if not cond:
        FAILS.append(label)


def section(title: str) -> None:
    print("\n== " + title + " ==")


def run_node_explorer_module() -> None:
    """Execute the REAL explorer.js under Node with a DOM stub.

    This proves the *runtime* behaviour (not just that some token appears in the
    source): the mounted node is centred exactly once with the right options, a
    missing node is a guarded no-op, and ``syncTree`` selects + centres.
    """
    node = shutil.which("node")
    if not node:
        check(False, "node executable available for the explorer scroll harness")
        return

    prelude = r"""
'use strict';
var SCROLL = [];
function makeNode(path) {
  return {
    dataset: { path: path, loaded: '0' },
    classList: {
      _s: {},
      add: function (c) { this._s[c] = true; },
      remove: function (c) { delete this._s[c]; },
      contains: function (c) { return !!this._s[c]; }
    },
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    appendChild: function (c) { return c; },
    setAttribute: function () {},
    removeAttribute: function () {},
    hasAttribute: function () { return false; },
    remove: function () {},
    addEventListener: function () {},
    scrollIntoView: function (opts) { SCROLL.push({ path: path, opts: opts }); },
    textContent: ''
  };
}
var TREE = {
  _nodes: [],
  querySelectorAll: function () { return this._nodes; },
  querySelector: function () { return null; }
};
var els = {
  tree: TREE,
  pathInput: { addEventListener: function () {} },
  navUp: {}, navBack: {}, navForward: {}, refreshBtn: {}
};
function getJSON() { return Promise.resolve({ data: { ok: true, folders: [] } }); }
function requestAnimationFrame(fn) { fn(); return 1; }
function toast() {}
"""

    assertions = r"""
function ok(cond, label) { console.log((cond ? '  [PASS] ' : '  [FAIL] ') + label); if (!cond) process.exitCode = 1; }

(async function () {
  ok(typeof focusActiveNode === 'function',
     'the shipped explorer defines focusActiveNode');
  ok(typeof syncTree === 'function', 'the shipped explorer still defines syncTree');

  // -- mounted node is centred exactly once with the right options --------
  SCROLL.length = 0;
  TREE._nodes = [makeNode('J:\\a\\b')];
  var threw = null;
  try { focusActiveNode('J:\\a\\b'); } catch (e) { threw = e; }
  ok(threw === null, 'focusActiveNode on a mounted node never throws');
  ok(SCROLL.length === 1, 'the active node is scrolled exactly once');
  ok(SCROLL[0] && SCROLL[0].opts && SCROLL[0].opts.block === 'center',
     "the scroll centres the node (block: 'center')");
  ok(SCROLL[0] && SCROLL[0].opts && SCROLL[0].opts.inline === 'nearest',
     "the scroll avoids sideways jumps (inline: 'nearest')");

  // -- missing node (not rendered yet) is a guarded no-op -----------------
  SCROLL.length = 0;
  TREE._nodes = [makeNode('J:\\other')];
  threw = null;
  try { focusActiveNode('J:\\a\\b'); } catch (e) { threw = e; }
  ok(threw === null, 'focusActiveNode is a no-op (no throw) when the node is not rendered');
  ok(SCROLL.length === 0, 'nothing scrolls when the target node is absent');

  // -- empty path is ignored ---------------------------------------------
  threw = null;
  try { focusActiveNode(''); } catch (e) { threw = e; }
  ok(threw === null, 'focusActiveNode ignores an empty path');

  // -- integration: syncTree selects AND centres after the DOM settles ----
  SCROLL.length = 0;
  var node = makeNode('J:\\deep\\nested\\folder');
  TREE._nodes = [node];
  await syncTree('J:\\deep\\nested\\folder');
  ok(node.classList.contains('selected'),
     'syncTree keeps the active path selected in the tree');
  ok(SCROLL.length === 1 && SCROLL[0].opts && SCROLL[0].opts.block === 'center',
     'syncTree scrolls the active node to the centre after the DOM settles');

  console.log('  HARNESS_DONE');
})().catch(function (e) {
  console.log('  [FAIL] harness threw: ' + (e && e.message));
  process.exitCode = 1;
  console.log('  HARNESS_DONE');
});
"""

    harness = prelude + "\n" + ui.js_for("modules/explorer") + "\n" + assertions
    tmp = Path(tempfile.mkdtemp(prefix="veyra_tree_scroll_")) / "harness.js"
    tmp.write_text(harness, encoding="utf-8")
    done = False
    try:
        proc = subprocess.run([node, str(tmp)], capture_output=True, text=True, timeout=120)
    except Exception as exc:  # pragma: no cover - environment failure
        check(False, f"node harness ran ({exc})")
        return
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)

    out = proc.stdout + proc.stderr
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("[PASS]"):
            print("  " + s)
        elif s.startswith("[FAIL]"):
            check(False, s.split("[FAIL]", 1)[1].strip())
        elif "HARNESS_DONE" in s:
            done = True
        elif "Error" in s or "error" in s:
            print("  " + s)
    check(done, "the explorer scroll harness ran to completion")


def main() -> int:
    shell = ui.shell_html()
    explorer_js = ui.js_for("modules/explorer")
    grid_js = ui.js_for("modules/grid")

    # -- A. the shipped explorer wires the centre-the-active-node behaviour --
    section("A. explorer.js centres the active tree node")
    check("function focusActiveNode" in explorer_js,
          "a dedicated focusActiveNode helper exists (single responsibility)")
    check("node.scrollIntoView({ block: 'center', inline: 'nearest' })" in explorer_js,
          "the node is scrolled to the vertical centre (block:center, inline:nearest)")
    check("requestAnimationFrame" in explorer_js and "setTimeout(fn, 0)" in explorer_js,
          "the scroll is deferred one frame (with a setTimeout fallback)")
    check("if (!path) return;" in explorer_js,
          "an empty / null path is guarded (no error)")
    check("if (!node) return;" in explorer_js,
          "a node that is not in the DOM yet is a guarded no-op (no error)")

    section("A2. syncTree selects AND centres on every path change")
    check(explorer_js.count("focusActiveNode(path);") == 2,
          "syncTree centres the node on BOTH the refresh and the reveal branch")
    check("refreshTreeNode(path).then(function () {" in explorer_js,
          "the existing-node branch waits for the child refresh before focusing")
    check("markSelectedDir(path);" in explorer_js,
          "the active path is still marked selected")
    check("revealTreePath(path).then(function () {" in explorer_js,
          "the new-node branch still expands the ancestors before focusing")

    # -- B. the real module runs and behaves (Node runtime harness) ---------
    section("B. the shipped explorer.js behaves under Node")
    run_node_explorer_module()

    # -- C. stable explorer wiring is untouched -----------------------------
    section("C. the stable explorer / grid wiring is intact")
    check("markSelectedDir(data.path);" in grid_js and "syncTree(data.path);" in grid_js,
          "openFolder still selects the folder and syncs the tree")
    check(any("explorer.js" in s for s in ui.loaded_scripts(shell)),
          "the shell still loads modules/explorer.js")

    print("\n" + "=" * 64)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("RESULT: ALL EXPLORER ACTIVE-NODE CENTRING CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
