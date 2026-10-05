"""Readers for even more AI tools (all read-only, from files or databases the tools already keep).

Each reader turns a tool's own records into Sessions -> Turns -> ApiCalls, with usage split as
input (uncached), output (including reasoning), cache_write and cache_read.

Tools covered here, and where they keep their data:

* Kimi CLI        ~/.kimi/sessions/<workspace>/<session>/wire.jsonl
* Kimi Code       ~/.kimi-code/sessions/<workspace>/<session>/agents/<agent>/wire.jsonl
* CodeBuddy       ~/.codebuddy/projects/<project>/<session>.jsonl
* WorkBuddy       ~/.workbuddy/projects/<project>/**/*.jsonl
* Pi, oh-my-pi, OmO   ~/.pi|.omp|.omo/agent/sessions/<project>/*.jsonl
* MiniMax Code    ~/.minimax/v2/sessions/**/messages.jsonl
* Craft Agents    ~/.craft-agent/workspaces/<id>/sessions/<id>/session.jsonl
* Droid (Factory) ~/.factory/sessions/**/<id>.settings.json
* Grok Build      ~/.grok/sessions/<project>/<session>/updates.jsonl
* GitHub Copilot  ~/.copilot/session-store.db (CLI and Copilot app)
* Goose           <app data>/goose/sessions/sessions.db
* Zed             <app data>/Zed/threads/threads.db
* Kiro            <VS Code-style user dir>/Kiro/User/globalStorage/kiro.kiroagent/dev_data/devdata.sqlite
* Hermes Agent    ~/.hermes/state.db
* AnythingLLM     <app data>/anythingllm-desktop/storage/anythingllm.db
* LM Studio       ~/.lmstudio/server-logs/**/*.log (local models, cost 0)
* Cursor          usage list downloaded from cursor.com with Cursor's own login (see cursor_usage.py)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .core import ApiCall, Session, Turn, Usage, parse_ts, read_jsonl
from .sources import _int, _text


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _env_path(name: str, default: Path) -> Path:
    value = (os.environ.get(name) or "").strip()
    return Path(value).expanduser() if value else default


def _app_data() -> Path:
    """Roaming app data (Windows), Application Support (macOS) or ~/.config (Linux)."""
    home = Path.home()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    if sys.platform == "darwin":
        return home / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")


def _local_data() -> Path:
    """Local app data (Windows), Application Support (macOS) or ~/.local/share (Linux)."""
    home = Path.home()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    if sys.platform == "darwin":
        return home / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")


def _when(value) -> datetime | None:
    """A timestamp in any of the shapes tools use: epoch seconds or ms (number or digits), or ISO text."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        try:
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        # "YYYY-MM-DD HH:MM:SS" from SQLite is UTC, not local time.
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?", text):
            try:
                return datetime.fromisoformat(text.replace(" ", "T")).replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return parse_ts(text)
    return None


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return None


def _clean(text) -> str:
    return " ".join(text.split()) if isinstance(text, str) else ""


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError, RecursionError):
        return None


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _cost(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
        return None
    return float(value)


def _project(cwd: str) -> str:
    return (Path(cwd).name or cwd) if cwd else ""


def _finish(s: Session) -> Session | None:
    s.turns = [t for t in s.turns if t.calls]
    if not s.calls:
        return None
    s.turns.sort(key=lambda t: t.timestamp or datetime.min.replace(tzinfo=timezone.utc))
    if s.cwd and not s.project:
        s.project = _project(s.cwd)
    if not s.title:
        s.title = next((t.prompt for t in s.turns if t.prompt), "") or "(no prompt)"
    return s


class _Builder:
    """Prompts and calls in file order -> one Session."""

    def __init__(self, s: Session):
        self.s = s
        self.turn: Turn | None = None

    def prompt(self, text: str, ts) -> None:
        text = _clean(text)
        if text:
            self.turn = Turn(prompt=text, timestamp=ts)
            self.s.turns.append(self.turn)

    def call(self, call: ApiCall) -> None:
        if not call.usage.total:
            return
        if self.turn is None:
            self.turn = Turn(prompt="", timestamp=call.timestamp)
            self.s.turns.append(self.turn)
        self.turn.calls.append(call)


def _rows(db: Path, sql: str, params: tuple = ()) -> list[dict]:
    """Rows from a SQLite database opened read-only. A busy or WAL-mode database is copied first."""
    if not db.is_file():
        return []

    def run(path: Path, uri: bool) -> list[dict]:
        target = path.resolve().as_uri() + "?mode=ro" if uri else str(path)
        con = sqlite3.connect(target, uri=uri, timeout=2)
        try:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute(sql, params)]
        finally:
            con.close()

    try:
        return run(db, True)
    except sqlite3.OperationalError as exc:
        if "no such" in str(exc):  # a table/column this version doesn't have
            return []
    except sqlite3.Error:
        return []
    # Locked, or a write-ahead log we can't open read-only: read a private copy instead.
    tmp = Path(tempfile.mkdtemp(prefix="ai-token-tracker-"))
    try:
        copy = tmp / db.name
        shutil.copy2(db, copy)
        for suffix in ("-wal", "-shm"):
            side = db.with_name(db.name + suffix)
            if side.exists():
                shutil.copy2(side, tmp / side.name)
        return run(copy, False)
    except (OSError, sqlite3.Error):
        return []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _columns(db: Path, table: str) -> set[str]:
    return {r.get("name") for r in _rows(db, f"PRAGMA table_info({table})")}


def _pick(cols: set[str], *names: str) -> str:
    """SQL for the first column that exists, else NULL."""
    for n in names:
        if n in cols:
            return n
    return "NULL"


# --------------------------------------------------------------------------- #
# Kimi CLI and Kimi Code (Moonshot)
# --------------------------------------------------------------------------- #


def kimi_home() -> Path:
    return _env_path("KIMI_HOME", Path.home() / ".kimi")


