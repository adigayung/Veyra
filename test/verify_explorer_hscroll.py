"""Permanent verification for the File-Explorer panel's HORIZONTAL scrollbar.

Run with the project venv (from the project root)::

    venv\\Scripts\\python.exe test\\verify_explorer_hscroll.py

Context / task locked in by this suite
--------------------------------------
The File Explorer tree already scrolled VERTICALLY (``.explorer-body`` owns
``overflow: auto`` + a styled scrollbar).  But a long folder name / deep
indentation was shown with an ellipsis and could not be reached: the tree
content simply shrank to the panel width, so NO horizontal scrollbar ever
appeared and the overflowing name was cut off.

Two minimal, CSS-only rules fix that:

  1. ``.tree`` grows to the width of its widest row (``width: max-content``)
     while still filling the panel when short (``min-width: 100%``), so the
     content can exceed the panel and trigger the horizontal scrollbar.
  2. ``.tree li .label`` no longer clips (``overflow``/``text-overflow`` gone):
     long names stay fully readable and are reached by scrolling sideways.

The horizontal scrollbar is styled by the SAME ``.explorer-body::-webkit-scrollbar``
rule that styles the vertical one (both ``width`` and ``height`` are set), so the
two bars look identical - the whole point of "consistent with the vertical bar".

What this suite checks (a static audit of the real shipped CSS, like the other
``verify_*.py`` suites - stdlib only, no browser required):

 1. The tree scroll container still owns the scroll (``overflow: auto``) and
    keeps its stable gutter, so the vertical bar is untouched.
 2. ``.tree`` is a horizontally scrollable canvas: ``width: max-content`` +
    ``min-width: 100%`` (content may exceed the panel; short trees still fill).
 3. Tree rows are single-line (``white-space: nowrap``) and the label no longer
    ellipsises / clips, so the full folder name is readable.
 4. The horizontal bar is styled exactly like the vertical one (same
    ``::-webkit-scrollbar`` width == height, same thumb colour / radius, and a
    Firefox ``scrollbar-color``).
 5. The stable explorer layout contract is not broken: nodes still wrap
    (``.tree li`` flex-wrap) and the nested list is still a full-width block.
 6. The stylesheet is actually loaded by the shell.
"""

from __future__ import annotations

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
    """Return the declaration block for the first ``selector`` followed by ``{``.

    Mirrors the parser used by the other explorer/UI audits: find the selector
    followed by ``{`` and return the text up to the matching ``}``.
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

    Needed for selectors that also appear earlier inside a comma-grouped list
    (e.g. ``.tree, .tree ul { ... }``): the standalone ``.tree { ... }`` rule is
    found here without tripping over the grouped one.
    """
    idx = css.find("\n" + selector + " {")
    if idx == -1:
        return ""
    brace = css.find("{", idx)
    end = css.find("}", brace)
    return css[brace + 1:end] if end != -1 and brace != -1 else ""


def main() -> int:
    shell = ui.shell_html()
    css = ui.ui_css()
    explorer_css = (ui.STATIC_ROOT / "css" / "explorer.css").read_text(encoding="utf-8")

    # -- 1. the scroll container is unchanged (vertical bar untouched) -------
    section("1. the tree still scrolls in its own container (vertical kept)")
    body = decl_block(css, ".explorer-body")
    check("overflow: auto" in body,
          ".explorer-body uses overflow:auto (scrolls BOTH axes -> vertical kept)")
    check("scrollbar-gutter: stable" in body,
          ".explorer-body keeps its stable gutter (rows never jump)")
    check("flex: 1 1 auto" in body,
          ".explorer-body still fills the remaining sidebar height")

    # -- 2. the tree is a horizontal canvas ---------------------------------
    section("2. .tree can exceed the panel width (horizontal canvas)")
    tree = decl_block(css, ".tree")
    check("width: max-content" in tree,
          ".tree sizes to its widest row (content may exceed the panel)")
    check("min-width: 100%" in tree,
          ".tree still fills the panel when content is narrow (no wasted gutter)")

    # -- 3. rows are single line and names are NOT clipped ------------------
    section("3. long folder names stay fully readable (no clipping)")
    li = rule_block(css, ".tree li")
    check("white-space: nowrap" in li,
          ".tree li keeps its row on one line (overflow drives the scrollbar)")
    label = decl_block(css, ".tree li .label")
    check("text-overflow" not in label,
          ".tree li .label no longer ellipsises (full name readable)")
    check("overflow" not in label,
          ".tree li .label no longer clips its text (nothing to truncate)")
    check("white-space: nowrap" in label,
          ".tree li .label does not wrap the folder name")

    # -- 4. horizontal bar styled like the vertical one ---------------------
    section("4. the horizontal bar matches the vertical bar")
    sb = rule_block(css, ".explorer-body::-webkit-scrollbar")
    check("width: 8px" in sb and "height: 8px" in sb,
          "the scoped scrollbar sets BOTH width (vertical) and height (horizontal)")
    w = sb.split("width:", 1)[1].split(";", 1)[0].strip() if "width:" in sb else ""
    h = sb.split("height:", 1)[1].split(";", 1)[0].strip() if "height:" in sb else ""
    check(w != "" and w == h,
          f"vertical and horizontal bars share one thickness ({w or '?'} == {h or '?'})")
    thumb = rule_block(css, ".explorer-body::-webkit-scrollbar-thumb")
    check("var(--line-1)" in thumb and "border-radius" in thumb,
          "the thumb colour / radius are shared by both bars")
    check("scrollbar-color: var(--line-1) transparent" in explorer_css,
          "Firefox gets the same thin coloured scrollbar (both axes)")

    # -- 5. the stable explorer layout contract is intact -------------------
    section("5. the wrapping tree layout is not broken")
    check("display: flex" in li and "flex-wrap: wrap" in li,
          ".tree li is still a wrapping flex row (children drop to a new line)")
    child_ul = rule_block(css, ".tree li > ul")
    check("flex: 0 0 100%" in child_ul or "width: 100%" in child_ul,
          "the nested list is still a full-width block under its parent row")
    check("padding-left" in child_ul,
          "the nested list keeps its indentation (depth stays readable)")

    # -- 6. the stylesheet really loads -------------------------------------
    section("6. the stylesheet is loaded by the shell")
    check(any("explorer.css" in h for h in ui.loaded_stylesheets(shell)),
          "the shell links explorer.css (the fix ships)")

    print("\n" + "=" * 64)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("RESULT: ALL FILE-EXPLORER HORIZONTAL-SCROLL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
