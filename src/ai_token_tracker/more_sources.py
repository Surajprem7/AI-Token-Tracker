"""Readers for more AI coding tools (all read-only, from files the tools already write).

* OpenCode              - ~/.local/share/opencode/opencode*.db (SQLite), or the older
                          storage/message/<session>/<message>.json files
* Cline / Roo Code / Kilo Code (VS Code extensions)
                        - <VS Code user data>/globalStorage/<extension>/tasks/<id>/ui_messages.json
* Qwen Code             - ~/.qwen/tmp/<project>/chats (same format as Gemini CLI)

Formats were checked against each tool's own source code.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import math

from .core import ApiCall, Session, Turn, Usage, read_jsonl
from .sources import _int, _text as _text_of


def _cost(value) -> float | None:
    """A cost figure a tool logged itself, if it is a sane number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _ms(value) -> datetime | None:
    if isinstance(value, (int, float)) and value > 0:
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _clean(text) -> str:
    return " ".join(text.split()) if isinstance(text, str) else ""


# --------------------------------------------------------------------------- #
# OpenCode
# --------------------------------------------------------------------------- #


def opencode_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base).expanduser() / "opencode"


def _opencode_usage(tokens: dict) -> Usage:
    # OpenCode already subtracts cached tokens from "input"; reasoning is billed as output.
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    return Usage(
        input=_int(tokens.get("input")),
        output=_int(tokens.get("output")) + _int(tokens.get("reasoning")),
        cache_read=_int(cache.get("read")),
        cache_write=_int(cache.get("write")),
    )


class _OpenCodeBuilder:
    """Collects messages from either storage format into sessions."""

    def __init__(self):
        self.sessions: dict[str, dict] = {}   # id -> {title, cwd, parent, created}
        self.messages: dict[str, list] = {}   # session id -> [(created, kind, payload)]
        self.prompts: dict[str, str] = {}     # user message id -> text
        self.seen: set = set()                # fork copies repeat the same assistant message

    def add_message(self, session_id: str, msg_id: str, data: dict) -> None:
        created = (data.get("time") or {}).get("created") if isinstance(data.get("time"), dict) else None
        role = data.get("role")
        bucket = self.messages.setdefault(session_id, [])
        if role == "user":
            bucket.append((created or 0, "user", msg_id))
        elif role == "assistant" and isinstance(data.get("tokens"), dict):
            usage = _opencode_usage(data["tokens"])
            if not usage.total:
                return
            fingerprint = (created, data.get("modelID"), usage.input, usage.output, usage.cache_read, usage.cache_write)
            if fingerprint in self.seen:
                return  # the same reply copied into a forked session
            self.seen.add(fingerprint)
            path = data.get("path") if isinstance(data.get("path"), dict) else {}
            info = self.sessions.setdefault(session_id, {})
            info.setdefault("cwd", path.get("root") or path.get("cwd") or "")
            call = ApiCall(_ms(created), str(data.get("modelID") or ""), usage, app="OpenCode")
            bucket.append((created or 0, "call", call))

    def build(self, source: Path) -> list[Session]:
        out: dict[str, Session] = {}
        for sid, items in self.messages.items():
            info = self.sessions.get(sid, {})
            s = Session(session_id=sid, project="", path=source, tool="OpenCode",
                        cwd=info.get("cwd") or "", title=_clean(info.get("title")))
            turn = None
            for created, kind, payload in sorted(items, key=lambda x: x[0]):
                if kind == "user":
                    text = _clean(self.prompts.get(payload))
                    turn = Turn(prompt=text, timestamp=_ms(created))
                    s.turns.append(turn)
                else:
                    if turn is None:
                        turn = Turn(prompt="", timestamp=payload.timestamp)
                        s.turns.append(turn)
                    turn.calls.append(payload)
            s.turns = [t for t in s.turns if t.calls]
            if s.cwd:
                s.project = Path(s.cwd).name or s.cwd
            if not s.title:
                s.title = next((t.prompt for t in s.turns if t.prompt), "") or "(no prompt)"
            out[sid] = s
        # Sub-agent sessions (parent_id set) become a "[sub-agent]" prompt in their parent.
        for sid, info in self.sessions.items():
            parent = info.get("parent")
            if parent and parent in out and sid in out and out[sid].calls:
                child = out.pop(sid)
                calls = [c for t in child.turns for c in t.calls]
                for c in calls:
                    c.subagent = True
                out[parent].turns.append(Turn(prompt=f"[sub-agent] {child.title}", timestamp=calls[0].timestamp, calls=calls))
        return [s for s in out.values() if s.calls]