def kimi_code_home() -> Path:
    return _env_path("KIMI_CODE_HOME", Path.home() / ".kimi-code")


def _kimi_default_model() -> str:
    """config.toml: default_model = "<key>", and [models."<key>"] model = "<name>"."""
    try:
        raw = (kimi_home() / "config.toml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "kimi-for-coding"
    m = re.search(r'^\s*default_model\s*=\s*"([^"]+)"', raw, re.M)
    if not m:
        return "kimi-for-coding"
    key = m.group(1)
    section = re.search(r'\[models\."' + re.escape(key) + r'"\]([\s\S]*?)(?:\n\[|$)', raw)
    if section:
        name = re.search(r'^\s*model\s*=\s*"([^"]+)"', section.group(1), re.M)
        if name:
            return name.group(1)
    return key.split("/")[-1] or "kimi-for-coding"


def _kimi_prompt(payload: dict) -> str:
    for key in ("user_input", "input", "text", "content"):
        value = payload.get(key)
        text = _text(value) if not isinstance(value, str) else _clean(value)
        if text:
            return text
    return ""


def load_kimi_sessions() -> list[Session]:
    out: list[Session] = []
    sessions = kimi_home() / "sessions"
    if sessions.is_dir():
        model = _kimi_default_model()
        for path in sorted(sessions.glob("*/*/wire.jsonl")):
            s = Session(session_id=path.parent.name, project=path.parent.parent.name, path=path, tool="Kimi")
            b, seen = _Builder(s), set()
            for rec in read_jsonl(path):
                msg = _dict(rec.get("message"))
                payload = _dict(msg.get("payload"))
                ts = _when(rec.get("timestamp") or payload.get("timestamp"))
                kind = msg.get("type")
                if kind in ("TurnBegin", "UserInput", "UserMessage"):
                    b.prompt(_kimi_prompt(payload), ts)
                elif kind == "StatusUpdate":
                    u, mid = _dict(payload.get("token_usage")), payload.get("message_id")
                    if not u or not mid or mid in seen:
                        continue
                    seen.add(mid)
                    usage = Usage(input=_int(u.get("input_other")), output=_int(u.get("output")),
                                  cache_read=_int(u.get("input_cache_read")),
                                  cache_write=_int(u.get("input_cache_creation")))
                    b.call(ApiCall(ts, model, usage, app="Kimi CLI", key=str(mid)))
            if (s := _finish(s)):
                out.append(s)

    code_sessions = kimi_code_home() / "sessions"
    if code_sessions.is_dir():
        for session_dir in sorted(p for p in code_sessions.glob("*/*") if p.is_dir()):
            s = Session(session_id=session_dir.name, project="", path=session_dir, tool="Kimi")
            b, seen = _Builder(s), set()
            for path in sorted(session_dir.glob("agents/*/wire.jsonl")):
                sub = path.parent.name not in ("main", "root", "default")
                model = "kimi-code"
                for rec in read_jsonl(path):
                    if rec.get("type") == "config.update":
                        alias = rec.get("modelAlias")
                        if isinstance(alias, str) and alias.strip():
                            model = alias.strip().split("/")[-1]
                        continue
                    if not sub and rec.get("type") in ("user.message", "user_message", "turn.begin"):
                        b.prompt(_kimi_prompt(rec) or _kimi_prompt(_dict(rec.get("message"))), _when(rec.get("time")))
                        continue
                    ev = _dict(rec.get("event")) if rec.get("type") == "context.append_loop_event" else rec
                    u = _dict(ev.get("usage"))
                    uid = ev.get("uuid")
                    if ev.get("type") != "step.end" or not u or not uid or uid in seen:
                        continue
                    seen.add(uid)
                    if u.get("inputOther") is not None:  # current format: input already excludes the cache
                        usage = Usage(input=_int(u.get("inputOther")), output=_int(u.get("output")),
                                      cache_read=_int(u.get("inputCacheRead")),
                                      cache_write=_int(u.get("inputCacheCreation")))
                    elif u.get("cache_read_input_tokens") is not None:
                        usage = Usage(input=_int(u.get("input_tokens")), output=_int(u.get("output_tokens")),
                                      cache_read=_int(u.get("cache_read_input_tokens")),
                                      cache_write=_int(u.get("cache_creation_input_tokens")))
                    else:  # OpenAI-style: cached tokens are inside input_tokens
                        cached = _int(_dict(u.get("input_tokens_details")).get("cached_tokens"))
                        usage = Usage(input=max(_int(u.get("input_tokens")) - cached, 0),
                                      output=_int(u.get("output_tokens")), cache_read=cached,
                                      cache_write=_int(u.get("cache_creation_input_tokens")))
                    b.call(ApiCall(_when(rec.get("time") or ev.get("time")), model, usage, subagent=sub,
                                   app="Kimi Code", key=str(uid)))
            if (s := _finish(s)):
                out.append(s)
    return out


# --------------------------------------------------------------------------- #
# CodeBuddy and WorkBuddy (Tencent; Claude Code-style folders, OpenAI-style usage)
# --------------------------------------------------------------------------- #


def codebuddy_home() -> Path:
    return _env_path("CODEBUDDY_HOME", Path.home() / ".codebuddy")


def workbuddy_home() -> Path:
    return _env_path("WORKBUDDY_HOME", Path.home() / ".workbuddy")


def _buddy_usage(raw: dict) -> Usage:
    """prompt_tokens is the whole prompt (cache reads and writes included); completion includes reasoning."""
    details = _dict(raw.get("prompt_tokens_details"))
    read = max(_int(raw.get("cache_read_input_tokens")), _int(details.get("cached_tokens")),
               _int(raw.get("prompt_cache_hit_tokens")))
    write = _int(raw.get("cache_creation_input_tokens"))
    prompt = _int(raw.get("prompt_tokens"))
    return Usage(input=max(prompt - read - write, 0), output=_int(raw.get("completion_tokens")),
                 cache_read=min(read, prompt) if prompt else read, cache_write=write)


def _buddy_model(home: Path) -> str:
    settings = _load_json(home / "settings.json")
    return str(_dict(settings).get("model") or "")


def _buddy_prompt(rec: dict) -> str:
    if rec.get("role") != "user" or rec.get("type") not in ("message", None):
        return ""
    content = rec.get("content")
    if content is None:
        content = _dict(rec.get("message")).get("content")
    text = _clean(content) if isinstance(content, str) else _text(content)
    return "" if text.startswith("<") else text


def _load_buddy(tool: str, home: Path, nested: bool) -> list[Session]:
    projects = home / "projects"
    if not projects.is_dir():
        return []
    default_model = _buddy_model(home)
    files = projects.rglob("*.jsonl") if nested else projects.glob("*/*.jsonl")
    sessions: dict[str, Session] = {}
    builders: dict[str, _Builder] = {}
    seen: set = set()
    for path in sorted(files):
        sub = "subagents" in path.parts
        sid = path.parent.parent.name if sub else path.stem
        if sid not in sessions:
            project = path.parents[2].name if sub else path.parent.name
            sessions[sid] = Session(session_id=sid, project=project, path=path, tool=tool)
            builders[sid] = _Builder(sessions[sid])
        b = builders[sid]
        for rec in read_jsonl(path):
            ts = _when(rec.get("timestamp"))
            sessions[sid].cwd = sessions[sid].cwd or str(rec.get("cwd") or "")
            if not sub and (text := _buddy_prompt(rec)):
                b.prompt(text, ts)
                continue
            provider = _dict(rec.get("providerData"))
            raw = _dict(provider.get("rawUsage"))
            if not raw:
                continue
            key = str(provider.get("messageId") or rec.get("id") or "")
            if key and key in seen:
                continue
            seen.add(key)
            model = str(provider.get("model") or rec.get("model") or default_model or tool.lower())
            b.call(ApiCall(ts, model, _buddy_usage(raw), subagent=sub, app=tool, key=key))
    return [s for s in map(_finish, sessions.values()) if s]


def load_codebuddy_sessions() -> list[Session]:
    return _load_buddy("CodeBuddy", codebuddy_home(), nested=False)


def load_workbuddy_sessions() -> list[Session]:
    return _load_buddy("WorkBuddy", workbuddy_home(), nested=True)


# --------------------------------------------------------------------------- #
# Pi coding agent, oh-my-pi and OmO (same session format), MiniMax Code
# --------------------------------------------------------------------------- #

PI_FAMILY = {
    "Pi": ("PI_CODING_AGENT_DIR", Path(".pi") / "agent"),
    "oh-my-pi": ("OMP_HOME", Path(".omp") / "agent"),
    "OmO": ("OMO_HOME", Path(".omo") / "agent"),
}


def pi_dirs() -> dict[str, Path]:
    out = {}
    for tool, (var, rel) in PI_FAMILY.items():
        base = _env_path(var, Path.home() / rel)
        out[tool] = base / "sessions" if base.name != "sessions" else base
    return out


def _pi_usage(u: dict, reasoning_in_output: bool) -> Usage:
    reasoning = _int(u.get("reasoningTokens") or u.get("reasoning"))
    return Usage(input=_int(u.get("input")), output=_int(u.get("output")) + (0 if reasoning_in_output else reasoning),
                 cache_read=_int(u.get("cacheRead")), cache_write=_int(u.get("cacheWrite")))


def _load_pi_file(path: Path, tool: str, reasoning_in_output: bool) -> Session | None:
    s = Session(session_id=path.stem, project=path.parent.name.strip("-"), path=path, tool=tool)
    b, seen = _Builder(s), set()
    for rec in read_jsonl(path):
        if rec.get("type") == "session":
            s.cwd = str(rec.get("cwd") or s.cwd)
            s.session_id = str(rec.get("id") or s.session_id)
            continue
        msg = _dict(rec.get("message"))
        if rec.get("type") != "message" or not msg:
            continue
        ts = _when(msg.get("timestamp")) or _when(rec.get("timestamp"))
        if msg.get("role") == "user":
            b.prompt(msg.get("content") if isinstance(msg.get("content"), str) else _text(msg.get("content")), ts)
        elif msg.get("role") == "assistant" and isinstance(msg.get("usage"), dict):
            key = str(rec.get("id") or "")
            if key and key in seen:
                continue
            seen.add(key)
            b.call(ApiCall(ts, str(msg.get("model") or f"{tool.lower()}-unknown"),
                           _pi_usage(msg["usage"], reasoning_in_output), app=tool, key=key))
    if s.cwd:
        s.project = _project(s.cwd)
    return _finish(s)


def load_pi_sessions() -> list[Session]:
    out, done = [], set()
    for tool, sessions in pi_dirs().items():
        if not sessions.is_dir() or sessions.resolve() in done:
            continue
        done.add(sessions.resolve())
        for path in sorted(sessions.rglob("*.jsonl")):
            if (s := _load_pi_file(path, tool, reasoning_in_output=(tool == "OmO"))):
                out.append(s)
    return out


def minimax_dir() -> Path:
    return _env_path("MINIMAX_HOME", Path.home() / ".minimax") / "v2" / "sessions"


def load_minimax_sessions() -> list[Session]:
    root = minimax_dir()
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.rglob("messages.jsonl")):
        s = Session(session_id=path.parent.name, project="", path=path, tool="MiniMax Code")
        b, seen = _Builder(s), set()
        for rec in read_jsonl(path):
            msg = _dict(rec.get("message")) or rec
            ts = _when(msg.get("timestamp")) or _when(rec.get("timestamp"))
            if msg.get("role") == "user":
                b.prompt(msg.get("content") if isinstance(msg.get("content"), str) else _text(msg.get("content")), ts)
            elif msg.get("role") == "assistant" and isinstance(msg.get("usage"), dict):
                key = str(rec.get("message_id") or msg.get("id") or "")
                if key and key in seen:
                    continue
                seen.add(key)
                b.call(ApiCall(ts, str(msg.get("model") or "minimax"), _pi_usage(msg["usage"], False),
                               app="MiniMax Code", key=key))
        if (s := _finish(s)):
            out.append(s)
    return out


