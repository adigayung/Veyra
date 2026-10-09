"""Permanent verification for the Veyra sidebar + File-Explorer layout.

Run with the project venv (from the project root)::

    venv\\Scripts\\python.exe test\\verify_sidebar_explorer.py

Task locked in by this suite: the left column (``.sidebar``) must contain ONLY
the File Explorer tree - the GROUPS panel that used to sit above it is removed,
so the Explorer owns the whole panel height.  Removing that panel must NOT break
load: every group control is optional and ``modules/group.js`` must stay
null-safe (no JS error at load), while the Group *subsystem* (DB service,
``/api/groups`` routes, the toolbar Group selector and the context-menu
Add/Remove actions) stays fully intact.

What this suite locks in:

 1. The shell holds no sidebar GROUPS panel markup (ids / classes removed).
 2. The File Explorer key DOM (sidebar / explorer / head / body / tree) is
    present, so browsing, tree navigation and selection keep working.
 3. ``modules/group.js`` is load-safe: with the panel absent every lookup is
    guarded (proved by running the REAL module under Node with a stub DOM that
    has none of the panel controls - it must not throw).
 4. The Group subsystem is untouched: the toolbar selector, the DB-backed
    routes and the service modules still exist.
 5. The sidebar / tree CSS is tidy: the explorer owns the full height (no stray
    separator), the tree scrolls in its own gutter-stable container and the
    chevron / icon / label columns line up.

Like the other ``verify_*.py`` suites this is a static audit of the *real*
shipped sources plus a small Node runtime harness; stdlib only.
"""

from __future__ import annotations

import os
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


def rule_block(css: str, selector: str) -> str:
    """Return the declaration block for a (first-level) ``selector`` (or "").

    Small, dependency-free parser mirroring the one used by the other
    explorer/UI audits: find the selector followed by ``{`` and return the text
    up to the matching ``}``.
    """
    idx = css.find(selector)
    while idx != -1:
        brace = css.find("{", idx)
        if brace == -1:
            return ""
        header = css[idx:brace]
        if "/*" not in header and header.strip().startswith(selector.strip()):
            end = css.find("}", brace)
            return css[brace + 1:end] if end != -1 else ""
        idx = css.find(selector, idx + 1)
    return ""


def decl_block(css: str, selector: str) -> str:
    """Return the block whose selector starts a line (``\\n.selector {``).

    Needed for selectors that also appear inside a comma-grouped selector list
    earlier in the file (e.g. ``.twisty, .folder, .label { ... }``): ``rule_block``
    would stop on that group, this one skips to the real standalone rule.
    """
    idx = css.find("\n" + selector + " {")
    if idx == -1:
        return ""
    brace = css.find("{", idx)
    end = css.find("}", brace)
    return css[brace + 1:end] if end != -1 and brace != -1 else ""


def run_node_group_module() -> bool:
    """Execute the REAL group.js under Node with a DOM that has NO panel nodes.

    The module must load and its panel painters must be no-ops instead of
    throwing ``Cannot set properties of null``.  This is the runtime proof of
    "no JS error at load" once the sidebar GROUPS panel is gone.
    """
    node = shutil.which("node")
    if not node:
        check(False, "node executable available for the group-module load test")
        return False

    prelude = r"""
'use strict';
// DOM with NONE of the sidebar GROUPS controls: every getElementById -> null.
var els = { groupFilter: null };
var document = {
  getElementById() { return null; },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {},
};
var groupsState = { list: [], selected: null, detail: null };
var state = { group: null, groupImages: [] };
function toast() {}
function esc(s) { return String(s == null ? '' : s); }
function getJSON() { return Promise.resolve({ data: {} }); }
function postJSON() { return Promise.resolve({ data: {} }); }
function prompt() { return null; }
function confirm() { return false; }
"""

    assertions = r"""
function ok(cond, label) { console.log((cond ? '  [PASS] ' : '  [FAIL] ') + label); if (!cond) process.exitCode = 1; }
var threw = null;
try {
  renderGroups();        // groupList host is null -> must be a no-op
  renderGroupDetail();   // groupDetail panel is null -> must be a no-op
  renderGroupFilter();   // els.groupFilter is null -> must be a no-op
} catch (e) { threw = e; }
ok(threw === null, 'group.js panel painters are null-safe (no load-time error)'
   + (threw ? ' -- threw: ' + threw.message : ''));
ok(typeof renderGroups === 'function' && typeof renderGroupDetail === 'function',
   'the group panel painters still exist (kept for the subsystem)');
ok(typeof renderGroupFilter === 'function' && typeof selectGroupDataset === 'function',
   'the toolbar Group selector wiring is still defined');
ok(typeof addSelectionToGroup === 'function' && typeof removeSelectionFromGroup === 'function',
   'the context-menu group actions are still defined');
"""

    harness = prelude + "\n" + ui.js_for("modules/group") + "\n" + assertions
    tmp = Path(tempfile.mkdtemp(prefix="veyra_sidebar_")) / "harness.js"
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


