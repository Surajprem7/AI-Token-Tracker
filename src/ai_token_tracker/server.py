"""Local dashboard server.

Serves the web dashboard (web/) and a JSON API on 127.0.0.1 only. Every API
call must carry the random token generated at start-up, and the Host header
must be the loopback address, so other websites open in your browser can't
read your usage data.
"""

from __future__ import annotations

import html
import json
import re
import secrets
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, autostart, web_usage, context, cursor_usage, limits, outcomes, plan, pricing, rates, updater
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


RESUME = {"Claude Code": "claude --resume {id}", "Codex CLI": "codex resume {id}", "Grok": "grok --resume {id}"}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


def resume_command(s: Session) -> str | None:
    """The command that continues this session (run it in the session's folder). Only plain ids are
    used, so copying the command can never paste anything else into a terminal."""
    template = RESUME.get(s.tool)
    if not template or not SAFE_ID.match(s.session_id or "") or s.session_id.startswith("agent-"):
        return None
    return template.format(id=s.session_id)


def build_payload(sessions: list[Session], roots: list[Path] | None) -> dict:
    """Compact JSON for the dashboard. Calls are arrays to keep it small:
    [epoch_ms, model_idx, app_idx, input, output, cache_write, cache_read, cost_usd, priced, subagent, saved_usd]
    """
    tables: dict[str, dict[str, int]] = {"tools": {}, "models": {}, "apps": {}, "projects": {}}

    def idx(kind: str, value: str) -> int:
        t = tables[kind]
        if value not in t:
            t[value] = len(t)
        return t[value]

    try:
        commits = outcomes.link_commits(sessions)
    except Exception:  # git missing or odd: the rest of the dashboard still works
        commits = {}
    skills: dict[str, list] = {}  # name -> [uses, tokens, cost, sessions set, last ms]
    out = []
    for n, s in enumerate(sessions):
        turns = []
        for t in s.turns:
            calls = []
            for c in t.calls:
                u = c.usage
                ms = int(c.timestamp.timestamp() * 1000) if c.timestamp else None
                calls.append([
                    ms, idx("models", c.model or "unknown"), idx("apps", c.app or s.tool),
                    u.input, u.output, u.cache_write, u.cache_read,
                    round(u.cost, 6), 0 if u.unpriced else 1, 1 if c.subagent else 0, round(u.saved, 6),
                ])
                for name in c.skills:  # a response that used several skills shares its cost between them
                    k = skills.setdefault(name, [0, 0, 0.0, set(), 0])
                    k[0] += 1
                    k[1] += u.total // len(c.skills)
                    k[2] += u.cost / len(c.skills)
                    k[3].add(n)
                    k[4] = max(k[4], ms or 0)
            prompt = t.prompt if len(t.prompt) <= PROMPT_CHARS else t.prompt[: PROMPT_CHARS - 1] + "…"
            turns.append({"p": prompt, "t": int(t.timestamp.timestamp() * 1000) if t.timestamp else None,
                          "c": calls})
        row = {
            "id": s.session_id, "tool": idx("tools", s.tool), "project": idx("projects", s.project or "-"),
            "title": s.title[:PROMPT_CHARS], "branch": s.branch, "cwd": s.cwd, "turns": turns,
        }
        if (cmd := resume_command(s)):
            row["resume"] = cmd
        if commits.get(n):
            row["commits"] = [[c["hash"], int(c["at"].timestamp() * 1000), c["subject"]] for c in commits[n]]
        out.append(row)
    return {
        "version": __version__,
        "generated": datetime.now(timezone.utc).isoformat(),
        "tools": list(tables["tools"]), "models": list(tables["models"]),
        "apps": list(tables["apps"]), "projects": list(tables["projects"]),
        "sessions": out,
        "skills": [{"name": k, "uses": v[0], "tokens": v[1], "cost": round(v[2], 6), "sessions": len(v[3]),
                    "last": v[4] or None} for k, v in sorted(skills.items(), key=lambda kv: -kv[1][1])],
        "sources": source_status(roots),
        "pricing": pricing.pricing_info(),
    }


EXTENSION_ZIP = "https://github.com/Surajprem7/AI-Token-Tracker/releases/latest/download/AITokenTracker-BrowserExtension.zip"


