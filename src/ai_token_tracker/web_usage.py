"""Chats on the Claude, ChatGPT and Gemini websites, reported by our browser extension.

The websites don't show token counts, so the extension estimates them from the text of
each chat (about 4 characters per token): for every reply, the input is the whole
conversation so far plus attached files, and the output is the reply itself. The
extension sends one small JSON document per chat to the tracker on this computer,
which keeps it in ~/.ai-token-tracker/web/<site>/<chat id>.json (replaced each time,
so sending the same chat twice never counts it twice).

The extension proves it's yours with a connection key that the tracker creates once
and shows on its Sources page. Nothing is sent anywhere else.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .core import ApiCall, Session, Turn, Usage

SITES = {"claude": "Claude.ai (web)", "chatgpt": "ChatGPT (web)", "gemini": "Gemini (web)"}
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
MAX_TURNS = 5000
PROMPT_CHARS = 400


def _home() -> Path:
    return Path(os.environ.get("AI_TOKEN_TRACKER_DIR") or Path.home() / ".ai-token-tracker").expanduser()


def web_dir() -> Path:
    return _home() / "web"


def key_file() -> Path:
    return _home() / "extension-key"


def connection_key() -> str:
    """The key the extension must send. Created once, readable by you only."""
    path = key_file()
    try:
        key = path.read_text(encoding="utf-8").strip()
        if len(key) >= 16:
            return key
    except OSError:
        pass
    key = secrets.token_urlsafe(18)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key)
    return key


def key_ok(sent: str | None) -> bool:
    return bool(sent) and secrets.compare_digest(sent.encode("utf-8", "replace"), connection_key().encode())


def _count(v) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 10**10 else 0


def _clip(text, n: int) -> str:
    return " ".join(text.split())[:n] if isinstance(text, str) else ""


def save_chat(doc: dict) -> int:
    """Store one chat from the extension (replacing the earlier copy). Returns the number of replies."""
    if not isinstance(doc, dict):
        raise ValueError("expected a JSON object")
    site, chat = doc.get("site"), str(doc.get("id") or "")
    if site not in SITES or not SAFE_ID.match(chat):
        raise ValueError("unknown site or bad chat id")
    turns = []
    for t in (doc.get("turns") or [])[:MAX_TURNS]:
        if not isinstance(t, dict):
            continue
        ms = _count(t.get("t"))
        turns.append({"id": _clip(str(t.get("id") or ""), 100), "t": ms or None,
                      "prompt": _clip(t.get("prompt"), PROMPT_CHARS), "model": _clip(t.get("model"), 80),
                      "input": _count(t.get("input")), "output": _count(t.get("output"))})
    clean = {"site": site, "id": chat, "title": _clip(doc.get("title"), 200), "model": _clip(doc.get("model"), 80),
             "saved": datetime.now(timezone.utc).isoformat(), "turns": turns}
    folder = web_dir() / site
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"{chat}.json.tmp"
    tmp.write_text(json.dumps(clean), encoding="utf-8")
    os.replace(tmp, folder / f"{chat}.json")
    return len(turns)


def load_web_sessions() -> list[Session]:
    out = []
    for site, tool in SITES.items():
        folder = web_dir() / site
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, RecursionError):
                continue
            if not isinstance(doc, dict):
                continue
            s = Session(session_id=str(doc.get("id") or path.stem), project="", path=path, tool=tool,
                        title=_clip(doc.get("title"), 200))
            for t in doc.get("turns") or []:
                if not isinstance(t, dict):
                    continue
                usage = Usage(input=_count(t.get("input")), output=_count(t.get("output")))
                if not usage.total:
                    continue
                ts = datetime.fromtimestamp(t["t"] / 1000, timezone.utc) if _count(t.get("t")) else None
                model = _clip(t.get("model") or doc.get("model"), 80) or f"{site} (model not shown)"
                s.turns.append(Turn(prompt=_clip(t.get("prompt"), PROMPT_CHARS), timestamp=ts, calls=[
                    ApiCall(ts, model, usage, app=f"{tool.split(' ')[0]} website (estimated)", key=f"web:{site}:{t.get('id')}")]))
            if s.calls:
                s.title = s.title or next((t.prompt for t in s.turns if t.prompt), "") or "(untitled chat)"
                out.append(s)
    return out


def chat_count() -> int:
    return sum(1 for site in SITES for _ in (web_dir() / site).glob("*.json")) if web_dir().is_dir() else 0


def limits_file() -> Path:
    return _home() / "claude-web-limits.json"  # outside web/, so it doesn't trigger a full re-read


def save_limits(doc: dict) -> None:
    """Claude plan usage as claude.ai shows it (used when Claude Code isn't logged in here)."""
    rows = []
    for w in (doc.get("windows") or [])[:10] if isinstance(doc, dict) else []:
        if isinstance(w, dict) and isinstance(w.get("percent"), (int, float)) and not isinstance(w.get("percent"), bool):
            rows.append({"label": _clip(w.get("label"), 40) or "Limit", "percent": max(0.0, min(float(w["percent"]), 100.0)),
                         "resets_at": _clip(w.get("resets_at"), 40) or None})
    if not rows:
        raise ValueError("no usage rows")
    web_dir().mkdir(parents=True, exist_ok=True)
    limits_file().write_text(json.dumps({"at": datetime.now(timezone.utc).timestamp(), "windows": rows}), encoding="utf-8")


def recent_limits(max_age: float = 15 * 60) -> list[dict] | None:
    try:
        doc = json.loads(limits_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if datetime.now(timezone.utc).timestamp() - float(doc.get("at") or 0) > max_age:
        return None
    return doc.get("windows") or None
