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


def _focus_running_copy(widget: bool) -> bool:
    """If the app is already running, ask it to come forward instead of starting a second copy."""
    import json
    import urllib.request

    from .server import DEFAULT_PORT

    req = urllib.request.Request(f"http://127.0.0.1:{DEFAULT_PORT}/api/focus", method="POST",
                                 data=json.dumps({"widget": widget}).encode(), headers={"X-AITT": "focus"})
    for _ in range(30):
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:  # noqa: S310 - our own local server
                reply = json.loads(resp.read() or b"{}")
        except Exception:
            return False
        if reply.get("app") != "ai-token-tracker":
            return False
        if not reply.get("closing"):
            return True
        time.sleep(0.5)  # the old copy is quitting for an update; start once it's gone
    return False


def _tray_image():
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, 63, 63), radius=14, fill=(42, 120, 214, 255))
    for x, top in ((16, 28), (28, 18), (40, 32), (52, 24)):
        d.line((x - 2, 46, x - 2, top), fill="white", width=6)
    return img


class DesktopApp:
    """The dashboard window, the widget window and (on Windows) the tray icon."""

    def __init__(self, webview, app, url: str, start_with_widget: bool):
        self.webview, self.app, self.url = webview, app, url
        self.widget_url = url.replace("/?t=", "/widget.html?t=", 1)
        self.widget = None
        self.tray = None
        self.quitting = False
        self.main_hidden = start_with_widget
        self.main = webview.create_window("AI Token Tracker", url, width=1320, height=900, min_size=(420, 560),
                                          hidden=start_with_widget)
        self.main.events.closing += self._main_closing
        app.on_widget = self.toggle_widget
        app.on_show = self.show_main
        app.on_show_widget = self.show_widget
        if start_with_widget:
            self.show_widget()

    # -- windows
    def show_main(self):
        self.main_hidden = False
        self.main.show()
        try:
            self.main.restore()
        except Exception:
            pass

    def toggle_widget(self):
        if self.widget is not None:
            self.widget.destroy()
        else:
            self.show_widget()

    def show_widget(self):
        if self.widget is not None:
            self.widget.show()
            return
        self.widget = self.webview.create_window("AI Tokens", self.widget_url, width=340, height=560,
                                                 min_size=(260, 320), on_top=True)
        self.widget.events.closed += self._widget_closed

    def _widget_closed(self):
        self.widget = None
        if not self.quitting and self.tray is None and self.main_hidden:
            self.quit()  # nothing left on screen and no tray icon: really quit

    def _main_closing(self):
        if self.quitting:
            return True
        if self.tray is not None or self.widget is not None:
            self.main.hide()  # keep running for the widget / tray icon
            self.main_hidden = True
            return False
        return True

    def quit(self):
        self.quitting = True
        if self.tray is not None:
            self.tray.stop()
        for w in list(self.webview.windows):
            try:
                w.destroy()
            except Exception:
                pass

    # -- tray icon (Windows; macOS keeps its Dock icon)
    def start_tray(self):
        if sys.platform != "win32":
            return
        try:
            import pystray
            image = _tray_image()
        except Exception:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Open dashboard", lambda: self.show_main(), default=True),
            pystray.MenuItem("Show / hide widget", lambda: self.toggle_widget()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self.quit()))
        self.tray = pystray.Icon("AITokenTracker", image, "AI Token Tracker", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    start_with_widget = "--widget" in argv
    if _focus_running_copy(start_with_widget):
        return 0  # the app was already open; it has come to the front
    httpd, app, url = start()
    try:
        import webview  # optional dependency: pip install "ai-token-tracker[app]"
    except ImportError:
        webview = None

    if webview is not None:
        try:
            webview.settings["ALLOW_DOWNLOADS"] = True  # "Export CSV"
            desktop = DesktopApp(webview, app, url, start_with_widget)

            def close_for_update():
                app.exit_event.wait()
                desktop.quit()  # an update is being installed; it restarts the app

            threading.Thread(target=close_for_update, daemon=True).start()
            desktop.start_tray()
            # Not private mode, and a fixed storage folder: the dashboard remembers your
            # period, theme and hidden tools between launches.
            storage = app_data_dir() / "webview"
            storage.mkdir(parents=True, exist_ok=True)
            webview.start(private_mode=False, storage_path=str(storage))
            if desktop.tray is not None:
                desktop.tray.stop()
            httpd.shutdown()
            return 0
        except Exception as exc:  # no GUI backend on this system: fall back to the browser
            print(f"Native window unavailable ({exc}); opening your browser instead.", file=sys.stderr)

    if start_with_widget:
        url = url.replace("/?t=", "/widget.html?t=", 1)
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

    def open_again():  # someone started the app again: open another tab instead of a second copy
        extra = _launcher_file(url.replace("/widget.html?t=", "/?t=", 1))
        webbrowser.open(extra.as_uri())
        threading.Timer(60, lambda: extra.unlink(missing_ok=True)).start()

    app.on_show = open_again
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