# --------------------------------------------------------------------------- #
# Craft Agents - the first line of each session file holds the session's running totals
# --------------------------------------------------------------------------- #


def craft_dir() -> Path:
    return _env_path("CRAFT_CONFIG_DIR", Path.home() / ".craft-agent")


def craft_session_files() -> list[Path]:
    base = craft_dir()
    roots = [base / "workspaces"]
    config = _dict(_load_json(base / "config.json"))
    for ws in config.get("workspaces") or []:
        root = _dict(ws).get("rootPath")
        if isinstance(root, str) and root:
            roots.append(Path(root).expanduser())
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files += root.glob("*/sessions/*/session.jsonl") if root.name == "workspaces" else root.glob("sessions/*/session.jsonl")
    return sorted(set(files))


def load_craft_sessions() -> list[Session]:
    out = []
    for path in craft_session_files():
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                header = json.loads(fh.readline() or "{}")
        except (OSError, ValueError, RecursionError):
            continue
        u = _dict(_dict(header).get("tokenUsage"))
        usage = Usage(input=_int(u.get("inputTokens")), output=_int(u.get("outputTokens")),
                      cache_read=_int(u.get("cacheReadTokens")), cache_write=_int(u.get("cacheCreationTokens")))
        if not usage.total:
            continue
        ts = _when(header.get("lastMessageAt")) or _mtime(path)
        cwd = str(header.get("workingDirectory") or header.get("cwd") or "")
        s = Session(session_id=str(header.get("id") or path.parent.name), project=_project(cwd), path=path,
                    tool="Craft Agents", cwd=cwd, title=_clean(header.get("name") or header.get("title")))
        start = _when(header.get("createdAt")) or ts
        s.turns.append(Turn(prompt="(whole session; Craft keeps only session totals)", timestamp=start,
                            calls=[ApiCall(ts, str(header.get("model") or "craft"), usage, app="Craft Agents")]))
        out.append(_finish(s))
    return [s for s in out if s]


