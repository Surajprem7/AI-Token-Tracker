"""Desktop app: `ai-tokens-gui` (or the AITokenTracker app).

Starts the local dashboard server and shows it in a native window when
pywebview is available (Edge WebView2 on Windows, WebKit on macOS). Otherwise
it opens the dashboard in your web browser and quits a few minutes after the
last browser tab is closed.
"""

from __future__ import annotations

import sys
import time
import webbrowser

from .server import start

IDLE_EXIT_SECONDS = 180


def main(argv: list[str] | None = None) -> int:
    httpd, app, url = start()
    try:
        import webview  # optional dependency: pip install "ai-token-tracker[app]"
    except ImportError:
        webview = None

    if webview is not None:
        try:
            webview.create_window("AI Token Tracker", url, width=1320, height=900, min_size=(420, 560))
            webview.start()
            httpd.shutdown()
            return 0
        except Exception as exc:  # no GUI backend on this system: fall back to the browser
            print(f"Native window unavailable ({exc}); opening your browser instead.", file=sys.stderr)

    return serve_in_browser(httpd, app, url, open_browser=True, idle_exit=True)


def serve_in_browser(httpd, app, url: str, open_browser: bool, idle_exit: bool) -> int:
    print(f"AI Token Tracker is running at {url}", flush=True)
    print("Keep this window open while you use the dashboard. Press Ctrl+C to stop.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(5)
            if idle_exit and time.monotonic() - app.last_ping > IDLE_EXIT_SECONDS:
                break  # every dashboard tab has been closed
    except KeyboardInterrupt:
        pass
    httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