def _read_opencode_db(path: Path, b: _OpenCodeBuilder) -> None:
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=2)
    except sqlite3.Error:
        return
    try:
        con.row_factory = sqlite3.Row
        for row in con.execute("SELECT * FROM session"):
            keys = row.keys()
            b.sessions[row["id"]] = {
                "title": row["title"] if "title" in keys else "",
                "cwd": row["directory"] if "directory" in keys else "",
                "parent": row["parent_id"] if "parent_id" in keys else None,
            }
        user_ids = set()
        for row in con.execute("SELECT id, session_id, data FROM message"):
            try:
                data = json.loads(row["data"])
            except (TypeError, ValueError, RecursionError):
                continue
            if isinstance(data, dict):
                b.add_message(row["session_id"], row["id"], data)
                if data.get("role") == "user":
                    user_ids.add(row["id"])
        # The prompt text lives in "text" parts of the user's message.
        for row in con.execute("SELECT message_id, data FROM part"):
            if row["message_id"] not in user_ids or row["message_id"] in b.prompts:
                continue
            try:
                data = json.loads(row["data"])
            except (TypeError, ValueError, RecursionError):
                continue
            if isinstance(data, dict) and data.get("type") == "text" and not data.get("synthetic"):
                b.prompts[row["message_id"]] = data.get("text") or ""
    except sqlite3.Error:
        pass  # a newer/older schema we don't know: skip rather than fail
    finally:
        con.close()


def _read_opencode_files(storage: Path, b: _OpenCodeBuilder) -> None:
    """Older OpenCode versions: one JSON file per session, message and part."""
    def load(p: Path):
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError, RecursionError):
            return None

    for p in (storage / "session").rglob("*.json") if (storage / "session").is_dir() else []:
        data = load(p)
        if data and data.get("id"):
            b.sessions.setdefault(data["id"], {}).update(
                title=data.get("title") or "", cwd=data.get("directory") or "", parent=data.get("parentID"))
    user_ids = set()
    msg_dir = storage / "message"
    if msg_dir.is_dir():
        for p in msg_dir.rglob("*.json"):
            data = load(p)
            if not data:
                continue
            sid = data.get("sessionID") or p.parent.name
            b.add_message(sid, data.get("id") or p.stem, data)
            if data.get("role") == "user":
                user_ids.add(data.get("id") or p.stem)
    part_dir = storage / "part"
    for mid in user_ids:
        d = part_dir / mid
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            data = load(p)
            if data and data.get("type") == "text" and not data.get("synthetic"):
                b.prompts[mid] = data.get("text") or ""
                break


def load_opencode_sessions() -> list[Session]:
    root = opencode_data_dir()
    if not root.is_dir():
        return []
    b = _OpenCodeBuilder()
    dbs = [p for p in sorted(root.glob("opencode*.db")) if p.is_file()]
    for db in dbs:
        _read_opencode_db(db, b)
    if (root / "storage").is_dir():
        _read_opencode_files(root / "storage", b)  # fork/fingerprint check avoids double counting after migration
    return b.build(dbs[0] if dbs else root / "storage")


# --------------------------------------------------------------------------- #
# Cline (CLI / desktop app, ~/.cline) and the VS Code extensions
# Cline, Roo Code and Kilo Code (globalStorage/<extension>/tasks/*/ui_messages.json)
# --------------------------------------------------------------------------- #

VSCODE_EXTENSIONS = {
    "saoudrizwan.claude-dev": "Cline",
    "rooveterinaryinc.roo-cline": "Roo Code",
    "kilocode.kilo-code": "Kilo Code",
}
VSCODE_EDITORS = ["Code", "Code - Insiders", "Cursor", "Windsurf", "VSCodium", "Trae", "Kiro"]


def vscode_user_dirs() -> list[Path]:
    """<editor>/User folders for VS Code and its forks, on Windows, macOS and Linux."""
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return [base / editor / "User" for editor in VSCODE_EDITORS]


def cline_dir() -> Path:
    return Path(os.environ.get("CLINE_DIR") or Path.home() / ".cline").expanduser()


