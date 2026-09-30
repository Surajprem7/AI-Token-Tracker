"""Shared data model, the Claude Code reader, and grouping/formatting helpers.

The readers for other AI tools live in sources.py. Every reader turns a tool's
local log files into the same Session -> Turn -> ApiCall structure, so the CLI
and the app don't care where the numbers came from.

Claude Code writes a transcript for every session to
~/.claude/projects/<project>/<session-id>.jsonl. Every assistant message in
those files carries the API `usage` block, so we can add up exactly what each
session consumed without any API key or network access. Nothing is written
and nothing leaves the computer.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

TOKEN_FIELDS = ("input", "output", "cache_write", "cache_read")


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    cache_write_1h: int = 0  # part of cache_write that used the 1-hour cache (priced higher)
    cost: float = 0.0        # estimated USD, filled in by pricing.apply_costs()
    unpriced: int = 0        # tokens from models with no known price

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_write + self.cache_read

    def add(self, other: "Usage") -> None:
        for f in TOKEN_FIELDS + ("cache_write_1h", "cost", "unpriced"):
            setattr(self, f, getattr(self, f) + getattr(other, f))

    def to_dict(self) -> dict:
        d = {f: getattr(self, f) for f in TOKEN_FIELDS}
        d["total"] = self.total
        d["cost_usd"] = round(self.cost, 6)
        return d


@dataclass
class ApiCall:
    timestamp: datetime | None
    model: str
    usage: Usage
    subagent: bool = False
    app: str = ""  # which Claude app sent the request (terminal, desktop, web, ...)


@dataclass
class Turn:
    """One user prompt and all the API calls it triggered."""

    prompt: str
    timestamp: datetime | None
    calls: list[ApiCall] = field(default_factory=list)

    @property
    def usage(self) -> Usage:
        u = Usage()
        for c in self.calls:
            u.add(c.usage)
        return u


@dataclass
class Session:
    session_id: str
    project: str
    path: Path
    tool: str = "Claude Code"  # which AI tool the session belongs to
    title: str = ""
    cwd: str = ""
    branch: str = ""
    turns: list[Turn] = field(default_factory=list)

    @property
    def calls(self) -> list[ApiCall]:
        return [c for t in self.turns for c in t.calls]

    @property
    def usage(self) -> Usage:
        u = Usage()
        for t in self.turns:
            u.add(t.usage)
        return u

    @property
    def models(self) -> list[str]:
        seen: dict[str, None] = {}
        for c in self.calls:
            if c.model and not c.model.startswith("<"):
                seen[c.model] = None
        return list(seen)

    @property
    def app(self) -> str:
        counts: dict[str, int] = {}
        for c in self.calls:
            counts[c.app] = counts.get(c.app, 0) + 1
        return max(counts, key=counts.get) if counts else ""

    @property
    def start(self) -> datetime | None:
        stamps = [c.timestamp for c in self.calls if c.timestamp]
        stamps += [t.timestamp for t in self.turns if t.timestamp]
        return min(stamps) if stamps else None

    @property
    def end(self) -> datetime | None:
        stamps = [c.timestamp for c in self.calls if c.timestamp]
        return max(stamps) if stamps else self.start

    def to_dict(self, with_turns: bool = True) -> dict:
        d = {
            "session_id": self.session_id,
            "tool": self.tool,
            "project": self.project,
            "title": self.title,
            "cwd": self.cwd,
            "git_branch": self.branch,
            "models": self.models,
            "app": self.app,
            "start": iso(self.start),
            "end": iso(self.end),
            "api_calls": len(self.calls),
            "prompts": sum(1 for t in self.turns if t.prompt),
            "usage": self.usage.to_dict(),
            "file": str(self.path),
        }
        if with_turns:
            d["turns"] = [
                {
                    "prompt": t.prompt,
                    "timestamp": iso(t.timestamp),
                    "api_calls": len(t.calls),
                    "usage": t.usage.to_dict(),
                    "calls": [
                        {
                            "timestamp": iso(c.timestamp),
                            "model": c.model,
                            "app": c.app,
                            "subagent": c.subagent,
                            "usage": c.usage.to_dict(),
                        }
                        for c in t.calls
                    ],
                }
                for t in self.turns
            ]
        return d


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def parse_ts(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def usage_from(raw: dict) -> Usage:
    def n(key: str, src=raw) -> int:
        v = src.get(key) if isinstance(src, dict) else None
        return v if isinstance(v, int) else 0

    return Usage(
        input=n("input_tokens"),
        output=n("output_tokens"),
        cache_write=n("cache_creation_input_tokens"),
        cache_read=n("cache_read_input_tokens"),
        cache_write_1h=n("ephemeral_1h_input_tokens", raw.get("cache_creation")),
    )


def prompt_text(entry: dict) -> str | None:
    """Return the text of a real user prompt, or None for tool results etc."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        text = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        return None
    text = " ".join(text.split())
    # Slash-command / system wrappers are not interesting as titles.
    if not text or text.startswith("<command-") or text.startswith("<local-command"):
        return None
    return text