# --------------------------------------------------------------------------- #
# Droid (Factory) - <id>.settings.json holds the session's running totals
# --------------------------------------------------------------------------- #


def droid_dirs() -> list[Path]:
    if os.environ.get("DROID_SESSIONS_DIR"):
        return [Path(d.strip()).expanduser() for d in os.environ["DROID_SESSIONS_DIR"].split(",") if d.strip()]
    return [_env_path("FACTORY_DIR", Path.home() / ".factory") / "sessions"]


def _droid_model(raw) -> str:
    if not isinstance(raw, str):
        return ""
    name = raw[len("custom:"):] if raw.startswith("custom:") else raw
    name = re.sub(r"\[[^\]]*\]", "", name).strip().lower()
    return re.sub(r"-+", "-", re.sub(r"[\s.]+", "-", name)).strip("-")


def _droid_transcript(settings: Path) -> tuple[str, str]:
    """(cwd, first prompt) from the sibling <id>.jsonl transcript."""
    cwd = prompt = ""
    transcript = settings.with_name(settings.name[: -len(".settings.json")] + ".jsonl")
    for i, rec in enumerate(read_jsonl(transcript)):
        if i > 500 or (cwd and prompt):
            break
        if rec.get("type") in ("session_start", "session") and isinstance(rec.get("cwd"), str):
            cwd = cwd or rec["cwd"].strip()
        msg = _dict(rec.get("message"))
        if not prompt and msg.get("role") == "user":
            text = _clean(msg.get("content")) if isinstance(msg.get("content"), str) else _text(msg.get("content"))
            if text and not text.startswith("<"):
                prompt = text
    return cwd, prompt


def load_droid_sessions() -> list[Session]:
    best: dict[str, tuple[int, Session]] = {}
    for root in droid_dirs():
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.settings.json")):
            doc = _dict(_load_json(path))
            u = _dict(doc.get("tokenUsage"))
            usage = Usage(input=_int(u.get("inputTokens")),
                          output=_int(u.get("outputTokens")) + _int(u.get("thinkingTokens")),
                          cache_read=_int(u.get("cacheReadTokens")), cache_write=_int(u.get("cacheCreationTokens")))
            missing = _int(u.get("totalTokens")) - usage.total
            if missing > 0:
                usage.output += missing  # older files: the total is right, a detail field is missing
            if not usage.total:
                continue
            sid = path.name[: -len(".settings.json")]
            cwd, prompt = _droid_transcript(path)
            model = _droid_model(doc.get("model")) or {
                "anthropic": "claude-unknown", "openai": "gpt-unknown", "google": "gemini-unknown",
            }.get(str(doc.get("providerLock") or "").lower(), "droid-unknown")
            ts = _mtime(path)
            s = Session(session_id=sid, project=_project(cwd), path=path, tool="Droid", cwd=cwd,
                        title=_clean(doc.get("title")) or prompt)
            start = _when(doc.get("providerLockTimestamp")) or ts
            s.turns.append(Turn(prompt=prompt or "(whole session; Droid keeps only session totals)", timestamp=start,
                                calls=[ApiCall(ts, model, usage, app="Droid")]))
            # The same session can sit in two folders: keep the most complete copy.
            if sid not in best or usage.total > best[sid][0]:
                best[sid] = (usage.total, s)
    return [s for _, s in best.values() if _finish(s)]


# --------------------------------------------------------------------------- #
# Grok Build (xAI) - turn_completed events in updates.jsonl
# --------------------------------------------------------------------------- #


def grok_home() -> Path:
    return _env_path("GROK_HOME", Path.home() / ".grok")


