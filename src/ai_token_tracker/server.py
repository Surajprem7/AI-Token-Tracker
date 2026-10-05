"""Local dashboard server.

Serves the web dashboard (web/) and a JSON API on 127.0.0.1 only. Every API
call must carry the random token generated at start-up, and the Host header
must be the loopback address, so other websites open in your browser can't
read your usage data.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, cursor_usage, limits, plan, pricing, updater
from .core import Session, default_roots, load_sessions
from .sources import data_signature, source_status

WEB_DIR = Path(__file__).with_name("web")
PROMPT_CHARS = 400
DEFAULT_PORT = 47690  # a steady address keeps the dashboard's saved settings between launches
# Fixed types: on Windows, mimetypes reads the registry, which can map .js to text/plain and
# (with nosniff) stop the dashboard from loading.
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml",
                 ".png": "image/png", ".ico": "image/x-icon"}  # prompts are clipped in the payload; the dashboard only shows a preview


def build_payload(sessions: list[Session], roots: list[Path] | None) -> dict:
    """Compact JSON for the dashboard. Calls are arrays to keep it small:
    [epoch_ms, model_idx, app_idx, input, output, cache_write, cache_read, cost_usd, priced, subagent]
    """
    tables: dict[str, dict[str, int]] = {"tools": {}, "models": {}, "apps": {}, "projects": {}}

    def idx(kind: str, value: str) -> int:
        t = tables[kind]
        if value not in t:
            t[value] = len(t)
        return t[value]

    out = []
    for s in sessions:
        turns = []
        for t in s.turns:
            calls = []
            for c in t.calls:
                u = c.usage
                calls.append([
                    int(c.timestamp.timestamp() * 1000) if c.timestamp else None,
                    idx("models", c.model or "unknown"), idx("apps", c.app or s.tool),
                    u.input, u.output, u.cache_write, u.cache_read,
                    round(u.cost, 6), 0 if u.unpriced else 1, 1 if c.subagent else 0,
                ])
            prompt = t.prompt if len(t.prompt) <= PROMPT_CHARS else t.prompt[: PROMPT_CHARS - 1] + "…"
            turns.append({"p": prompt, "t": int(t.timestamp.timestamp() * 1000) if t.timestamp else None,
                          "c": calls})
        out.append({
            "id": s.session_id, "tool": idx("tools", s.tool), "project": idx("projects", s.project or "-"),
            "title": s.title[:PROMPT_CHARS], "branch": s.branch, "cwd": s.cwd, "turns": turns,
        })
    return {
        "version": __version__,
        "generated": datetime.now(timezone.utc).isoformat(),
        "tools": list(tables["tools"]), "models": list(tables["models"]),
        "apps": list(tables["apps"]), "projects": list(tables["projects"]),
        "sessions": out,
        "sources": source_status(roots),
        "pricing": pricing.pricing_info(),
    }


class Dashboard:
    def __init__(self, roots: list[Path] | None = None):
        self.roots = roots
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        self.payload: bytes = b""
        self.signature = None
        self.last_ping = time.monotonic()
        self.exit_event = threading.Event()  # set when an update needs the app to quit

    def data(self, force: bool = False) -> bytes:
        """The dashboard JSON; logs are re-read only when a log file changed (or when forced)."""
        # Cursor keeps no local token log: fetch its usage list in the background (at most every 15 min).
        threading.Thread(target=cursor_usage.refresh, daemon=True).start()
        with self.lock:
            signature = data_signature(self.roots)
            if force or not self.payload or signature != self.signature:
                sessions = load_sessions(self.roots)
                self.payload = json.dumps(build_payload(sessions, self.roots), separators=(",", ":")).encode()
                self.signature = signature
            return self.payload


def make_handler(app: Dashboard, port_ref: list):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AITokenTracker"

        def log_message(self, *args):  # keep the console quiet
            pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").lower()
            return host in {f"127.0.0.1:{port_ref[0]}", f"localhost:{port_ref[0]}"}

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy",
                                 "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
            self.end_headers()
            self.wfile.write(body)

        def _api_allowed(self, query: dict) -> bool:
            token = self.headers.get("X-Token") or (query.get("t") or [""])[0]
            return secrets.compare_digest(token.encode("utf-8", "replace"), app.token.encode())

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b'{"error":"bad host"}')
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path.startswith("/api/"):
                if not self._api_allowed(query):
                    return self._send(403, b'{"error":"bad token"}')
                app.last_ping = time.monotonic()
                if url.path == "/api/data":
                    try:
                        return self._send(200, app.data(force="refresh" in query))
                    except Exception as exc:  # report instead of a blank page
                        return self._send(500, json.dumps({"error": str(exc)}).encode())
                if url.path == "/api/plan":
                    return self._send(200, json.dumps(plan.status(force="force" in query)).encode())
                if url.path == "/api/limits":
                    return self._send(200, json.dumps(limits.status(force="force" in query)).encode())
                if url.path == "/api/update":
                    return self._send(200, json.dumps(updater.check(force="force" in query)).encode())
                if url.path == "/api/ping":
                    return self._send(200, b'{"ok":true}')
                return self._send(404, b'{"error":"not found"}')
            name = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
            path = (WEB_DIR / name).resolve()
            if WEB_DIR.resolve() not in path.parents or not path.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
            return self._send(200, path.read_bytes(), ctype)

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, b'{"error":"bad host"}')
            url = urlparse(self.path)
            if not self._api_allowed(parse_qs(url.query)):
                return self._send(403, b'{"error":"bad token"}')
            if url.path == "/api/update/install":
                try:
                    message = updater.install()
                except Exception as exc:
                    return self._send(500, json.dumps({"error": str(exc)}).encode())
                if "latest version" not in message:
                    # Let this reply reach the page, then quit so the update can replace the app.
                    threading.Timer(1.5, app.exit_event.set).start()
                return self._send(200, json.dumps({"message": message}).encode())
            if url.path == "/api/update-prices":
                try:
                    n = pricing.update_prices()
                except Exception as exc:
                    return self._send(502, json.dumps({"error": f"Could not update prices: {exc}"}).encode())
                app.data(force=True)
                return self._send(200, json.dumps({"models": n}).encode())
            return self._send(404, b'{"error":"not found"}')

    return Handler


def start(port: int | None = None, roots: list[Path] | None = None) -> tuple[ThreadingHTTPServer, Dashboard, str]:
    """Start the server in a background thread. Returns (server, app, url).

    port None: use DEFAULT_PORT, or any free port if it's taken. 0: any free port.
    """
    app = Dashboard(roots)
    port_ref = [0]
    handler = make_handler(app, port_ref)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", DEFAULT_PORT if port is None else port), handler)
    except OSError:
        if port not in (None, DEFAULT_PORT):
            raise  # the user asked for this exact port
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port_ref[0] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port_ref[0]}/?t={app.token}"
    return httpd, app, url


__all__ = ["start", "build_payload", "default_roots"]