def cline_sessions_dir() -> Path:
    """Cline's own lookup order: CLINE_SESSION_DATA_DIR, then CLINE_DATA_DIR/sessions, then ~/.cline/data/sessions."""
    if os.environ.get("CLINE_SESSION_DATA_DIR"):
        return Path(os.environ["CLINE_SESSION_DATA_DIR"]).expanduser()
    data_dir = Path(os.environ.get("CLINE_DATA_DIR") or cline_dir() / "data").expanduser()
    return data_dir / "sessions"


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError, RecursionError):
        return None


def load_cline_sessions() -> list[Session]:
    """Cline CLI / desktop app: <cline dir>/data/sessions/<id>/<id>.messages.json."""
    sessions_dir = cline_sessions_dir()
    if not sessions_dir.is_dir():
        return []
    out = []
    for path in sorted(sessions_dir.rglob("*.messages.json")):
        doc = _load_json(path)
        messages = doc.get("messages") if isinstance(doc, dict) else doc
        if not isinstance(messages, list):
            continue
        sid = path.name[: -len(".messages.json")]
        meta = _load_json(path.with_name(sid + ".json"))
        meta = meta if isinstance(meta, dict) else {}
        cwd = meta.get("cwd") or meta.get("workspaceRoot") or ""
        s = Session(session_id=sid, project=Path(cwd).name if cwd else "", path=path, tool="Cline", cwd=cwd,
                    title=_clean(meta.get("title") or meta.get("prompt") or ""))
        turn = None
        for m in messages:
            if not isinstance(m, dict):
                continue
            ts = _ms(m.get("ts"))
            if m.get("role") == "user":
                text = _text_of(m.get("content"))
                if text:
                    turn = Turn(prompt=text, timestamp=ts)
                    s.turns.append(turn)
                continue
            metrics = m.get("metrics")
            if m.get("role") != "assistant" or not isinstance(metrics, dict):
                continue
            # Cline's inputTokens include the cached part; outputTokens include reasoning.
            read, write = _int(metrics.get("cacheReadTokens")), _int(metrics.get("cacheWriteTokens"))
            usage = Usage(input=max(_int(metrics.get("inputTokens")) - read - write, 0),
                          output=_int(metrics.get("outputTokens")), cache_read=read, cache_write=write)
            if not usage.total:
                continue
            info = m.get("modelInfo") if isinstance(m.get("modelInfo"), dict) else {}
            cost = metrics.get("cost")
            call = ApiCall(ts, str(info.get("id") or ""), usage, app="Cline",
                           reported_cost=_cost(cost))
            if turn is None:
                turn = Turn(prompt="", timestamp=ts)
                s.turns.append(turn)
            turn.calls.append(call)
        s.turns = [t for t in s.turns if t.calls]
        if not s.title:
            s.title = next((t.prompt for t in s.turns if t.prompt), "") or "(no prompt)"
        if s.calls:
            out.append(s)
    return out


def load_vscode_extension_tasks(user_dirs: list[Path] | None = None) -> list[Session]:
    """Cline / Roo Code / Kilo Code in VS Code (and forks): tasks/<id>/ui_messages.json."""
    out = []
    for user_dir in user_dirs if user_dirs is not None else vscode_user_dirs():
        editor = user_dir.parent.name
        for ext_id, tool in VSCODE_EXTENSIONS.items():
            tasks = user_dir / "globalStorage" / ext_id / "tasks"
            if not tasks.is_dir():
                continue
            for path in sorted(tasks.glob("*/ui_messages.json")):
                s = _load_ui_messages(path, tool, f"{tool} ({editor})")
                if s:
                    out.append(s)
    return out


def _history_model(task_dir: Path) -> str:
    """Roo Code doesn't log the model per request; its API history names it in <model> tags."""
    try:
        text = (task_dir / "api_conversation_history.json").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    found = re.findall(r"<model>\s*([^<\s][^<]{0,120}?)\s*</model>", text)
    return found[-1] if found else ""