def _grok_usage(u: dict) -> tuple[Usage, float | None]:
    read = _int(u.get("cachedReadTokens") or u.get("cacheReadInputTokens") or u.get("cache_read_input_tokens")
                or u.get("cached_input_tokens"))
    write = _int(u.get("cacheCreationTokens") or u.get("cachedWriteTokens") or u.get("cacheWriteInputTokens")
                 or u.get("cache_creation_input_tokens"))
    if u.get("inputTokens") is not None:  # camelCase: the whole prompt, cache included
        inp = max(_int(u.get("inputTokens")) - read - write, 0)
    else:
        inp = _int(u.get("input_tokens"))
    usage = Usage(input=inp, output=_int(u.get("outputTokens") or u.get("output_tokens")),
                  cache_read=read, cache_write=write)
    cost = None
    if not (u.get("costIsPartial") or u.get("cost_is_partial") or u.get("usageIsIncomplete")):
        ticks = u.get("costUsdTicks", u.get("totalCostUsdTicks", u.get("cost_usd_ticks")))
        if isinstance(ticks, (int, float)) and not isinstance(ticks, bool) and ticks >= 0:
            cost = ticks / 10_000_000_000
        else:
            cost = _cost(u.get("costUsd", u.get("totalCostUsd", u.get("cost_usd"))))
    return usage, cost


def load_grok_sessions() -> list[Session]:
    root = grok_home() / "sessions"
    if not root.is_dir():
        return []
    out = []
    for updates in sorted(root.glob("*/*/updates.jsonl")):
        folder = updates.parent
        summary = _dict(_load_json(folder / "summary.json"))
        signals = _dict(_load_json(folder / "signals.json"))
        model = str(summary.get("model") or signals.get("model") or "grok-build")
        cwd = str(summary.get("cwd") or signals.get("cwd") or "")
        s = Session(session_id=folder.name, project=_project(cwd) or folder.parent.name, path=updates,
                    tool="Grok", cwd=cwd, title=_clean(summary.get("title")))
        b, seen = _Builder(s), set()
        for i, rec in enumerate(read_jsonl(updates)):
            params = _dict(rec.get("params"))
            update = _dict(params.get("update"))
            meta = _dict(params.get("_meta")) or _dict(rec.get("_meta"))
            ts = (_when(meta.get("agentTimestampMs")) or _when(meta.get("timestampMs"))
                  or _when(rec.get("timestamp_ms")) or _when(rec.get("timestamp")))
            kind = update.get("sessionUpdate")
            if kind == "user_message_chunk":
                b.prompt(_text([update.get("content")]) if isinstance(update.get("content"), dict)
                         else _clean(update.get("content")), ts)
                continue
            if kind != "turn_completed" or not isinstance(update.get("usage"), dict):
                continue
            key = str(meta.get("eventId") or rec.get("id") or update.get("prompt_id") or i)
            if key in seen:
                continue
            seen.add(key)
            usage_all = update["usage"]
            per_model = _dict(usage_all.get("modelUsage"))
            items = [(name, _dict(v)) for name, v in per_model.items()] or [(model, usage_all)]
            for name, u in items:
                usage, cost = _grok_usage(u)
                b.call(ApiCall(ts, str(name), usage, app="Grok Build", reported_cost=cost, key=f"{key}|{name}"))
        if (s := _finish(s)):
            out.append(s)
    return out


# --------------------------------------------------------------------------- #
# GitHub Copilot (CLI and Copilot app) - ~/.copilot/session-store.db
# --------------------------------------------------------------------------- #


def copilot_home() -> Path:
    return _env_path("COPILOT_HOME", Path.home() / ".copilot")


def load_copilot_sessions() -> list[Session]:
    db = copilot_home() / "session-store.db"
    cols = _columns(db, "assistant_usage_events")
    if not cols:
        return []
    rows = _rows(db, "SELECT id, session_id, model, input_tokens, output_tokens, "
                     f"{_pick(cols, 'cache_read_tokens')} AS cache_read_tokens, "
                     f"{_pick(cols, 'cache_write_tokens')} AS cache_write_tokens, created_at "
                     "FROM assistant_usage_events ORDER BY id")
    sessions: dict[str, Session] = {}
    for r in rows:
        sid = str(r.get("session_id") or "copilot")
        if sid not in sessions:
            sessions[sid] = Session(session_id=sid, project="", path=db, tool="GitHub Copilot")
            sessions[sid].turns.append(Turn(prompt="", timestamp=None))
        prompt = _int(r.get("input_tokens"))  # the whole prompt, cache included
        read = min(_int(r.get("cache_read_tokens")), prompt)
        write = min(_int(r.get("cache_write_tokens")), prompt - read)
        usage = Usage(input=prompt - read - write, output=_int(r.get("output_tokens")), cache_read=read, cache_write=write)
        if usage.total:
            ts = _when(r.get("created_at"))
            turn = sessions[sid].turns[0]
            turn.timestamp = turn.timestamp or ts
            turn.calls.append(ApiCall(ts, str(r.get("model") or "copilot"), usage, app="Copilot", key=f"copilot:{r.get('id')}"))
    _copilot_titles(sessions)
    return [s for s in map(_finish, sessions.values()) if s]


def _copilot_titles(sessions: dict[str, Session]) -> None:
    """Titles and folders, when Copilot's session-state/<id>/ folder has them."""
    state = copilot_home() / "session-state"
    for sid, s in sessions.items():
        folder = state / sid
        for name in ("workspace.yaml", "metadata.json", "session.json"):
            path = folder / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if name.endswith(".json"):
                doc = _dict(_load_json(path))
                s.title = s.title or _clean(doc.get("summary") or doc.get("title"))
                s.cwd = s.cwd or str(doc.get("cwd") or "")
            else:
                for key, attr in (("summary", "title"), ("cwd", "cwd")):
                    m = re.search(rf"^{key}:\s*(.+)$", text, re.M)
                    if m and not getattr(s, attr):
                        setattr(s, attr, _clean(m.group(1).strip().strip("'\"")))
        if s.turns:
            s.turns[0].prompt = s.title or ""


