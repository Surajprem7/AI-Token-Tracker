"""Cursor: Cursor keeps no token counts on your computer, so we ask cursor.com.

Cursor saves its login in its own settings database (state.vscdb). We read it from
there and download your own usage list (the same CSV as Cursor's web dashboard ->
Usage -> Export). The login is sent only to cursor.com and never stored by us; the
downloaded CSV is cached in ~/.ai-token-tracker so the dashboard works offline.

Cursor hasn't documented these web endpoints, so their format could change.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.request
from pathlib import Path

from .core import ApiCall, Session, Turn, Usage

CSV_URL = "https://cursor.com/api/dashboard/export-usage-events-csv?strategy=tokens"
SUMMARY_URL = "https://cursor.com/api/usage-summary"
REFRESH_SECONDS = 15 * 60
_lock = threading.Lock()
_last_try = [float("-inf")]


def _home() -> Path:
    return Path(os.environ.get("AI_TOKEN_TRACKER_DIR") or Path.home() / ".ai-token-tracker").expanduser()


def cache_file() -> Path:
    return _home() / "cursor-usage.csv"


def cursor_app_dir() -> Path:
    home = Path.home()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "Cursor"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Cursor"
    return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "Cursor"


def _subject(sub: str) -> str | None:
    """The user id Cursor's web cookie expects: "user_..." or "<provider>|<id>"."""
    if not sub:
        return None
    if "|user_" in sub:
        return sub.split("|")[-1]
    provider = sub.split("|")[0]
    if sub.count("|") == 1 and provider in ("google-oauth2", "github", "oidc", "auth0"):
        return sub
    return None


def read_login() -> dict | None:
    """{cookie, token} from Cursor's own saved login, or None when Cursor isn't logged in."""
    db = cursor_app_dir() / "User" / "globalStorage" / "state.vscdb"
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            row = con.execute("SELECT value FROM ItemTable WHERE key = 'cursorAuth/accessToken'").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    token = row[0] if row else None
    if isinstance(token, bytes):
        token = token.decode("utf-8", "replace")
    if not isinstance(token, str) or token.count(".") != 2:
        return None
    user = None
    try:
        cfg = json.loads((Path.home() / ".cursor" / "cli-config.json").read_text(encoding="utf-8"))
        user = _subject(str(((cfg or {}).get("authInfo") or {}).get("authId") or ""))
    except (OSError, ValueError, AttributeError):
        pass
    if not user:
        try:
            part = token.split(".")[1]
            payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            user = _subject(str(payload.get("sub") or ""))
        except (ValueError, AttributeError):
            return None
    if not user:
        return None
    return {"cookie": f"WorkosCursorSessionToken={user}%3A%3A{token}", "token": token}


def _get(url: str, login: dict, timeout: float = 20) -> bytes:
    from .pricing import _ssl_context

    req = urllib.request.Request(url, headers={
        "Cookie": login["cookie"], "Referer": "https://www.cursor.com/settings",
        "Accept": "*/*", "User-Agent": "ai-token-tracker"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:  # noqa: S310
        return resp.read(50 * 1024 * 1024)


def refresh(force: bool = False) -> bool:
    """Download a fresh usage CSV when the cached one is older than 15 minutes. True if it changed."""
    path = cache_file()
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        age = float("inf")
    if not force and (age < REFRESH_SECONDS or time.monotonic() - _last_try[0] < REFRESH_SECONDS):
        return False
    if not _lock.acquire(blocking=False):
        return False
    _last_try[0] = time.monotonic()
    try:
        login = read_login()
        if not login:
            return False
        try:
            data = _get(CSV_URL, login)
        except Exception:
            return False
        text = data.decode("utf-8", "replace")
        if "Date" not in text.split("\n", 1)[0]:
            return False  # a login page or error, not the CSV
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        return True
    finally:
        _lock.release()


def _num(value) -> float:
    try:
        return max(float(str(value).replace(",", "").replace("$", "").strip() or 0), 0.0)
    except ValueError:
        return 0.0


def load_cursor_sessions() -> list[Session]:
    """One session per day from the cached CSV (Cursor's export has no session ids)."""
    from .extra_sources import _when

    path = cache_file()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    days: dict[str, Session] = {}
    for i, row in enumerate(csv.DictReader(io.StringIO(text))):
        if not isinstance(row, dict):
            continue
        with_write, without_write = _num(row.get("Input (w/ Cache Write)")), _num(row.get("Input (w/o Cache Write)"))
        usage = Usage(input=int(without_write), output=int(_num(row.get("Output Tokens"))),
                      cache_write=int(max(with_write - without_write, 0)), cache_read=int(_num(row.get("Cache Read"))))
        if not usage.total:
            continue
        ts = _when(row.get("Date"))
        day = ts.astimezone().strftime("%Y-%m-%d") if ts else "unknown"
        if day not in days:
            days[day] = Session(session_id=f"cursor-{day}", project="", path=path, tool="Cursor",
                                title=f"Cursor usage on {day}")
            days[day].turns.append(Turn(prompt="(Cursor's usage list has no session details)", timestamp=ts))
        kind = (row.get("Kind") or "").strip()
        app = f"Cursor ({kind})" if kind else "Cursor"
        cost = _num(row.get("Cost"))
        days[day].turns[0].calls.append(ApiCall(ts, (row.get("Model") or "cursor").strip(), usage, app=app,
                                                reported_cost=cost, key=f"cursor:{i}"))
    for s in days.values():
        s.turns[0].calls.sort(key=lambda c: c.timestamp.timestamp() if c.timestamp else 0)
        s.turns[0].timestamp = s.turns[0].calls[0].timestamp
    return [s for s in days.values() if s.calls]
