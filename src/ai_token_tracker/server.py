"""Local dashboard server.

Serves the web dashboard (web/) and a JSON API on 127.0.0.1 only. Every API
call must carry the random token generated at start-up, and the Host header
must be the loopback address, so other websites open in your browser can't
read your usage data.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, pricing
from .core import Session, default_roots, load_sessions
from .sources import source_status

WEB_DIR = Path(__file__).with_name("web")
PROMPT_CHARS = 400  # prompts are clipped in the payload; the dashboard only shows a preview


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
        self.loaded_at = 0.0
        self.last_ping = time.monotonic()

    def data(self, refresh: bool = False) -> bytes:
        with self.lock:
            if refresh or not self.payload or time.monotonic() - self.loaded_at > 30:
                sessions = load_sessions(self.roots)
                self.payload = json.dumps(build_payload(sessions, self.roots), separators=(",", ":")).encode()
                self.loaded_at = time.monotonic()
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
            return secrets.compare_digest(token, app.token)

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
                        return self._send(200, app.data(refresh="refresh" in query))
                    except Exception as exc:  # report instead of a blank page
                        return self._send(500, json.dumps({"error": str(exc)}).encode())
                if url.path == "/api/ping":
                    return self._send(200, b'{"ok":true}')
                return self._send(404, b'{"error":"not found"}')
            name = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
            path = (WEB_DIR / name).resolve()
            if WEB_DIR.resolve() not in path.parents or not path.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return self._send(200, path.read_bytes(), ctype)

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, b'{"error":"bad host"}')
            url = urlparse(self.path)
            if not self._api_allowed(parse_qs(url.query)):
                return self._send(403, b'{"error":"bad token"}')
            if url.path == "/api/update-prices":
                try:
                    n = pricing.update_prices()
                except Exception as exc:
                    return self._send(502, json.dumps({"error": f"Could not update prices: {exc}"}).encode())
                app.data(refresh=True)
                return self._send(200, json.dumps({"models": n}).encode())
            return self._send(404, b'{"error":"not found"}')

    return Handler


def start(port: int = 0, roots: list[Path] | None = None) -> tuple[ThreadingHTTPServer, Dashboard, str]:
    """Start the server in a background thread. Returns (server, app, url)."""
    app = Dashboard(roots)
    port_ref = [port]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port_ref))
    port_ref[0] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port_ref[0]}/?t={app.token}"
    return httpd, app, url


__all__ = ["start", "build_payload", "default_roots"]
