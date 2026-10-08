"""Desktop entry point for Veyra using the native pywebview (WebView2) shell.

Startup order — and why it matters:

1. The Flask backend is started on a daemon thread. ``make_server`` binds the
   socket inside its constructor, so as soon as the thread object exists the
   backend is already *listening*. Startup is therefore deterministic and does
   not need a fixed ``time.sleep()`` delay.
2. The native, maximized window is created and ``webview.start()`` takes over
   the GUI event loop (this is a native desktop window, not an external
   browser).
3. Anything that must touch the live page (``window.evaluate_js``) is deferred
   to pywebview's ``loaded`` lifecycle event. Calling ``evaluate_js`` *before*
   ``webview.start()`` cannot work: pywebview blocks on its internal
   ``_pywebviewready`` event and finally raises
   ``WebViewException('Main window failed to start')``.
"""

from __future__ import annotations

import sys
import threading

import webview
from werkzeug.serving import make_server

from veyra.application import create_app

HOST = "127.0.0.1"
PORT = 8349
URL = f"http://{HOST}:{PORT}/"


class FlaskServerThread(threading.Thread):
    """Serve the Veyra WSGI application from a daemon thread.

    ``make_server`` binds and starts listening eagerly, so the backend is
    reachable as soon as this object is constructed — no startup sleep needed.
    The thread is a daemon, so it is torn down together with the desktop
    process when the window is closed.
    """

    def __init__(self, flask_app, host: str = HOST, port: int = PORT) -> None:
        super().__init__(name="veyra-flask", daemon=True)
        self._server = make_server(host, port, flask_app, threaded=True)

    def run(self) -> None:  # pragma: no cover - blocking server loop
        self._server.serve_forever()

    def stop(self) -> None:  # pragma: no cover - shutdown
        self._server.shutdown()


def main() -> None:
    """Start the Flask backend and the native pywebview window."""
    app = create_app()
    flask_app = app._flask_app

    # 1) Backend first: listening deterministically before the UI is loaded.
    flask_server = FlaskServerThread(flask_app)
    flask_server.start()

    # 2) Native, maximized desktop window (WebView2). Not an external browser.
    window = webview.create_window(
        "Veyra Image Viewer",
        URL,
        maximized=True,
        fullscreen=False,
    )

    # 3) Native fullscreen bridge (JS -> Python). ``expose`` is safe before the
    #    GUI loop starts: it only registers the handlers here, and pywebview
    #    publishes them to the page as ``window.pywebview.api.*`` once the page
    #    (and its JS bridge) is ready.
    fs_state = {"on": False}

    def fs_enter() -> bool:
        if not fs_state["on"]:
            window.toggle_fullscreen()
            fs_state["on"] = True
        return fs_state["on"]

    def fs_leave() -> bool:
        if fs_state["on"]:
            window.toggle_fullscreen()
            fs_state["on"] = False
        return fs_state["on"]

    window.expose(fs_enter, fs_leave)

    # 4) Page-side shim is injected from the ``loaded`` lifecycle callback, i.e.
    #    only after the native window started and the UI finished loading. This
    #    is the *only* safe place to use ``evaluate_js``.
    def on_loaded() -> None:
        window.evaluate_js(
            "window.__aether_fs = {"
            "  enter: function () { try { return window.pywebview.api.fs_enter(); }"
            "                             catch (e) { return false; } },"
            "  leave: function () { try { return window.pywebview.api.fs_leave(); }"
            "                             catch (e) { return false; } }"
            "};"
        )

    window.events.loaded += on_loaded

    try:
        # Blocks until the window is closed.
        webview.start()
    finally:
        flask_server.stop()

    sys.exit(0)


if __name__ == "__main__":
    main()