def read_jsonl(path: Path):
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def collect_calls(path: Path, subagent: bool, on_prompt=None, meta=None) -> list[ApiCall]:
    """Read one transcript file and return its API calls in order.

    Claude Code writes one line per content block, so the same API response
    (same message id) can appear several times with identical usage. We keep
    each response exactly once.
    """
    calls: dict[str, ApiCall] = {}
    order: list[str] = []
    app = ""
    for i, entry in enumerate(read_jsonl(path)):
        if entry.get("entrypoint"):
            app = app_label(entry["entrypoint"])
        if meta is not None:
            for key, src in (("cwd", "cwd"), ("branch", "gitBranch")):
                if not meta.get(key) and entry.get(src):
                    meta[key] = entry[src]
            if entry.get("type") in ("summary", "custom-title", "ai-title"):
                t = entry.get("summary") or entry.get("customTitle") or entry.get("aiTitle")
                if t:
                    meta["title"] = t
        if on_prompt is not None:
            text = prompt_text(entry)
            if text:
                on_prompt(text, parse_ts(entry.get("timestamp")), len(order))
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message") or {}
        raw = msg.get("usage")
        if not isinstance(raw, dict):
            continue
        key = msg.get("id") or entry.get("requestId") or entry.get("uuid") or f"line{i}"
        if key not in calls:
            order.append(key)
        calls[key] = ApiCall(
            timestamp=parse_ts(entry.get("timestamp")),
            model=msg.get("model") or "",
            usage=usage_from(raw),
            subagent=subagent or bool(entry.get("isSidechain")),
            app=app,
        )
    return [calls[k] for k in order]


APP_NAMES = {
    "cli": "Claude Code (terminal)",
    "remote": "Claude Code (web / cloud)",
    "claude-desktop": "Claude Desktop",
    "claude-vscode": "Claude Code (VS Code)",
    "claude-jetbrains": "Claude Code (JetBrains)",
    "sdk-cli": "Agent SDK",
    "sdk-ts": "Agent SDK (TypeScript)",
    "sdk-py": "Agent SDK (Python)",
    "mcp": "Claude Code (MCP)",
    "github-action": "Claude Code (GitHub Action)",
}


def app_label(entrypoint: str) -> str:
    return APP_NAMES.get(entrypoint, entrypoint)


def model_family(model: str) -> str:
    """'claude-opus-5-5' -> 'Opus'. Unknown ids are returned unchanged."""
    m = model.lower()
    for fam in ("fable", "opus", "sonnet", "haiku"):
        if fam in m:
            return fam.capitalize()
    if "gemini" in m or "gemma" in m:
        return "Gemini"
    if m.startswith(("gpt", "o1", "o3", "o4", "codex", "chatgpt")):
        return "GPT"
    if any(k in m for k in ("llama", "qwen", "mistral", "deepseek", "grok")):
        return next(k for k in ("llama", "qwen", "mistral", "deepseek", "grok") if k in m).capitalize()
    return model or "unknown"


@dataclass
class Group:
    """Token totals for one model / app / project / day."""

    usage: Usage = field(default_factory=Usage)
    calls: int = 0
    session_ids: set = field(default_factory=set)


def breakdown(sessions: list[Session], key) -> dict[str, Group]:
    """Group every API call by key(session, call); biggest total first."""
    groups: dict[str, Group] = {}
    for s in sessions:
        for call in s.calls:
            g = groups.setdefault(key(s, call), Group())
            g.usage.add(call.usage)
            g.calls += 1
            g.session_ids.add((s.tool, s.session_id))
    return dict(sorted(groups.items(), key=lambda kv: kv[1].usage.total, reverse=True))


BY_TOOL = lambda s, c: s.tool  # noqa: E731
BY_MODEL = lambda s, c: c.model or "unknown"  # noqa: E731
BY_APP = lambda s, c: c.app or "unknown"  # noqa: E731
BY_PROJECT = lambda s, c: s.project  # noqa: E731


def by_day(sessions: list[Session]) -> dict[str, dict[str, Usage]]:
    """{'2026-09-30': {'Opus': Usage, ...}} using local dates."""
    days: dict[str, dict[str, Usage]] = {}
    for s in sessions:
        for call in s.calls:
            if not call.timestamp:
                continue
            day = call.timestamp.astimezone().strftime("%Y-%m-%d")
            days.setdefault(day, {}).setdefault(model_family(call.model), Usage()).add(call.usage)
    return days


