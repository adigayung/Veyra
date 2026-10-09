"""Verification for the Veyra desktop-startup regression fix.

Runs ``desktop.main()`` with a fake pywebview module and a fake werkzeug
server, so no native window and no real socket are required. It asserts the
startup contract that the regression violated:

  * the backend server is bound (listening) *before* the GUI loop starts;
  * ``window.evaluate_js()`` is NEVER called before ``webview.start()``;
  * the page shim is injected from the ``loaded`` lifecycle event;
  * the window is created as a maximized native desktop window;
  * the native fullscreen bridge is exposed to JS;
  * the backend server is shut down cleanly when the window closes;
  * no timing-based ``sleep()`` is used anywhere in desktop.py.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import sys
import types

failures: list[str] = []
record: list = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  [OK]   {msg}")
    else:
        print(f"  [FAIL] {msg}")
        failures.append(msg)


class FakeEvent:
    def __init__(self, name: str) -> None:
        self.name = name
        self._items: list = []

    def __iadd__(self, item):
        self._items.append(item)
        return self

    def __add__(self, item):
        self._items.append(item)
        return self

    def fire(self) -> None:
        for item in list(self._items):
            item()


class FakeEvents:
    def __init__(self) -> None:
        self.loaded = FakeEvent("loaded")


class FakeWindow:
    def __init__(self, title, url, **kwargs) -> None:
        record.append(("create_window", {"title": title, "url": url, "kwargs": kwargs}))
        self.title = title
        self.url = url
        self.kwargs = kwargs
        self.events = FakeEvents()
        self.fullscreen = bool(kwargs.get("fullscreen", False))
        self.exposed: dict = {}

    def evaluate_js(self, script):
        record.append(("evaluate_js", script))

    def expose(self, *fns):
        for fn in fns:
            self.exposed[fn.__name__] = fn
        record.append(("expose", tuple(f.__name__ for f in fns)))

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        record.append(("toggle_fullscreen", self.fullscreen))


class FakeWebview(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("webview")
        self.window: FakeWindow | None = None
        self.started = False

    def create_window(self, title, url=None, **kwargs):
        self.window = FakeWindow(title, url, **kwargs)
        return self.window

    def start(self, *args, **kwargs):
        self.started = True
        record.append(("start",))
        # Simulate pywebview firing the 'loaded' lifecycle event once the GUI
        # loop is running and the page finished loading.
        assert self.window is not None
        self.window.events.loaded.fire()


class FakeServer:
    def __init__(self) -> None:
        self.serve_called = False
        self.shutdown_called = False

    def serve_forever(self) -> None:
        self.serve_called = True

    def shutdown(self) -> None:
        self.shutdown_called = True
        record.append(("server_shutdown",))


def main() -> int:
    fake_webview = FakeWebview()
    holder: dict = {}

    desktop = importlib.import_module("desktop")
    desktop.webview = fake_webview
    desktop.create_app = lambda: types.SimpleNamespace(_flask_app=object())

    def fake_make_server(host, port, app, threaded=False):
        record.append(("make_server", host, port, threaded))
        server = FakeServer()
        holder["server"] = server
        return server

    desktop.make_server = fake_make_server

    print("== desktop.main() under a mocked pywebview/werkzeug ==")
    try:
        desktop.main()
    except SystemExit as exc:
        record.append(("system_exit", exc.code))

    server: FakeServer | None = holder.get("server")
    window = fake_webview.window
    names = [r[0] for r in record]

    check(server is not None, "backend server object created")
    check("make_server" in names, "backend bound via werkzeug make_server")
    check("start" in names, "webview.start() invoked (native GUI loop)")

    assert window is not None
    start_idx = names.index("start")
    eval_indices = [i for i, n in enumerate(names) if n == "evaluate_js"]
    check(len(eval_indices) > 0, "page shim injected through evaluate_js")
    check(
        len(eval_indices) > 0 and all(i > start_idx for i in eval_indices),
        "evaluate_js is NEVER called before webview.start()",
    )

    create_idx = names.index("create_window")
    make_idx = names.index("make_server")
    check(make_idx < create_idx, "backend bound before the window is created")

    created = [r[1] for r in record if r[0] == "create_window"][0]
    check(created["kwargs"].get("maximized") is True, "window created maximized")
    check(created["kwargs"].get("fullscreen") is False, "window not created fullscreen")
    check(
        str(created["url"]).endswith(":8349/"),
        "window loads the local backend URL (native window, not a browser)",
    )

    check("expose" in names, "native fullscreen bridge exposed to JS")
    check(set(window.exposed) == {"fs_enter", "fs_leave"}, "fs_enter/fs_leave exposed")

    window.exposed["fs_enter"]()
    check(window.fullscreen is True, "fs_enter toggles native fullscreen on")
    window.fullscreen = True
    window.exposed["fs_enter"]()
    check(window.fullscreen is True, "fs_enter is idempotent when already fullscreen")
    window.exposed["fs_leave"]()
    check(window.fullscreen is False, "fs_leave toggles native fullscreen off")

    check(server.shutdown_called, "backend server shut down after the window closes")
    check(("system_exit", 0) in record, "main() exits cleanly with code 0")

    tree = ast.parse(inspect.getsource(desktop))
    sleep_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "sleep"
    ]
    check(not sleep_calls, "no timing-based .sleep() call in desktop.py")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
