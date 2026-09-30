"""Readers for AI tools other than Claude Code, plus a custom log for anything else.

Supported out of the box (all read-only, from files the tools already write):

* OpenAI Codex CLI  - $CODEX_HOME or ~/.codex/{sessions,archived_sessions}/**/*.jsonl
* Google Gemini CLI - ~/.gemini/tmp/<project>/chats/*.jsonl (and older *.json)
* Custom log        - ~/.ai-token-tracker/usage/*.jsonl or *.csv, for any other AI.
                      Write to it with `ai-tokens --add ...` or log_usage().
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .core import ApiCall, Session, Turn, Usage, parse_ts, read_jsonl


def load_other_tools() -> list[Session]:
    sessions: list[Session] = []
    for loader in (load_codex_sessions, load_gemini_sessions, load_custom_sessions):
        try:
            sessions += loader()
        except OSError:
            continue  # unreadable folder: skip that tool rather than fail
    return [s for s in sessions if s.calls]


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _int(value) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def _text(content) -> str:
    """Flatten a string / list-of-parts message body to plain text."""
    if isinstance(content, str):
        return " ".join(content.split())
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
        return " ".join(" ".join(parts).split())
    return ""


def _build(session: Session, events: list) -> Session:
    """events: ("prompt", text, ts) and ("call", ApiCall) in file order."""
    turn: Turn | None = None
    for ev in events:
        if ev[0] == "prompt":
            turn = Turn(prompt=ev[1], timestamp=ev[2])
            session.turns.append(turn)
        else:
            if turn is None:
                turn = Turn(prompt="", timestamp=ev[1].timestamp)
                session.turns.append(turn)
            turn.calls.append(ev[1])
    session.turns = [t for t in session.turns if t.calls or t.prompt]
    if not session.title:
        session.title = next((t.prompt for t in session.turns if t.prompt), "") or "(no prompt)"
    if session.cwd and not session.project:
        session.project = Path(session.cwd).name or session.cwd
    return session


# --------------------------------------------------------------------------- #
# OpenAI Codex CLI
# --------------------------------------------------------------------------- #

CODEX_APPS = {
    "codex_cli_rs": "Codex CLI (terminal)",
    "codex_vscode": "Codex (VS Code)",
    "codex_exec": "Codex exec",
    "codex_desktop": "Codex app",
}


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def _codex_usage(raw: dict) -> Usage:
    # OpenAI counts cached (and cache-write) tokens inside input_tokens, and
    # reasoning tokens inside output_tokens.
    cached = _int(raw.get("cached_input_tokens"))
    written = _int(raw.get("cache_write_input_tokens"))
    return Usage(
        input=max(_int(raw.get("input_tokens")) - cached - written, 0),
        output=_int(raw.get("output_tokens")),
        cache_write=written,
        cache_read=cached,
    )


def load_codex_sessions() -> list[Session]:
    home = codex_home()
    files = []
    for sub in ("sessions", "archived_sessions"):
        folder = home / sub
        if folder.is_dir():
            files += sorted(folder.rglob("*.jsonl"))
    return [load_codex_file(f) for f in files]


def load_codex_file(path: Path) -> Session:
    session = Session(session_id=path.stem, project="", path=path, tool="Codex CLI")
    model, app = "", "Codex CLI"
    prompts_and_records: list = []  # newer versions: one record per response
    prompts_and_totals: list = []   # older versions: running totals only
    seen_responses: set = set()
    last_total: dict | None = None

    for entry in read_jsonl(path):
        kind, payload = entry.get("type"), entry.get("payload")
        if not isinstance(payload, dict):
            continue
        ts = parse_ts(entry.get("timestamp"))
        if kind == "session_meta":
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else payload
            session.session_id = meta.get("id") or session.session_id
            session.cwd = meta.get("cwd") or session.cwd
            app = CODEX_APPS.get(meta.get("originator", ""), meta.get("originator") or app)
            git = payload.get("git") if isinstance(payload.get("git"), dict) else {}
            session.branch = git.get("branch") or session.branch
        elif kind == "turn_context":
            model = payload.get("model") or model
            session.cwd = session.cwd or payload.get("cwd") or ""
        elif kind == "event_msg" and payload.get("type") == "user_message":
            text = _text(payload.get("message"))
            if text:
                ev = ("prompt", text, ts)
                prompts_and_records.append(ev)
                prompts_and_totals.append(ev)
        elif kind == "token_usage_record" and isinstance(payload.get("usage"), dict):
            rid = payload.get("response_id")
            if rid and rid in seen_responses:
                continue
            seen_responses.add(rid)
            prompts_and_records.append(("call", ApiCall(ts, model, _codex_usage(payload["usage"]), app=app)))
        elif kind == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            total = info.get("total_token_usage") if isinstance(info, dict) else None
            if not isinstance(total, dict) or total == last_total:
                continue  # repeated snapshot (e.g. rate-limit only update)
            prev = last_total or {}
            delta = {k: _int(total.get(k)) - _int(prev.get(k)) for k in
                     ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens")}
            if any(v < 0 for v in delta.values()):  # counter reset (e.g. after compaction)
                delta = {k: _int(total.get(k)) for k in delta}
            last_total = total
            prompts_and_totals.append(("call", ApiCall(ts, model, _codex_usage(delta), app=app)))

    has_records = any(ev[0] == "call" for ev in prompts_and_records)
    return _build(session, prompts_and_records if has_records else prompts_and_totals)


# --------------------------------------------------------------------------- #
# Google Gemini CLI
# --------------------------------------------------------------------------- #


def gemini_tmp_dirs() -> list[Path]:
    home = Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()).expanduser()
    dirs = [home / ".gemini" / "tmp", home / ".cache" / ".gemini" / "tmp"]
    return [d for d in dirs if d.is_dir()]


def _gemini_usage(tokens: dict) -> Usage:
    # promptTokenCount includes cached tokens; thoughts are billed as output.
    cached = _int(tokens.get("cached"))
    return Usage(
        input=max(_int(tokens.get("input")) - cached, 0) + _int(tokens.get("tool")),
        output=_int(tokens.get("output")) + _int(tokens.get("thoughts")),
        cache_read=cached,
    )


def _gemini_records(path: Path):
    """Yield (metadata, messages) from either the .jsonl log or a legacy .json file."""
    if path.suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return {}, []
        if not isinstance(data, dict):
            return {}, []
        return data, [m for m in data.get("messages") or [] if isinstance(m, dict)]
    meta: dict = {}
    messages: dict[str, dict] = {}  # same id is re-written when tokens arrive; keep last
    for rec in read_jsonl(path):
        if isinstance(rec.get("$set"), dict):
            meta.update(rec["$set"])
        elif "sessionId" in rec and "projectHash" in rec:
            meta.update(rec)
        elif isinstance(rec.get("id"), str) and "type" in rec:
            messages[rec["id"]] = rec
        # "$rewindTo" hides messages from the chat, but their tokens were still spent.
    return meta, list(messages.values())


def load_gemini_sessions() -> list[Session]:
    merged: dict[str, Session] = {}
    for tmp in gemini_tmp_dirs():
        for project_dir in sorted(p for p in tmp.iterdir() if p.is_dir()):
            chats = project_dir / "chats"
            if not chats.is_dir():
                continue
            root_file = project_dir / ".project_root"
            try:
                cwd = root_file.read_text(encoding="utf-8").strip() if root_file.is_file() else ""
            except OSError:
                cwd = ""
            for path in sorted(list(chats.rglob("*.jsonl")) + list(chats.rglob("*.json"))):
                if path.suffix == ".json" and path.with_suffix(".jsonl").exists():
                    continue  # already migrated to .jsonl
                sub_agent = path.parent != chats
                meta, messages = _gemini_records(path)
                sid = path.parent.name if sub_agent else (meta.get("sessionId") or path.stem)
                s = merged.get(sid)
                if s is None:
                    s = Session(session_id=sid, project=Path(cwd).name if cwd else project_dir.name,
                                path=path, tool="Gemini CLI", cwd=cwd, title=meta.get("summary") or "")
                    merged[sid] = s
                events = []
                for m in sorted(messages, key=lambda m: m.get("timestamp") or ""):
                    ts = parse_ts(m.get("timestamp"))
                    if m.get("type") == "user" and not sub_agent:
                        text = _text(m.get("content"))
                        if text and not text.startswith("/"):
                            events.append(("prompt", text, ts))
                    elif m.get("type") == "gemini" and isinstance(m.get("tokens"), dict):
                        call = ApiCall(ts, m.get("model") or "", _gemini_usage(m["tokens"]),
                                       subagent=sub_agent, app="Gemini CLI")
                        events.append(("call", call))
                if sub_agent and events:
                    events.insert(0, ("prompt", f"[sub-agent] {path.stem}", events[0][1].timestamp))
                _build(s, events)
    return list(merged.values())


# --------------------------------------------------------------------------- #
# Custom log: any other AI
# --------------------------------------------------------------------------- #

CUSTOM_FIELDS = ["timestamp", "tool", "model", "session", "project", "app", "prompt",
                 "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"]


def custom_dir() -> Path:
    base = os.environ.get("AI_TOKEN_TRACKER_DIR") or Path.home() / ".ai-token-tracker"
    return Path(base).expanduser() / "usage"


def log_usage(tool: str, model: str, input_tokens: int = 0, output_tokens: int = 0,
              cache_read_tokens: int = 0, cache_write_tokens: int = 0, session: str | None = None,
              prompt: str = "", project: str = "", app: str = "",
              timestamp: datetime | None = None) -> Path:
    """Append one usage record to the custom log. Use it from your own scripts:

        from ai_token_tracker import log_usage
        r = client.messages.create(...)
        log_usage("My app", r.model, r.usage.input_tokens, r.usage.output_tokens)
    """
    ts = timestamp or datetime.now(timezone.utc)
    record = {
        "timestamp": ts.isoformat(), "tool": tool, "model": model,
        "session": session or f"{tool} {ts.astimezone().strftime('%Y-%m-%d')}",
        "project": project, "app": app, "prompt": prompt,
        "input_tokens": int(input_tokens), "output_tokens": int(output_tokens),
        "cache_read_tokens": int(cache_read_tokens), "cache_write_tokens": int(cache_write_tokens),
    }
    folder = custom_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "usage.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return path


def _custom_rows(path: Path):
    if path.suffix.lower() == ".csv":
        try:
            with path.open(encoding="utf-8-sig", newline="") as fh:
                yield from csv.DictReader(fh)
        except OSError:
            return
    else:
        yield from read_jsonl(path)


def _num(row: dict, *keys: str) -> int:
    for k in keys:
        v = row.get(k)
        try:
            n = int(float(v)) if v not in (None, "") else 0
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            return n
    return 0


def load_custom_sessions() -> list[Session]:
    folder = custom_dir()
    if not folder.is_dir():
        return []
    sessions: dict[tuple, tuple[Session, list]] = {}
    for path in sorted(list(folder.glob("*.jsonl")) + list(folder.glob("*.csv"))):
        for row in _custom_rows(path):
            if not isinstance(row, dict):
                continue
            tool = (row.get("tool") or path.stem).strip()
            ts = parse_ts(row.get("timestamp")) or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            sid = (row.get("session") or f"{tool} {ts.astimezone().strftime('%Y-%m-%d')}").strip()
            key = (tool, sid)
            if key not in sessions:
                s = Session(session_id=sid, project=row.get("project") or "", path=path, tool=tool)
                sessions[key] = (s, [])
            s, events = sessions[key]
            s.project = s.project or row.get("project") or ""
            if row.get("prompt"):
                events.append(("prompt", _text(row["prompt"]), ts))
            usage = Usage(
                input=_num(row, "input_tokens", "input", "prompt_tokens"),
                output=_num(row, "output_tokens", "output", "completion_tokens"),
                cache_read=_num(row, "cache_read_tokens", "cache_read", "cached_tokens"),
                cache_write=_num(row, "cache_write_tokens", "cache_write"),
            )
            events.append(("call", ApiCall(ts, row.get("model") or "", usage, app=row.get("app") or tool)))
    out = []
    for s, events in sessions.values():
        events.sort(key=lambda ev: (ev[2] if ev[0] == "prompt" else ev[1].timestamp) or datetime.min.replace(tzinfo=timezone.utc))
        out.append(_build(s, events))
    return out


# --------------------------------------------------------------------------- #
# Where each tool's data lives (for the Sources page / --sources)
# --------------------------------------------------------------------------- #


def source_status(claude_roots: list[Path] | None = None) -> list[dict]:
    """One row per supported tool: where we look, and whether anything is there."""
    from .core import default_roots

    def row(tool: str, paths: list[Path], pattern: str, how: str) -> dict:
        found = [p for p in paths if p.is_dir()]
        files = 0
        for p in found:
            try:
                files += sum(1 for _ in p.rglob(pattern))
            except OSError:
                pass
        return {"tool": tool, "paths": [str(p) for p in paths], "found": bool(found),
                "files": files, "how": how}

    claude = claude_roots if claude_roots is not None else default_roots()
    return [
        row("Claude Code", claude or [Path.home() / ".claude" / "projects"], "*.jsonl",
            "Automatic. Reads the session transcripts Claude Code saves."),
        row("Codex CLI", [codex_home() / "sessions", codex_home() / "archived_sessions"], "*.jsonl",
            "Automatic. Reads Codex rollout logs (set CODEX_HOME if you moved them)."),
        row("Gemini CLI", gemini_tmp_dirs() or [Path.home() / ".gemini" / "tmp"], "*.json*",
            "Automatic. Reads Gemini CLI chat logs."),
        row("Custom log", [custom_dir()], "*.*",
            "Any other AI: `ai-tokens --add`, log_usage() in your scripts, or drop a CSV here."),
    ]