# --------------------------------------------------------------------------- #
# Goose (Block) - sessions.db, one row per session with running totals
# --------------------------------------------------------------------------- #


def goose_db() -> Path:
    root = (os.environ.get("GOOSE_PATH_ROOT") or "").strip()
    if root:
        return Path(root).expanduser() / "data" / "sessions" / "sessions.db"
    if sys.platform == "win32":
        return _app_data() / "goose" / "sessions" / "sessions.db"
    if sys.platform == "darwin":
        return _app_data() / "goose" / "sessions" / "sessions.db"
    for candidate in (_local_data() / "goose" / "sessions" / "sessions.db",
                      _local_data() / "Block" / "goose" / "sessions" / "sessions.db"):
        if candidate.is_file():
            return candidate
    return _local_data() / "goose" / "sessions" / "sessions.db"


def load_goose_sessions() -> list[Session]:
    db = goose_db()
    cols = _columns(db, "sessions")
    if not cols:
        return []
    rows = _rows(db, f"SELECT id, model_config_json, created_at, "
                     f"{_pick(cols, 'updated_at')} AS updated_at, "
                     f"{_pick(cols, 'name', 'description')} AS title, "
                     f"{_pick(cols, 'working_dir')} AS cwd, "
                     f"{_pick(cols, 'accumulated_input_tokens', 'input_tokens')} AS inp, "
                     f"{_pick(cols, 'accumulated_output_tokens', 'output_tokens')} AS outp, "
                     f"{_pick(cols, 'accumulated_total_tokens', 'total_tokens')} AS total FROM sessions")
    out = []
    for r in rows:
        try:
            model = str(_dict(json.loads(r.get("model_config_json") or "{}")).get("model_name") or "")
        except (ValueError, RecursionError):
            model = ""
        usage = Usage(input=_int(r.get("inp")), output=_int(r.get("outp")))
        extra = _int(r.get("total")) - usage.total
        if extra > 0:
            usage.output += extra  # Goose has no cache fields; the rest is reasoning
        if not usage.total:
            continue
        start, end = _when(r.get("created_at")), _when(r.get("updated_at"))
        cwd = str(r.get("cwd") or "")
        s = Session(session_id=str(r.get("id")), project=_project(cwd), path=db, tool="Goose", cwd=cwd,
                    title=_clean(r.get("title")))
        s.turns.append(Turn(prompt=s.title or "(whole session; Goose keeps only session totals)", timestamp=start,
                            calls=[ApiCall(end or start, model or "goose", usage, app="Goose")]))
        if (s := _finish(s)):
            out.append(s)
    return out


# --------------------------------------------------------------------------- #
# Zed - threads.db, one (often zstd-compressed) JSON document per agent thread
# --------------------------------------------------------------------------- #


def zed_db() -> Path:
    if sys.platform == "darwin":
        return _app_data() / "Zed" / "threads" / "threads.db"
    if sys.platform == "win32":
        return _local_data() / "Zed" / "threads" / "threads.db"
    return _local_data() / "zed" / "threads" / "threads.db"


def _zstd_decompress(data: bytes) -> bytes | None:
    try:
        from compression import zstd  # Python 3.14+
        return zstd.decompress(data)
    except ImportError:
        pass
    try:
        import zstandard
        return zstandard.ZstdDecompressor().decompress(data, max_output_size=256 * 1024 * 1024)
    except Exception:
        return None


def _zed_usage(u) -> Usage:
    u = _dict(u)
    return Usage(input=_int(u.get("input_tokens")), output=_int(u.get("output_tokens")),
                 cache_read=_int(u.get("cache_read_input_tokens")),
                 cache_write=_int(u.get("cache_creation_input_tokens")))


def load_zed_sessions() -> list[Session]:
    db = zed_db()
    cols = _columns(db, "threads")
    if not cols:
        return []
    rows = _rows(db, f"SELECT id, {_pick(cols, 'summary')} AS summary, updated_at, "
                     f"{_pick(cols, 'data_type')} AS data_type, data FROM threads")
    out = []
    for r in rows:
        data = r.get("data")
        if isinstance(data, str):
            data = data.encode()
        if not isinstance(data, (bytes, bytearray)):
            continue
        if str(r.get("data_type") or "json").lower() == "zstd":
            data = _zstd_decompress(bytes(data))
            if data is None:
                continue
        try:
            thread = json.loads(data.decode("utf-8", "replace"))
        except (ValueError, RecursionError):
            continue
        if not isinstance(thread, dict) or thread.get("imported") is True:
            continue
        model_info = _dict(thread.get("model"))
        model = str(model_info.get("model") or "")
        if not model:
            continue
        when = _when(r.get("updated_at")) or _when(thread.get("updated_at"))
        title = _clean(r.get("summary") or thread.get("title") or thread.get("summary"))
        s = Session(session_id=str(r.get("id")), project="", path=db, tool="Zed", title=title)
        turn = Turn(prompt=title, timestamp=when)
        per_request = thread.get("request_token_usage")
        entries = list(per_request.values()) if isinstance(per_request, dict) else per_request or []
        for i, u in enumerate(entries if isinstance(entries, list) else []):
            turn.calls.append(ApiCall(when, model, _zed_usage(u), app="Zed", key=f"zed:{r.get('id')}:{i}"))
        turn.calls = [c for c in turn.calls if c.usage.total]
        if not turn.calls:
            usage = _zed_usage(thread.get("cumulative_token_usage"))
            if usage.total:
                turn.calls.append(ApiCall(when, model, usage, app="Zed"))
        s.turns.append(turn)
        if (s := _finish(s)):
            out.append(s)
    return out


# --------------------------------------------------------------------------- #
# Kiro (Amazon's VS Code-based editor) - devdata.sqlite, one row per request
# --------------------------------------------------------------------------- #