def load_session(path: Path, project: str) -> Session:
    session = Session(session_id=path.stem, project=project, path=path)
    meta: dict = {}
    prompts: list[tuple[str, datetime | None, int]] = []  # (text, ts, index of first call)

    calls = collect_calls(
        path,
        subagent=False,
        on_prompt=lambda text, ts, idx: prompts.append((text, ts, idx)),
        meta=meta,
    )

    # Split the calls into turns, one per user prompt.
    if not prompts or prompts[0][2] > 0:
        prompts.insert(0, ("", None, 0))
    for n, (text, ts, start) in enumerate(prompts):
        stop = prompts[n + 1][2] if n + 1 < len(prompts) else len(calls)
        turn = Turn(prompt=text, timestamp=ts, calls=calls[start:stop])
        if turn.prompt or turn.calls:
            session.turns.append(turn)

    # Sub-agent transcripts live next to the session: <session-id>/**/*.jsonl
    sub_dir = path.with_suffix("")
    if sub_dir.is_dir():
        for sub in sorted(sub_dir.rglob("*.jsonl")):
            sub_calls = collect_calls(sub, subagent=True)
            for call in sub_calls:
                call.app = call.app or (calls[0].app if calls else "")
            if sub_calls:
                label = f"[sub-agent] {sub.stem}"
                first = next((c.timestamp for c in sub_calls if c.timestamp), None)
                session.turns.append(Turn(prompt=label, timestamp=first, calls=sub_calls))

    session.cwd = meta.get("cwd") or ""
    session.branch = meta.get("branch") or ""
    first_prompt = next((t.prompt for t in session.turns if t.prompt and not t.prompt.startswith("[sub-agent]")), "")
    session.title = meta.get("title") or first_prompt or "(no prompt)"
    return session


def default_roots() -> list[Path]:
    roots = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        for part in env.split(","):
            roots.append(Path(part).expanduser() / "projects")
    home = Path.home()
    roots += [home / ".claude" / "projects", home / ".config" / "claude" / "projects"]
    out, seen = [], set()
    for r in roots:
        key = str(r.resolve()) if r.exists() else str(r)
        if key not in seen and r.is_dir():
            seen.add(key)
            out.append(r)
    return out


def pretty_project(dirname: str) -> str:
    """'-home-me-code-my-app' -> 'my-app' (best effort, dashes are ambiguous)."""
    parts = [p for p in dirname.split("-") if p]
    return parts[-1] if parts else dirname


def load_sessions(roots: list[Path] | None = None, project_filter: str | None = None,
                  tool_filter: str | None = None) -> list[Session]:
    """Every session from every supported AI tool, newest first.

    `roots` overrides where Claude Code transcripts are read from; the other
    tools are always read from their default locations.
    """
    from .sources import load_other_tools

    from .pricing import apply_costs

    sessions = load_claude_sessions(default_roots() if roots is None else roots) + load_other_tools()
    apply_costs(sessions)
    if project_filter:
        needle = project_filter.lower()
        sessions = [s for s in sessions if needle in f"{s.project} {s.cwd} {s.path}".lower()]
    if tool_filter:
        needle = tool_filter.lower()
        sessions = [s for s in sessions if needle in s.tool.lower()]
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    sessions.sort(key=lambda s: s.end or epoch, reverse=True)
    return sessions


def load_claude_sessions(roots: list[Path]) -> list[Session]:
    sessions: list[Session] = []
    for root in roots:
        if not root.is_dir():
            continue  # a folder that doesn't exist (yet) just has no sessions
        for proj in sorted(p for p in root.iterdir() if p.is_dir()):
            for path in sorted(proj.glob("*.jsonl")):
                s = load_session(path, proj.name)
                if s.cwd:
                    s.project = Path(s.cwd).name or s.project
                else:
                    s.project = pretty_project(proj.name)
                if s.calls:
                    sessions.append(s)
    return sessions



# --------------------------------------------------------------------------- #
# Formatting helpers (shared by the CLI and the GUI)
# --------------------------------------------------------------------------- #

def fmt(n: int) -> str:
    return f"{n:,}"


def short(n: int) -> str:
    for unit, size in (("B", 1_000_000_000), ("M", 1_000_000), ("k", 1_000)):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return str(n)


def local(ts: datetime | None, with_date: bool = True) -> str:
    if not ts:
        return "-"
    ts = ts.astimezone()
    return ts.strftime("%Y-%m-%d %H:%M" if with_date else "%H:%M:%S")


def duration(s: Session) -> str:
    if not s.start or not s.end:
        return "-"
    secs = int((s.end - s.start).total_seconds())
    h, rem = divmod(secs, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def clip(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


