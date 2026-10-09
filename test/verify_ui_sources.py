"""Shared helpers for the Veyra verification suites.

The frontend was refactored from a single ``veyra/index.html`` (with one big
inline ``<script>``) into a thin shell plus modular assets under
``veyra/static/css`` and ``veyra/static/js``.  The UI audits in the verify_*
scripts therefore no longer read a single file: they need

  * the SHELL + every loaded MODULE concatenated (wiring checks), and
  * the individual SOURCE FILES so the Node harnesses can execute the real
    shipped code (not a copy pasted into the test).

Keeping that logic here means every suite agrees on what "the frontend" is
while staying dependency-free (stdlib only) so it can be imported from any
verify script regardless of its working directory.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Project root (the folder that contains ``veyra/`` and ``desktop.py``).
ROOT = Path(__file__).resolve().parent.parent

#: UI shell + asset roots.
HTML_PATH = ROOT / "veyra" / "index.html"
STATIC_ROOT = ROOT / "veyra" / "static"

#: Frontend source files, grouped by the feature they implement.  Tests use
#: these to execute the REAL shipped code under a DOM/Node stub.
JS_CORE = (
    "core/state.js",
    "core/api.js",
    "core/dom.js",
    "core/session.js",
)
JS_MODULES = (
    "modules/grid.js",
    "modules/sort.js",
    "modules/explorer.js",
    "modules/selection.js",
    "modules/group.js",
    "modules/aimg.js",
    "modules/dialogs.js",
    "modules/fileops.js",
    "modules/viewer.js",
    "modules/contextmenu.js",
    "modules/shortcuts.js",
    "boot.js",
)


def _asset_path(rel: str) -> Path:
    """Map a manifest entry (``core/state.js``) to its file under static/js."""
    return STATIC_ROOT / "js" / rel


def js_sources(*parts: str) -> str:
    """Concatenate frontend JS sources, in the given order.

    ``parts`` are manifest-relative paths such as ``"core/state.js"`` or
    ``"modules/grid.js"``.  The result is valid top-level JavaScript that can be
    fed to Node after a DOM prelude.
    """
    chunks = []
    for rel in parts:
        path = _asset_path(rel)
        chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def js_for(*prefixes: str) -> str:
    """Concatenate every manifest module whose path starts with a prefix.

    ``js_for("core")`` -> all core modules; ``js_for("modules/grid")`` -> the
    grid module.  Order follows the manifest (load order).
    """
    wanted = [(rel, _asset_path(rel)) for rel in JS_CORE + JS_MODULES]
    out = []
    for rel, path in wanted:
        if any(rel.startswith(p) for p in prefixes):
            out.append(path.read_text(encoding="utf-8"))
    return "\n".join(out)


def shell_html() -> str:
    """The UI shell only (``veyra/index.html``)."""
    return HTML_PATH.read_text(encoding="utf-8")


def loaded_scripts(shell: str | None = None) -> list[str]:
    """Return the ``/static/js/...`` URLs referenced by the shell, in order."""
    html = shell if shell is not None else shell_html()
    return re.findall(r'<script\s+src="([^"]+)"', html)


def loaded_stylesheets(shell: str | None = None) -> list[str]:
    """Return the ``/static/css/...`` URLs referenced by the shell, in order."""
    html = shell if shell is not None else shell_html()
    return re.findall(r'<link\s+rel="stylesheet"\s+href="([^"]+)"', html)


def ui_source() -> str:
    """Shell + every module the shell loads, concatenated.

    This is what the UI wiring audits search: the shipped frontend as a whole
    rather than one monolithic file.
    """
    html = shell_html()
    parts = [html]
    for src in loaded_scripts(html):
        rel = src.lstrip("/")
        if rel.startswith("static/"):
            rel = rel[len("static/"):]
        asset = STATIC_ROOT / rel
        if asset.is_file():
            parts.append(asset.read_text(encoding="utf-8"))
    return "\n".join(parts)


def ui_css() -> str:
    """Every stylesheet the shell loads, concatenated."""
    parts = []
    for href in loaded_stylesheets():
        rel = href.lstrip("/")
        if rel.startswith("static/"):
            rel = rel[len("static/"):]
        asset = STATIC_ROOT / rel
        if asset.is_file():
            parts.append(asset.read_text(encoding="utf-8"))
    return "\n".join(parts)