def kiro_dev_data() -> Path:
    return _app_data() / "Kiro" / "User" / "globalStorage" / "kiro.kiroagent" / "dev_data"


def _kiro_model(raw) -> str:
    name = str(raw or "").strip()
    if not name or name.lower() == "agent":
        return "kiro (model not logged)"
    if name != name.lower():  # CLAUDE_SONNET_4_20250514_V1_0 -> claude-sonnet-4
        name = re.sub(r"_\d{8}_V\d+_\d+$", "", name, flags=re.I)
        name = re.sub(r"_V\d+$", "", name, flags=re.I).lower().replace("_", "-")
    return name


def load_kiro_sessions() -> list[Session]:
    db = kiro_dev_data() / "devdata.sqlite"
    rows = _rows(db, "SELECT id, model, tokens_prompt, tokens_generated, timestamp FROM tokens_generated ORDER BY id")
    days: dict[str, Session] = {}
    for r in rows:
        usage = Usage(input=_int(r.get("tokens_prompt")), output=_int(r.get("tokens_generated")))
        if not usage.total:
            continue
        ts = _when(r.get("timestamp"))
        day = ts.astimezone().strftime("%Y-%m-%d") if ts else "unknown"
        if day not in days:
            days[day] = Session(session_id=f"kiro-{day}", project="", path=db, tool="Kiro",
                                title=f"Kiro usage on {day}")
            days[day].turns.append(Turn(prompt="(Kiro logs requests without session details)", timestamp=ts))
        days[day].turns[0].calls.append(ApiCall(ts, _kiro_model(r.get("model")), usage, app="Kiro",
                                                key=f"kiro:{r.get('id')}"))
    return [s for s in map(_finish, days.values()) if s]


# --------------------------------------------------------------------------- #
# Hermes Agent (Nous Research) - ~/.hermes/state.db, one row per session
# --------------------------------------------------------------------------- #


def hermes_home() -> Path:
    default = Path.home() / ".hermes"
    if sys.platform == "win32" and not default.exists():
        default = _local_data() / "hermes"
    return _env_path("HERMES_HOME", default)


def load_hermes_sessions() -> list[Session]:
    db = hermes_home() / "state.db"
    cols = _columns(db, "sessions")
    if not cols:
        return []
    rows = _rows(db, f"SELECT id, model, started_at, ended_at, input_tokens, output_tokens, "
                     f"{_pick(cols, 'cache_read_tokens')} AS cr, {_pick(cols, 'cache_write_tokens')} AS cw, "
                     f"{_pick(cols, 'reasoning_tokens')} AS rt, {_pick(cols, 'title')} AS title, "
                     f"{_pick(cols, 'source')} AS src FROM sessions")
    out = []
    for r in rows:
        usage = Usage(input=_int(r.get("input_tokens")), output=_int(r.get("output_tokens")) + _int(r.get("rt")),
                      cache_read=_int(r.get("cr")), cache_write=_int(r.get("cw")))
        if not usage.total:
            continue
        start = _when(r.get("started_at"))
        end = _when(r.get("ended_at")) or start
        title = _clean(r.get("title"))
        app = f"Hermes ({r['src']})" if r.get("src") else "Hermes"
        s = Session(session_id=str(r.get("id")), project="", path=db, tool="Hermes", title=title)
        s.turns.append(Turn(prompt=title or "(whole session; Hermes keeps only session totals)", timestamp=start,
                            calls=[ApiCall(end, str(r.get("model") or "hermes"), usage, app=app)]))
        if (s := _finish(s)):
            out.append(s)
    return out


# --------------------------------------------------------------------------- #
# AnythingLLM Desktop - anythingllm.db, workspace_chats.response.metrics
# --------------------------------------------------------------------------- #


def anythingllm_db() -> Path:
    if os.environ.get("ANYTHINGLLM_DB"):
        return Path(os.environ["ANYTHINGLLM_DB"]).expanduser()
    return _app_data() / "anythingllm-desktop" / "storage" / "anythingllm.db"


def load_anythingllm_sessions() -> list[Session]:
    db = anythingllm_db()
    cols = _columns(db, "workspace_chats")
    if not cols:
        return []
    rows = _rows(db, f"SELECT id, {_pick(cols, 'workspaceId')} AS ws, {_pick(cols, 'thread_id')} AS thread, "
                     f"prompt, response, createdAt FROM workspace_chats ORDER BY id")
    sessions: dict[str, Session] = {}
    for r in rows:
        try:
            metrics = _dict(_dict(json.loads(r.get("response") or "{}")).get("metrics"))
        except (ValueError, RecursionError, TypeError):
            continue
        usage = Usage(input=_int(metrics.get("prompt_tokens")), output=_int(metrics.get("completion_tokens")))
        if not usage.total:
            continue
        sid = f"workspace {r.get('ws')}" + (f" thread {r['thread']}" if r.get("thread") else "")
        if sid not in sessions:
            sessions[sid] = Session(session_id=sid, project=f"workspace {r.get('ws')}", path=db, tool="AnythingLLM")
        ts = _when(r.get("createdAt"))
        sessions[sid].turns.append(Turn(prompt=_clean(r.get("prompt")), timestamp=ts, calls=[
            ApiCall(ts, str(metrics.get("model") or "anythingllm"), usage, app="AnythingLLM", key=f"allm:{r.get('id')}")]))
    return [s for s in map(_finish, sessions.values()) if s]


# --------------------------------------------------------------------------- #
# LM Studio - local server logs (models run on your own computer: cost 0)
# --------------------------------------------------------------------------- #


def lmstudio_home() -> Path:
    return _env_path("LM_STUDIO_HOME", Path.home() / ".lmstudio")


_USAGE_RE = re.compile(r'"usage"\s*:\s*\{')
_LAST_STR = lambda field: re.compile(r'"' + field + r'"\s*:\s*"((?:[^"\\]|\\.)*)"')  # noqa: E731
_MODEL_RE, _ID_RE = _LAST_STR("model"), _LAST_STR("id")
_CREATED_RE = re.compile(r'"created(?:_at)?"\s*:\s*(\d{9,13})')


