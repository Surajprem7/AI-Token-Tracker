"""Desktop app: `ai-tokens-gui` (or the AITokenTracker app).

Starts the local dashboard server and shows it in a native window when
pywebview is available (Edge WebView2 on Windows, WebKit on macOS). Otherwise
it opens the dashboard in your web browser and quits a few minutes after the
last browser tab is closed.
"""

from __future__ import annotations

import html
import os
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

from .server import start

IDLE_EXIT_SECONDS = 180


def app_data_dir() -> Path:
    base = os.environ.get("AI_TOKEN_TRACKER_DIR") or Path.home() / ".ai-token-tracker"
    return Path(base).expanduser()


def main(argv: list[str] | None = None) -> int:
    httpd, app, url = start()
    try:
        import webview  # optional dependency: pip install "ai-token-tracker[app]"
    except ImportError:
        webview = None

    if webview is not None:
        try:
            webview.settings["ALLOW_DOWNLOADS"] = True  # "Export CSV"
            window = webview.create_window("AI Token Tracker", url, width=1320, height=900, min_size=(420, 560))

            def close_for_update():
                app.exit_event.wait()
                window.destroy()  # an update is being installed; it restarts the app

            threading.Thread(target=close_for_update, daemon=True).start()
            # Not private mode, and a fixed storage folder: the dashboard remembers your
            # period, theme and hidden tools between launches.
            storage = app_data_dir() / "webview"
            storage.mkdir(parents=True, exist_ok=True)
            webview.start(private_mode=False, storage_path=str(storage))
            httpd.shutdown()
            return 0
        except Exception as exc:  # no GUI backend on this system: fall back to the browser
            print(f"Native window unavailable ({exc}); opening your browser instead.", file=sys.stderr)

    return serve_in_browser(httpd, app, url, open_browser=True, idle_exit=True)


def _launcher_file(url: str) -> Path:
    """A small private HTML file that forwards to the dashboard.

    Opening this file instead of the URL keeps the access token out of the browser's
    command line, where other users of the computer could read it.
    """
    fd, name = tempfile.mkstemp(prefix="ai-token-tracker-", suffix=".html")  # readable by you only
    safe = html.escape(url, quote=True)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={safe}">'
                 f'<title>AI Token Tracker</title><a href="{safe}">Open AI Token Tracker</a>')
    return Path(name)


def serve_in_browser(httpd, app, url: str, open_browser: bool, idle_exit: bool) -> int:
    print(f"AI Token Tracker is running at {url}", flush=True)
    print("Keep this window open while you use the dashboard. Press Ctrl+C to stop.", flush=True)
    launcher = None
    opened_at = time.monotonic()
    if open_browser:
        launcher = _launcher_file(url)
        webbrowser.open(launcher.as_uri())
    try:
        while True:
            if app.exit_event.wait(5):
                break  # an update is being installed
            if launcher is not None and app.last_ping > opened_at:
                launcher.unlink(missing_ok=True)  # the dashboard has loaded; the file isn't needed any more
                launcher = None
            if idle_exit and time.monotonic() - app.last_ping > IDLE_EXIT_SECONDS:
                break  # every dashboard tab has been closed
    except KeyboardInterrupt:
        pass
    finally:
        if launcher is not None:
            launcher.unlink(missing_ok=True)
    httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