def connect_page(code: str) -> str:
    """The page the browser extension connects itself from (it reads the aitt-connect tag)."""
    meta = f'<meta name="aitt-connect" content="{html.escape(code)}">' if code else ""
    body = ("""<h1>Connect the browser extension</h1>
<p id="aitt-status" class="wait">Waiting for the AI Token Tracker extension…</p>
<div id="aitt-help">
<p>Don't have it yet? It takes a minute:</p>
<ol>
<li><a class="btn" href="%s">Download the extension</a> and unzip it (right-click → Extract All).</li>
<li>Open <b>chrome://extensions</b> (Edge: <b>edge://extensions</b>) in a new tab, turn on <b>Developer mode</b>,
click <b>Load unpacked</b> and choose the unzipped folder.</li>
<li>Come back to this page and press <b>F5</b>. It connects by itself.</li>
</ol></div>""" % EXTENSION_ZIP) if code else """<h1>This link has expired</h1>
<p>Open AI Token Tracker and click <b>Connect browser extension</b> again.</p>"""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">{meta}
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Connect · AI Token Tracker</title>
<style>
:root{{color-scheme:light dark;--bg:#fafaf8;--ink:#1d1d1b;--muted:#74726d;--accent:#2a78d6}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151517;--ink:#f2f2f0;--muted:#99978f;--accent:#3987e5}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:620px;margin:10vh auto;padding:0 20px}} h1{{font-size:24px}} li{{margin:8px 0}}
#aitt-status{{font-size:18px;padding:14px 16px;border-radius:10px;border:1px solid var(--muted)}}
#aitt-status.ok{{border-color:#0ca30c}} #aitt-status.ok::before{{content:"✓ ";color:#0ca30c;font-weight:700}}
.btn{{display:inline-block;background:var(--accent);color:#fff;padding:6px 12px;border-radius:8px;text-decoration:none;font-weight:600}}
</style></head><body><main>{body}</main></body></html>"""


class Dashboard:
    def __init__(self, roots: list[Path] | None = None):
        self.roots = roots
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        self.payload: bytes = b""
        self.signature = None
        self.cwds: list[str] = []  # project folders of recent sessions, newest first
        self.sessions: list[Session] = []
        self.last_ping = time.monotonic()
        self.exit_event = threading.Event()  # set when an update needs the app to quit
        # Set by the desktop app: show/hide the widget, bring the dashboard window forward.
        self.on_widget = None
        self.on_show = None
        self.on_show_widget = None
        self.connect_links: dict[str, float] = {}  # one-time "connect the browser extension" links -> expiry

    def data(self, force: bool = False) -> bytes:
        """The dashboard JSON; logs are re-read only when a log file changed (or when forced)."""
        # Cursor keeps no local token log: fetch its usage list in the background (at most every 15 min).
        threading.Thread(target=cursor_usage.refresh, daemon=True).start()
        with self.lock:
            signature = data_signature(self.roots)
            if force or not self.payload or signature != self.signature:
                sessions = load_sessions(self.roots)
                self.sessions = sessions
                recent = sorted((s for s in sessions if s.cwd and s.end), key=lambda s: s.end, reverse=True)
                self.cwds = [s.cwd for s in recent][:200]
                self.payload = json.dumps(build_payload(sessions, self.roots), separators=(",", ":")).encode()
                self.signature = signature
            return self.payload


    def summary(self) -> dict:
        """The few numbers the widget shows: today, the latest session and plan limits."""
        self.data()
        now = datetime.now().astimezone()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today: dict[str, list] = {}
        latest = None
        for s in self.sessions:
            for c in s.calls:
                if c.timestamp and c.timestamp >= midnight:
                    t = today.setdefault(s.tool, [0, 0.0])
                    t[0] += c.usage.total
                    t[1] += c.usage.cost
            if s.end and (latest is None or s.end > latest.end):
                latest = s
        tools = sorted(({"tool": k, "tokens": v[0], "cost": round(v[1], 6)} for k, v in today.items()),
                       key=lambda r: -r["tokens"])
        out = {"today": {"tokens": sum(r["tokens"] for r in tools), "cost": round(sum(r["cost"] for r in tools), 6),
                         "tools": tools},
               "latest": None, "limits": limits._cache.get("value")}
        if latest is not None:
            u = latest.usage
            out["latest"] = {"title": latest.title[:160], "tool": latest.tool, "project": latest.project,
                             "tokens": u.total, "cost": round(u.cost, 6), "prompts": len(latest.turns),
                             "end": int(latest.end.timestamp() * 1000)}
        return out


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

        def cwds_ready(self) -> bool:
            return bool(app.payload)

        def _api_allowed(self, query: dict) -> bool:
            token = self.headers.get("X-Token") or (query.get("t") or [""])[0]
            return secrets.compare_digest(token.encode("utf-8", "replace"), app.token.encode())

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b'{"error":"bad host"}')
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/connect":  # opened in the browser: the extension connects itself from this page
                nonce = (query.get("n") or [""])[0]
                expiry = app.connect_links.get(nonce)
                ok = bool(nonce) and expiry is not None and time.monotonic() < expiry
                code = f"{port_ref[0]}-{web_usage.connection_key()}" if ok else ""
                return self._send(200 if ok else 410, connect_page(code).encode(), "text/html; charset=utf-8")
            if url.path == "/api/web/ping":  # the browser extension checks its connection
                if not web_usage.key_ok(self.headers.get("X-AITT-Key")):
                    return self._send(403, b'{"error":"bad key"}')
                return self._send(200, json.dumps({"ok": True, "version": __version__}).encode())
            if url.path == "/api/web/usage":  # plan usage for the extension's popup
                if not web_usage.key_ok(self.headers.get("X-AITT-Key")):
                    return self._send(403, b'{"error":"bad key"}')
                return self._send(200, json.dumps(limits.status()).encode())
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
                if url.path == "/api/extension":
                    return self._send(200, json.dumps({"code": f"{port_ref[0]}-{web_usage.connection_key()}",
                                                       "chats": web_usage.chat_count()}).encode())
                if url.path == "/api/summary":
                    return self._send(200, json.dumps(app.summary()).encode())
                if url.path == "/api/autostart":
                    return self._send(200, json.dumps(autostart.status()).encode())
                if url.path == "/api/rates":
                    return self._send(200, json.dumps(rates.get(force="force" in query)).encode())
                if url.path == "/api/context":
                    if not self.cwds_ready():
                        app.data()
                    return self._send(200, json.dumps(context.report(app.cwds)).encode())
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
            if url.path.startswith("/api/web/"):  # from the browser extension, with its own key
                if not web_usage.key_ok(self.headers.get("X-AITT-Key")):
                    return self._send(403, b'{"error":"bad key"}')
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if length > 8 * 1024 * 1024:
                        return self._send(413, b'{"error":"too large"}')
                    body = json.loads(self.rfile.read(length) or b"{}")
                    if url.path == "/api/web/chat":
                        n = web_usage.save_chat(body)
                        return self._send(200, json.dumps({"ok": True, "replies": n}).encode())
                    if url.path == "/api/web/limits":
                        web_usage.save_limits(body)
                        return self._send(200, b'{"ok":true}')
                    if url.path == "/api/web/show":
                        if app.on_show is not None:
                            threading.Thread(target=app.on_show, daemon=True).start()
                        return self._send(200, b'{"ok":true}')
                except (ValueError, TypeError) as exc:
                    return self._send(400, json.dumps({"error": str(exc)}).encode())
                return self._send(404, b'{"error":"not found"}')
            if url.path == "/api/focus" and self.headers.get("X-AITT") == "focus":
                # A second launch of the app asks this one to come forward. Needs no token (it has none),
                # but web pages can't send the custom header without a preflight we never answer, and
                # all it can do is show a window.
                try:
                    length = min(int(self.headers.get("Content-Length") or 0), 100)
                    widget = bool(json.loads(self.rfile.read(length) or b"{}").get("widget"))
                except (ValueError, AttributeError):
                    widget = False
                if app.exit_event.is_set():  # quitting for an update: the new copy should wait, not hand over
                    return self._send(200, b'{"app":"ai-token-tracker","closing":true}')
                handler = (app.on_show_widget if widget else None) or app.on_show
                if handler is not None:
                    threading.Thread(target=handler, daemon=True).start()
                return self._send(200, b'{"app":"ai-token-tracker"}')
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
            if url.path == "/api/extension/install":
                # Put the extension that ships with the app in a fixed folder (already connected)
                # and open the browser's extensions page; the user only clicks "Load unpacked".
                try:
                    folder = web_usage.install_extension(port_ref[0])
                except OSError as exc:
                    return self._send(500, json.dumps({"error": f"Couldn't set up the extension: {exc}"}).encode())
                browser = web_usage.open_extensions_page()
                return self._send(200, json.dumps({"path": str(folder), "browser": browser}).encode())
            if url.path == "/api/extension/connect":
                # A link the extension can connect itself from, valid for 10 minutes, opened in the browser.
                nonce = secrets.token_urlsafe(16)
                now = time.monotonic()
                app.connect_links = {k: v for k, v in app.connect_links.items() if v > now}
                app.connect_links[nonce] = now + 600
                link = f"http://127.0.0.1:{port_ref[0]}/connect?n={nonce}"
                threading.Thread(target=webbrowser.open, args=(link,), daemon=True).start()
                return self._send(200, json.dumps({"url": link}).encode())
            if url.path in ("/api/widget", "/api/show"):
                handler = app.on_widget if url.path == "/api/widget" else app.on_show
                if handler is None:
                    return self._send(200, b'{"native":false}')
                threading.Thread(target=handler, daemon=True).start()
                return self._send(200, b'{"native":true}')
            if url.path == "/api/autostart":
                try:
                    length = min(int(self.headers.get("Content-Length") or 0), 1000)
                    body = json.loads(self.rfile.read(length) or b"{}")
                    return self._send(200, json.dumps(autostart.set_enabled(bool(body.get("enabled")))).encode())
                except Exception as exc:
                    return self._send(500, json.dumps({"error": str(exc)}).encode())
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
    web_usage.refresh_installed_extension(port_ref[0])  # an extension installed from the app follows its updates
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port_ref[0]}/?t={app.token}"
    return httpd, app, url


__all__ = ["start", "build_payload", "default_roots"]
