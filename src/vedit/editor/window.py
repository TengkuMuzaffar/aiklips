"""`vedit app`: open the editor in a native desktop window (pywebview).

Falls back to the default browser when no native webview is available
(e.g. Linux without GTK/Qt webkit packages).
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

BRAND = Path(__file__).resolve().parents[3] / "assets" / "brand"
SITE = "https://aiklips.muzdev.online"
REPO = "https://github.com/TengkuMuzaffar/aiklips"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _brand_process() -> None:
    """Before the window exists: our name and icon instead of Python's (dock, taskbar, menu bar)."""
    if sys.platform == "win32":
        try:  # own taskbar group, so Windows shows the window's icon rather than python.exe's
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("muzdev.aiklips")
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            from Foundation import NSBundle

            info = NSBundle.mainBundle().infoDictionary()
            info["CFBundleName"] = info["CFBundleDisplayName"] = "aiklips"
        except Exception:
            pass


def _ready() -> None:
    """Tell the launcher's setup window the app is on screen, so it closes now (it watches this file)."""
    import os

    f = os.environ.get("AIKLIPS_READY_FILE")
    if f:
        try:
            Path(f).write_text("ready")
        except OSError:
            pass


def _start_icon(platform: str = sys.platform) -> str | None:
    """The icon for webview.start. Never on Windows: its WinForms window takes only a real .ico there and throws
    an unhandled .NET exception for a PNG ("must be a picture that can be used as a Icon"), which kills the app before
    the window shows. Windows gets its icon from icon.ico in _brand_window instead."""
    return None if platform == "win32" else str(BRAND / "icon-256.png")


def _show_hidden_window(user32, hwnd: int, tries: int = 25, pause: float = 0.2) -> bool:
    """Windows: the launcher starts the app with "start hidden" (so no console flashes), and Windows applies that to
    the first window the app shows: the editor opened invisible (a python process with no window). Show it, and keep
    checking a few seconds in case pywebview's own first show comes after ours. True when it had to be shown."""
    import time

    showed = False
    for _ in range(tries):
        if not user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
            user32.SetForegroundWindow(hwnd)
            showed = True
        time.sleep(pause)
    return showed


def _dark_title_bar(hwnd: int, dwm=None) -> bool:
    """Windows: a dark title bar matching the app instead of the white system one. Windows 10 1809+ takes the
    immersive dark mode flag (attribute 20, 19 on builds before 20H1); Windows 11 also takes our exact colours for
    the caption, its text and the border. Each call simply fails on a Windows that doesn't know it."""
    import ctypes

    dwm = dwm or ctypes.windll.dwmapi
    on = ctypes.c_int(1)
    ok = False
    for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE
        if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
            ok = True
            break
    for attr, rgb in ((35, 0x090D0B), (36, 0xE9F0EB), (34, 0x1D2822)):  # caption, text, border: ui.css --bg, --text, --line (COLORREF = 0x00BBGGRR)
        r, g, b = rgb >> 16, (rgb >> 8) & 0xFF, rgb & 0xFF
        ref = ctypes.c_uint(r | (g << 8) | (b << 16))
        dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(ref), ctypes.sizeof(ref))
    return ok


def _menu_for(platform: str, get_window):
    """macOS keeps its menu bar (it lives at the top of the screen, as Mac apps do). Windows gets none: WinForms
    draws it as a light grey strip that makes the app look like a generic program, and everything in it is in the
    app itself (Settings, Export, Home, undo / redo, Settings → About for help and updates)."""
    return None if platform == "win32" else _menu(get_window)


