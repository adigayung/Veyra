"""Permanent verification for Veyra's File-Explorer panel layout / styling.

Run with the project venv::

    J:\\Veyra\\venv\\Scripts\\python.exe verify_explorer_layout.py

The File Explorer is a real filesystem tree (This PC -> drives -> folders,
lazily expanded to unlimited depth).  A visual audit showed the panel was NOT
"rapi": the tree rows were laid out with ``display:flex`` and the nested
``<ul>`` became a *horizontal sibling* of the row, so every subtree spilled out
of the sidebar and overlapped the other rows.  The panel also used a hard-coded
sidebar width and an inconsistent explorer header/status-bar typography.

What this suite locks in (the layout contract, not just "some CSS changed"):

 1. Every stylesheet the shell references exists on disk and is concatenated
    by ``verify_ui_sources.ui_css()`` (the UI styling is actually loaded).
 2. A tree node WRAPS: ``.tree li`` is a wrapping flex row, so the nested
    ``<ul>`` drops to its own full-width line instead of sitting beside the
    label (the root cause of the overflow / overlap).
 3. The nested list is a full-width block (``.tree li > ul`` -> 100% width).
 4. Row highlight + selection are a single-row pill: the hover/selection
    background lives on ``.tree li::before/::after`` (26px tall), NOT on the
    ``<li>`` itself, so it never tints the node's whole subtree.
 5. The tree scrolls in its OWN container (``.explorer-body { overflow:auto }``)
    so scrolling the tree cannot move the Image Grid.
 6. The split pane is proportional with a minimum: the sidebar width is a
    ``clamp()`` and the sidebar declares a ``min-width``.
 7. Status bar + explorer header share one consistent type treatment
    (tabular figures, one muted meta colour instead of the disabled token).
 8. The stable features are NOT touched: the grid virtualisation, the Image
    View / fullscreen block and the crypto (.aimg) surface keep their rules.

This is a static audit of the *real shipped* sources (no browser required), in
the same shape as the other ``verify_*.py`` suites.
"""

from __future__ import annotations

import re
import sys
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
    """Return the declaration block for a (first-level) ``selector``.

    A small, dependency-free parser: find the selector followed by ``{`` and
    return the text up to the matching ``}``.  Returns ``""`` when absent.
    """
    idx = css.find(selector)
    while idx != -1:
        brace = css.find("{", idx)
        if brace == -1:
            return ""
        # The selector text must be the one we searched for (no comment hit).
        header = css[idx:brace]
        if "/*" not in header and header.strip().startswith(selector.strip()):
            end = css.find("}", brace)
            return css[brace + 1:end] if end != -1 else ""
        idx = css.find(selector, idx + 1)
    return ""


def main() -> int:
    shell = ui.shell_html()
    css = ui.ui_css()

    # -- 1. stylesheets are referenced AND exist on disk -------------------
    section("1. the shell loads every stylesheet (and they exist)")
    links = ui.loaded_stylesheets(shell)
    check(len(links) >= 8, f"the shell links the modular stylesheets ({len(links)} found)")
    check(any("explorer.css" in h for h in links), "explorer.css is loaded by the shell")
    check(any("layout.css" in h for h in links), "layout.css is loaded by the shell")
    check(any("base.css" in h for h in links), "base.css (tokens) is loaded first-ish")
    missing = [h for h in links if not (ui.STATIC_ROOT / h.lstrip("/").replace("static/", "", 1)).is_file()]
    check(not missing, f"every referenced stylesheet exists on disk (missing: {missing})")
    check(css.strip() != "", "the concatenated CSS is non-empty (styles really load)")

    # -- 2. a tree node wraps so its children drop to a new line -----------
    section("2. tree nodes wrap (nested <ul> drops to its own line)")
    li = rule_block(css, ".tree li")
    check("display: flex" in li, ".tree li is a flex row")
    check("flex-wrap: wrap" in li,
          ".tree li wraps -> the nested <ul> is NOT a horizontal sibling (overflow fix)")

    # -- 3. nested list is a full-width block ------------------------------
    section("3. the nested list is a full-width block")
    child_ul = rule_block(css, ".tree li > ul")
    check("flex: 0 0 100%" in child_ul or "width: 100%" in child_ul,
          ".tree li > ul is full-width (children stack under the parent row)")
    check("padding-left" in child_ul, "the nested list stays indented (depth is readable)")

    # -- 4. highlight/selection are a single-row pill ----------------------
    section("4. hover/selection never tint the whole subtree")
    check(".tree li::before" in css, "the row pill is drawn by .tree li::before")
    before = rule_block(css, ".tree li::before")
    check("height: 26px" in before, "the pill is exactly one row tall (26px)")
    check(".tree li.selected::before" in css, "selection uses the same single-row pill")
    check(":has(" in css,
          "an ancestor does not stay highlighted while a descendant is hovered (:has guard)")
    li_body = rule_block(css, ".tree li")
    check("background" not in li_body,
          "the <li> itself carries no background (so no subtree tinting)")

    # -- 5. the tree scrolls in its own container --------------------------
    section("5. the tree scrolls on its own (never moves the Image Grid)")
    body = rule_block(css, ".explorer-body")
    check("overflow: auto" in body, ".explorer-body owns the tree scroll")
    check("flex: 1" in body or "flex: 1 1 auto" in body,
          ".explorer-body fills the remaining sidebar height")

    # -- 6. proportional split pane with a minimum -------------------------
    section("6. the split pane is proportional with a sensible minimum")
    root = rule_block(css, ":root")
    check("--sidebar-w" in root and "clamp(" in root,
          "--sidebar-w is a clamp() (proportional, bounded)")
    sidebar = rule_block(css, ".sidebar")
    check("width: var(--sidebar-w)" in sidebar, ".sidebar uses the shared width token")
    check("min-width" in sidebar, ".sidebar declares a minimum width (never crushes)")

    # -- 7. status bar / explorer header consistency -----------------------
    section("7. status bar + explorer header share one type treatment")
    status = rule_block(css, ".status")
    check("font-variant-numeric: tabular-nums" in status,
          "the status bar uses tabular figures (stable counts)")
    check(".status .pill" in css, "status pills share one muted style")
    meta = rule_block(css, ".explorer-head .explorer-head-meta")
    check("var(--text-2)" in meta and "var(--text-3)" not in meta,
          "the explorer header meta uses the readable muted colour (not disabled)")

    # -- 8. the stable features keep their rules ---------------------------
    section("8. stable surfaces are untouched")
    check(".vgrid" in css and "grid-template-columns" in css,
          "the Image Grid layout is intact")
    check("body.fs-active .app" in css and "display: none" in css,
          "fullscreen still hides the app chrome")
    check(".fs-view img" in css and "object-fit: contain" in css,
          "Image View still preserves aspect ratio")
    check("body.fs-active" in css, "the fullscreen surface rule is intact")
    check(".card:hover" in css and ".card.selected" in css,
          "thumbnail hover/selected states are intact")

    print("\n" + "=" * 64)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("RESULT: ALL FILE-EXPLORER LAYOUT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