def _load_ui_messages(path: Path, tool: str, app: str) -> Session | None:
    messages = _load_json(path)
    if not isinstance(messages, list):
        return None
    fallback_model = ""
    s = Session(session_id=path.parent.name, project="", path=path, tool=tool)
    turn = None
    for m in messages:
        if not isinstance(m, dict) or m.get("type") != "say":
            continue
        ts = _ms(m.get("ts"))
        kind = m.get("say")
        if kind in ("task", "user_feedback"):
            text = _clean(m.get("text"))
            if text:
                turn = Turn(prompt=text, timestamp=ts)
                s.turns.append(turn)
        elif kind in ("api_req_started", "deleted_api_reqs"):
            # deleted_api_reqs: one entry holding the summed usage of requests that "Restore Task"
            # or a message delete removed from the chat. They were still paid for, so they count.
            try:
                info = json.loads(m.get("text") or "{}")
            except (ValueError, RecursionError):
                continue
            if not isinstance(info, dict):
                continue
            # tokensIn is already the uncached part of the prompt
            usage = Usage(input=_int(info.get("tokensIn")), output=_int(info.get("tokensOut")),
                          cache_write=_int(info.get("cacheWrites")), cache_read=_int(info.get("cacheReads")))
            if not usage.total:
                continue
            cost = info.get("cost")
            model = info.get("model") or info.get("modelId")
            if not model:
                if fallback_model == "":
                    fallback_model = _history_model(path.parent) or f"model not logged ({tool})"
                model = fallback_model
            model = str(model)
            call = ApiCall(ts, model, usage, app=app,
                           reported_cost=_cost(cost))
            if turn is None:
                turn = Turn(prompt="", timestamp=ts)
                s.turns.append(turn)
            turn.calls.append(call)
    s.turns = [t for t in s.turns if t.calls]
    s.title = next((t.prompt for t in s.turns if t.prompt), "") or "(no prompt)"
    return s if s.calls else None


# --------------------------------------------------------------------------- #
# Qwen Code - ~/.qwen/projects/<project>/chats/<session>.jsonl (or $QWEN_HOME)
# --------------------------------------------------------------------------- #


def qwen_dir() -> Path:
    return Path(os.environ.get("QWEN_HOME") or Path.home() / ".qwen").expanduser()


def _qwen_usage(ev: dict) -> Usage:
    # input_token_count includes cached tokens. Thinking tokens are already part of the
    # output for OpenAI-compatible / Qwen OAuth logins, and separate for Gemini logins.
    cached = _int(ev.get("cached_content_token_count"))
    thoughts = 0 if ev.get("auth_type") in ("openai", "qwen-oauth") else _int(ev.get("thoughts_token_count"))
    return Usage(input=max(_int(ev.get("input_token_count")) - cached, 0),
                 output=_int(ev.get("output_token_count")) + thoughts, cache_read=cached)


def load_qwen_sessions() -> list[Session]:
    from .core import parse_ts

    projects = qwen_dir() / "projects"
    if not projects.is_dir():
        return []
    out = []
    for path in sorted(projects.glob("*/chats/*.jsonl")):
        if path.name.endswith(".ledger.jsonl"):
            continue
        s = Session(session_id=path.stem, project="", path=path, tool="Qwen Code")
        turn = None
        for rec in read_jsonl(path):
            ts = parse_ts(rec.get("timestamp"))
            s.cwd = s.cwd or rec.get("cwd") or ""
            if rec.get("type") == "user" and rec.get("provenance", "real_user") == "real_user":
                msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
                text = _text_of(msg.get("parts"))
                if text and not text.startswith("/"):
                    turn = Turn(prompt=text, timestamp=ts)
                    s.turns.append(turn)
            elif rec.get("type") == "system" and rec.get("subtype") == "ui_telemetry":
                ev = (rec.get("systemPayload") or {}).get("uiEvent")
                if not isinstance(ev, dict) or ev.get("event.name") != "qwen-code.api_response":
                    continue
                usage = _qwen_usage(ev)
                if not usage.total:
                    continue
                call = ApiCall(parse_ts(ev.get("event.timestamp")) or ts, str(ev.get("model") or ""), usage,
                               subagent=bool(ev.get("subagent_name")), app="Qwen Code")
                if turn is None:
                    turn = Turn(prompt="", timestamp=ts)
                    s.turns.append(turn)
                turn.calls.append(call)
        s.turns = [t for t in s.turns if t.calls]
        if s.cwd:
            s.project = Path(s.cwd).name or s.cwd
        s.title = next((t.prompt for t in s.turns if t.prompt), "") or "(no prompt)"
        if s.calls:
            out.append(s)
    return out