def _brand_window(window) -> None:
    """Once the window is up: make sure it is visible (Windows), set its icon (Windows title bar/taskbar, macOS dock),
    then signal the launcher."""
    if sys.platform == "win32":
        window.events.shown.wait(20)  # the form and its handle exist by then
    _ready()
    try:
        if sys.platform == "win32":
            import ctypes

            ico = str(BRAND / "icon.ico")
            hwnd = window.native.Handle.ToInt64()
            user32 = ctypes.windll.user32
            _dark_title_bar(hwnd)
            threading.Thread(target=_show_hidden_window, args=(user32, hwnd), daemon=True).start()
            for which, size in ((0, 16), (1, 32)):  # ICON_SMALL, ICON_BIG
                h = user32.LoadImageW(None, ico, 1, size if which == 0 else 0, size if which == 0 else 0, 0x10)
                if h:
                    user32.SendMessageW(hwnd, 0x80, which, h)  # WM_SETICON
        elif sys.platform == "darwin":
            from AppKit import NSApplication, NSImage

            img = NSImage.alloc().initWithContentsOfFile_(str(BRAND / "icon.icns"))
            if img:
                NSApplication.sharedApplication().setApplicationIconImage_(img)
    except Exception:
        pass


def _menu(get_window):
    """The menu bar: everything also reachable in the UI, with the page doing the work (`aiklipsMenu` in shell.js)."""
    from webview.menu import Menu, MenuAction, MenuSeparator

    def cmd(name: str):
        def run():
            w = get_window()
            if w:
                w.evaluate_js(f"window.aiklipsMenu ? aiklipsMenu({name!r}) : location.assign('/')")
        return run

    if sys.platform == "darwin":  # macOS adds its own Edit (undo, copy/paste) and View menus; Settings lives in the app menu
        return [
            Menu("__app__", [MenuAction("Settings…", cmd("settings")), MenuAction("Check for updates…", cmd("updates"))]),
            Menu("File", [
                MenuAction("New project…", cmd("new")),
                MenuAction("Open project…", cmd("open")),
                MenuAction("Home", cmd("home")),
                MenuSeparator(),
                MenuAction("Export…", cmd("export")),
            ]),
            Menu("Help", [
                MenuAction("Welcome tour", cmd("welcome")),
                MenuAction("What's new", cmd("whatsnew")),
                MenuSeparator(),
                MenuAction("aiklips website", lambda: webbrowser.open(SITE)),
                MenuAction("Report a problem", lambda: webbrowser.open(REPO + "/issues/new")),
            ]),
        ]
    return [
        Menu("File", [
            MenuAction("New project…", cmd("new")),
            MenuAction("Open project…", cmd("open")),
            MenuAction("Home", cmd("home")),
            MenuSeparator(),
            MenuAction("Export…", cmd("export")),
        ]),
        Menu("Edit", [
            MenuAction("Undo", cmd("undo")),
            MenuAction("Redo", cmd("redo")),
            MenuSeparator(),
            MenuAction("Settings…", cmd("settings")),
        ]),
        Menu("Help", [
            MenuAction("Welcome tour", cmd("welcome")),
            MenuAction("What's new", cmd("whatsnew")),
            MenuAction("Check for updates", cmd("updates")),
            MenuSeparator(),
            MenuAction("aiklips website", lambda: webbrowser.open(SITE)),
            MenuAction("Report a problem", lambda: webbrowser.open(REPO + "/issues/new")),
        ]),
    ]


def launch(pdir: Path | None, browser: bool = False, port: int | None = None) -> None:
    import uvicorn

    from ..preview_server.app import create_app

    port = port or _free_port()
    url = f"http://127.0.0.1:{port}"
    start = url + ("/editor" if pdir else "/")
    server = uvicorn.Server(uvicorn.Config(create_app(pdir), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):  # wait until the server answers
        try:
            urllib.request.urlopen(url + "/api/projects", timeout=0.5)
            break
        except Exception:
            time.sleep(0.1)

    if not browser:
        try:
            import webview

            from ..library import home_dir

            _brand_process()
            win = webview.create_window("aiklips", start, width=1480, height=920, min_size=(1024, 640),
                                        background_color="#090d0b", menu=_menu_for(sys.platform, lambda: win) or [])
            webview.start(_brand_window, (win,), icon=_start_icon(),
                          private_mode=False, storage_path=str(home_dir() / "webview"))
            server.should_exit = True
            return
        except Exception as e:  # no native webview backend
            print(f"Native window unavailable ({e}); opening in your browser instead.")
    print(f"aiklips: {start}  (Ctrl+C to quit)")
    webbrowser.open(start)
    _ready()
    try:
        t.join()
    except KeyboardInterrupt:
        server.should_exit = True
