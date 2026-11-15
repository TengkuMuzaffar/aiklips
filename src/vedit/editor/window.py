"""`vedit app`: open the editor in a native desktop window (pywebview).

Falls back to the default browser when no native webview is available
(e.g. Linux without GTK/Qt webkit packages).
"""

from __future__ import annotations

import socket
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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

            webview.create_window("claude-cut", start, width=1480, height=920, min_size=(1024, 640))
            webview.start()
            server.should_exit = True
            return
        except Exception as e:  # no native webview backend
            print(f"Native window unavailable ({e}); opening in your browser instead.")
    print(f"claude-cut: {start}  (Ctrl+C to quit)")
    webbrowser.open(start)
    try:
        t.join()
    except KeyboardInterrupt:
        server.should_exit = True