def load_lmstudio_sessions() -> list[Session]:
    root = lmstudio_home() / "server-logs"
    if not root.is_dir():
        return []
    days: dict[str, Session] = {}
    seen: set = set()
    decoder = json.JSONDecoder()
    for path in sorted(root.rglob("*.log")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        prev_end = 0
        for m in _USAGE_RE.finditer(text):
            try:
                u, end = decoder.raw_decode(text, m.end() - 1)
            except (ValueError, RecursionError):
                continue
            window = text[max(prev_end, m.start() - 20000): m.start()]
            prev_end = end
            if not isinstance(u, dict):
                continue
            models, ids, created = _MODEL_RE.findall(window), _ID_RE.findall(window), _CREATED_RE.findall(window)
            key = ids[-1] if ids else f"{path.name}:{m.start()}"
            if key in seen:
                continue
            seen.add(key)
            details = _dict(u.get("prompt_tokens_details")) or _dict(u.get("input_tokens_details"))
            cached = _int(details.get("cached_tokens"))
            prompt = _int(u.get("prompt_tokens", u.get("input_tokens")))
            usage = Usage(input=max(prompt - cached, 0), output=_int(u.get("completion_tokens", u.get("output_tokens"))),
                          cache_read=cached)
            if not usage.total:
                continue
            ts = _when(int(created[-1])) if created else _mtime(path)
            day = ts.astimezone().strftime("%Y-%m-%d") if ts else "unknown"
            if day not in days:
                days[day] = Session(session_id=f"lmstudio-{day}", project="", path=path, tool="LM Studio",
                                    title=f"LM Studio local server on {day}")
                days[day].turns.append(Turn(prompt="(requests to LM Studio's local server)", timestamp=ts))
            days[day].turns[0].calls.append(ApiCall(ts, models[-1] if models else "local model", usage,
                                                    app="LM Studio", reported_cost=0.0, key=f"lms:{key}"))
    return [s for s in map(_finish, days.values()) if s]


# --------------------------------------------------------------------------- #
# Registry: what the Sources page shows, and which folders trigger a refresh
# --------------------------------------------------------------------------- #


@dataclass
class Tool:
    name: str
    paths: Callable[[], list[Path]]   # folders (or files) where the tool keeps its data
    pattern: str                      # files to count under those folders
    how: str
    loader: Callable[[], list[Session]]


def tools() -> list[Tool]:
    from . import cursor_usage

    return [
        Tool("Cursor", lambda: [cursor_usage.cache_file()], "",
             "Automatic when Cursor is logged in. Downloads your own usage list from cursor.com "
             "using Cursor's saved login (every 15 minutes).", cursor_usage.load_cursor_sessions),
        Tool("GitHub Copilot", lambda: [copilot_home() / "session-store.db"], "",
             "Automatic. Reads the usage Copilot CLI and the Copilot app save in session-store.db.",
             load_copilot_sessions),
        Tool("Kiro", lambda: [kiro_dev_data() / "devdata.sqlite"], "",
             "Automatic. Reads Kiro's request log (Kiro doesn't save which session a request belongs to).",
             load_kiro_sessions),
        Tool("Zed", lambda: [zed_db()], "",
             "Automatic. Reads Zed's agent threads (all model providers).", load_zed_sessions),
        Tool("Goose", lambda: [goose_db()], "",
             "Automatic. Reads Goose's session database (session totals).", load_goose_sessions),
        Tool("Droid (Factory)", droid_dirs, "*.settings.json",
             "Automatic. Reads Droid's session files (session totals).", load_droid_sessions),
        Tool("Grok Build", lambda: [grok_home() / "sessions"], "updates.jsonl",
             "Automatic. Reads Grok Build's session updates, including the cost xAI reports.", load_grok_sessions),
        Tool("Kimi", lambda: [kimi_home() / "sessions", kimi_code_home() / "sessions"], "wire.jsonl",
             "Automatic. Reads Kimi CLI and Kimi Code session logs.", load_kimi_sessions),
        Tool("CodeBuddy", lambda: [codebuddy_home() / "projects"], "*.jsonl",
             "Automatic. Reads CodeBuddy CLI session logs.", load_codebuddy_sessions),
        Tool("WorkBuddy", lambda: [workbuddy_home() / "projects"], "*.jsonl",
             "Automatic. Reads WorkBuddy session logs, sub-agents included.", load_workbuddy_sessions),
        Tool("Pi / oh-my-pi / OmO", lambda: list(pi_dirs().values()), "*.jsonl",
             "Automatic. Reads Pi-style agent session logs.", load_pi_sessions),
        Tool("MiniMax Code", lambda: [minimax_dir()], "messages.jsonl",
             "Automatic. Reads MiniMax Code session logs.", load_minimax_sessions),
        Tool("Craft Agents", lambda: [craft_dir()], "session.jsonl",
             "Automatic. Reads Craft Agents session files (session totals).", load_craft_sessions),
        Tool("Hermes Agent", lambda: [hermes_home() / "state.db"], "",
             "Automatic. Reads Hermes Agent's session database (session totals).", load_hermes_sessions),
        Tool("AnythingLLM", lambda: [anythingllm_db()], "",
             "Automatic. Reads AnythingLLM Desktop's chat history.", load_anythingllm_sessions),
        Tool("LM Studio", lambda: [lmstudio_home() / "server-logs"], "*.log",
             "Automatic. Reads LM Studio's local server logs (local models are free: cost 0).",
             load_lmstudio_sessions),
    ]


def load_all() -> list[Session]:
    """Every tool in this module. One tool's odd data never hides the others."""
    out: list[Session] = []
    for tool in tools():
        try:
            out += tool.loader()
        except Exception as exc:
            print(f"ai-token-tracker: skipped {tool.name}: {exc!r}", file=sys.stderr)
    return out