def serve_shell() -> None:
    """Boot the real Flask app and fetch the shell + every referenced asset.

    A missing/404 asset is exactly what produces a load-time JS/CSS error, so
    this proves the shell still loads cleanly once the GROUPS panel is gone.
    Uses an isolated temp DB + keystore so the developer's data is untouched.
    """
    work = Path(tempfile.mkdtemp(prefix="veyra_sidebar_serve_"))
    os.environ["VEYRA_DB"] = str(work / "veyra.db")
    os.environ["VEYRA_KEYSTORE"] = str(work / "keystore.bin")
    os.environ["VEYRA_DEFAULT_PATH"] = str(work / "browse")
    try:
        from veyra.application import create_app

        app = create_app()
        client = app._flask_app.test_client()

        resp = client.get("/")
        served = resp.data.decode("utf-8")
        check(resp.status_code == 200, "GET / returns 200")
        check('class="explorer"' in served and 'id="tree"' in served,
              "the served shell renders the File Explorer tree")
        check('id="groupsPanel"' not in served,
              "the served shell has no GROUPS panel")

        assets = ui.loaded_stylesheets(served) + ui.loaded_scripts(served)
        check(len(assets) >= 20, f"the served shell references its assets ({len(assets)})")
        broken = [u for u in assets if client.get(u).status_code != 200]
        check(not broken, f"every referenced css/js asset resolves (broken: {broken})")
    except Exception as exc:  # pragma: no cover - environment failure
        check(False, f"the Flask app booted for the HTTP check ({exc})")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    shell = ui.shell_html()
    css = ui.ui_css()

    # -- 1. the sidebar GROUPS panel is gone --------------------------------
    section("1. the sidebar holds no GROUPS panel")
    for token in (
        'id="groupsPanel"',
        'class="groups"',
        'id="groupList"',
        'id="groupName"',
        'id="btnCreateGroup"',
        'id="btnAddSelected"',
        'id="groupDetail"',
    ):
        check(token not in shell, f"the shell no longer contains {token}")

    # -- 2. the File Explorer key DOM is present ----------------------------
    section("2. the File Explorer key DOM is present")
    for token in (
        'class="sidebar"',
        'class="explorer"',
        'class="explorer-head"',
        'id="explorerBody"',
        'class="tree"',
        'id="tree"',
        'id="pathInput"',
        'id="grid"',
    ):
        check(token in shell, f"the shell still contains {token}")
    check(shell.count('class="explorer"') == 1, "the sidebar has exactly one Explorer panel")

    # -- 3. group.js is load-safe without the panel -------------------------
    section("3. modules/group.js is null-safe when the panel is absent")
    group_js = ui.js_for("modules/group")
    check("if (!host) return;" in group_js,
          "renderGroups guards a missing list host")
    check("if (!panel) return;" in group_js,
          "renderGroupDetail guards a missing detail panel")
    check("var btnCreateGroup = document.getElementById('btnCreateGroup');" in group_js
          and "if (btnCreateGroup) { btnCreateGroup.onclick = createGroup; }" in group_js,
          "the create control is wired only when present")
    check("if (btnAddSelected) { btnAddSelected.onclick = addSelectedToGroup; }" in group_js,
          "the add-selected control is wired only when present")
    check("document.getElementById('btnCreateGroup').onclick" not in group_js,
          "no unguarded `.onclick` on a panel control remains")
    run_node_group_module()

    # -- 4. the Group subsystem is untouched --------------------------------
    section("4. the Group subsystem + toolbar selector are untouched")
    check("/api/groups" in group_js and "/api/groups/detail?id=" in group_js,
          "group.js still talks to the DB-backed group API")
    check('id="groupFilter"' in shell, "the toolbar Group selector is still in the shell")
    check("function loadGroups(" in group_js, "loadGroups is still defined")
    check("loadGroups();" in ui.js_for("boot"), "boot still initialises the group list")
    for path in (
        "veyra/services/group_service.py",
        "veyra/services/group_models.py",
        "veyra/services/database.py",
    ):
        check((ui.ROOT / path).is_file(), f"{path} still exists (subsystem kept)")
    routes = (ui.ROOT / "veyra" / "web" / "routes.py").read_text(encoding="utf-8")
    check("/api/groups" in routes, "routes.py still exposes /api/groups")

    # -- 5. sidebar / tree CSS is tidy --------------------------------------
    section("5. the sidebar / tree CSS is tidy and consistent")
    explorer = rule_block(css, ".explorer")
    check("border-top" not in explorer,
          ".explorer has no stray top border (nothing stacks above it now)")
    check("flex: 1 1 auto" in explorer, ".explorer fills the whole sidebar height")
    body = rule_block(css, ".explorer-body")
    check("overflow: auto" in body, ".explorer-body owns the tree scroll")
    check("scrollbar-gutter: stable" in body,
          "the tree reserves its scrollbar gutter (rows never jump)")
    li = rule_block(css, ".tree li")
    check("min-height" in li, "tree rows have a uniform minimum height")
    twisty = decl_block(css, ".twisty")
    folder = decl_block(css, ".folder")
    check("width: 16px" in twisty and "width: 16px" in folder,
          "chevron and folder icon share one column width (aligned rows)")
    sidebar_css = rule_block(css, ".sidebar")
    check("width: var(--sidebar-w)" in sidebar_css, ".sidebar uses the shared width token")
    check("overflow: hidden" in sidebar_css, ".sidebar clips its content (clean edges)")

    # -- 6. the served shell + every asset load over real HTTP --------------
    section("6. the served shell loads (every css/js asset resolves)")
    serve_shell()

    print("\n" + "=" * 64)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("RESULT: ALL SIDEBAR / FILE-EXPLORER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
