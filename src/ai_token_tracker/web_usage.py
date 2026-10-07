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

SITES = {"claude": "Claude.ai (web)", "claudecode": "Claude Code (cloud)", "chatgpt": "ChatGPT (web)", "gemini": "Gemini (web)"}
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
                      "input": _count(t.get("input")), "output": _count(t.get("output")),
                      "cache_read": _count(t.get("cache_read")), "cache_write": _count(t.get("cache_write")),
                      "exact": t.get("exact") is True})
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
                usage = Usage(input=_count(t.get("input")), output=_count(t.get("output")),
                              cache_read=_count(t.get("cache_read")), cache_write=_count(t.get("cache_write")))
                if not usage.total:
                    continue
                ts = datetime.fromtimestamp(t["t"] / 1000, timezone.utc) if _count(t.get("t")) else None
                model = _clip(t.get("model") or doc.get("model"), 80) or f"{site} (model not shown)"
                s.turns.append(Turn(prompt=_clip(t.get("prompt"), PROMPT_CHARS), timestamp=ts, calls=[
                    ApiCall(ts, model, usage, key=f"web:{site}:{t.get('id')}",
                            app="claude.ai/code (cloud)" if site == "claudecode" else f"{tool.split(' ')[0]} website (estimated)")]))
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


# --------------------------------------------------------------------------- #
# Installing the extension from the desktop app
# --------------------------------------------------------------------------- #

BUNDLED = Path(__file__).with_name("extension")


def installed_dir() -> Path:
    return _home() / "browser-extension"


def install_extension(port: int) -> Path:
    """Copy the extension that ships with this app to a fixed folder, with the connection filled in.

    The browser loads it from that folder ("Load unpacked"), so the folder must stay. Running this
    again (each app start) keeps the extension up to date with the app.
    """
    import shutil

    dest = installed_dir()
    dest.mkdir(parents=True, exist_ok=True)
    for src in BUNDLED.rglob("*"):
        if src.is_file() and src.name != "STORE.md":
            target = dest / src.relative_to(BUNDLED)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    (dest / "connection.json").write_text(json.dumps({"port": port, "key": connection_key()}), encoding="utf-8")
    return dest


def refresh_installed_extension(port: int) -> None:
    """If the extension was installed from the app before, update its files and address."""
    if (installed_dir() / "manifest.json").is_file():
        try:
            install_extension(port)
        except OSError:
            pass


def open_extensions_page() -> str | None:
    """Open Chrome's or Edge's extensions page. Returns the browser's name, or None."""
    import shutil
    import subprocess
    import sys

    candidates = []  # (name, command, page)
    if sys.platform == "win32":
        bases = [os.environ.get(v) for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        for base in filter(None, bases):
            candidates.append(("Chrome", str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"), "chrome://extensions"))
        for base in filter(None, bases):
            candidates.append(("Edge", str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"), "edge://extensions"))
    elif sys.platform == "darwin":
        candidates += [("Chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "chrome://extensions"),
                       ("Edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "edge://extensions")]
    else:
        for name, exe in (("Chrome", "google-chrome"), ("Chrome", "chromium"), ("Edge", "microsoft-edge")):
            found = shutil.which(exe)
            if found:
                candidates.append((name, found, "edge://extensions" if name == "Edge" else "chrome://extensions"))
    for name, exe, page in candidates:
        if Path(exe).is_file():
            try:
                from .updater import clean_env

                subprocess.Popen([exe, page], close_fds=True, env=clean_env(),
                                 creationflags=0x08000000 if sys.platform == "win32" else 0)
                return name
            except OSError:
                continue
    return None
