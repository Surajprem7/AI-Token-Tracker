#!/usr/bin/env python3
"""AI Token Tracker - see how many tokens each Claude Code session spent.

Claude Code writes a transcript for every session to
~/.claude/projects/<project>/<session-id>.jsonl. Every assistant message in
those files carries the API `usage` block, so we can add up exactly what each
session consumed without any API key or network access.

Usage:
    python3 token_tracker.py                 # latest session + short list of recent ones
    python3 token_tracker.py --all           # every session, one row each
    python3 token_tracker.py -s <id|latest>  # expanded view of one session (per prompt)
    python3 token_tracker.py -s latest --calls   # ...and every single API call
    python3 token_tracker.py --html report.html  # expandable HTML report of all sessions
    python3 token_tracker.py --json          # machine-readable output

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
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

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_write + self.cache_read

    def add(self, other: "Usage") -> None:
        for f in TOKEN_FIELDS:
            setattr(self, f, getattr(self, f) + getattr(other, f))

    def to_dict(self) -> dict:
        d = {f: getattr(self, f) for f in TOKEN_FIELDS}
        d["total"] = self.total
        return d


@dataclass
class ApiCall:
    timestamp: datetime | None
    model: str
    usage: Usage
    subagent: bool = False


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
            "project": self.project,
            "title": self.title,
            "cwd": self.cwd,
            "git_branch": self.branch,
            "models": self.models,
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
    def n(key: str) -> int:
        v = raw.get(key)
        return v if isinstance(v, int) else 0

    return Usage(
        input=n("input_tokens"),
        output=n("output_tokens"),
        cache_write=n("cache_creation_input_tokens"),
        cache_read=n("cache_read_input_tokens"),
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
    for i, entry in enumerate(read_jsonl(path)):
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
        )
    return [calls[k] for k in order]


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


def load_sessions(roots: list[Path], project_filter: str | None = None) -> list[Session]:
    sessions: list[Session] = []
    for root in roots:
        for proj in sorted(p for p in root.iterdir() if p.is_dir()):
            for path in sorted(proj.glob("*.jsonl")):
                s = load_session(path, proj.name)
                if s.cwd:
                    s.project = Path(s.cwd).name or s.project
                else:
                    s.project = pretty_project(proj.name)
                if project_filter and project_filter.lower() not in (s.project + " " + proj.name + " " + s.cwd).lower():
                    continue
                if s.calls:
                    sessions.append(s)
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    sessions.sort(key=lambda s: s.end or epoch, reverse=True)
    return sessions


# --------------------------------------------------------------------------- #
# Terminal output
# --------------------------------------------------------------------------- #

USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def bold(t: str) -> str:
    return c(t, "1")


def dim(t: str) -> str:
    return c(t, "2")


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


def table(headers: list[str], rows: list[list[str]], right: set[int]) -> str:
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))

    def line(cells, style=None):
        out = []
        for i, cell in enumerate(cells):
            out.append(cell.rjust(widths[i]) if i in right else cell.ljust(widths[i]))
        s = "  ".join(out).rstrip()
        return style(s) if style else s

    return "\n".join([line(headers, bold)] + [line(r) for r in rows])


def usage_block(u: Usage) -> str:
    rows = [
        ("Input", u.input, "fresh prompt tokens"),
        ("Output", u.output, "tokens Claude wrote (incl. thinking)"),
        ("Cache write", u.cache_write, "context stored in the prompt cache"),
        ("Cache read", u.cache_read, "context re-read from cache (cheap)"),
    ]
    lines = [f"  {name:<12}{fmt(v):>14}   {dim(note)}" for name, v, note in rows]
    lines.append(f"  {bold('Total'.ljust(12))}{bold(fmt(u.total).rjust(14))}")
    return "\n".join(lines)


def session_header(s: Session) -> str:
    return "\n".join([
        f"{bold('Session')} {s.session_id}   {dim(s.project)}" + (dim(f" @ {s.branch}") if s.branch else ""),
        f"  {clip(s.title, 100)}",
        f"  {local(s.start)} → {local(s.end, with_date=False)}  ({duration(s)})   "
        f"models: {', '.join(s.models) or '-'}   API calls: {len(s.calls)}",
    ])


def session_rows(sessions: list[Session], title_width: int = 48) -> str:
    rows = []
    for i, s in enumerate(sessions, 1):
        u = s.usage
        rows.append([
            str(i), s.session_id[:8], local(s.start), clip(s.project, 20), str(len(s.calls)),
            short(u.input + u.cache_write), short(u.cache_read), short(u.output), short(u.total),
            clip(s.title, title_width),
        ])
    return table(
        ["#", "ID", "Started", "Project", "Calls", "In+Write", "CacheRead", "Output", "Total", "First prompt"],
        rows, right={0, 4, 5, 6, 7, 8},
    )


def print_overview(sessions: list[Session], recent: int) -> None:
    latest = sessions[0]
    print(bold("▶ Latest session"))
    print(session_header(latest))
    print(usage_block(latest.usage))
    print()
    shown = sessions[:recent]
    print(bold(f"Recent sessions ({len(shown)} of {len(sessions)})"))
    print(session_rows(shown))
    grand = Usage()
    for s in sessions:
        grand.add(s.usage)
    print()
    print(f"All {len(sessions)} sessions: {bold(fmt(grand.total))} tokens "
          f"({fmt(grand.output)} output)")
    print(dim("More: --all  |  -s <id> for a session's per-prompt breakdown  |  --html report.html"))


def print_all(sessions: list[Session]) -> None:
    print(session_rows(sessions, title_width=60))
    grand = Usage()
    for s in sessions:
        grand.add(s.usage)
    print()
    print(bold("Grand total"))
    print(usage_block(grand))


def print_session(s: Session, show_calls: bool) -> None:
    print(session_header(s))
    print(usage_block(s.usage))
    print()
    print(bold("Per prompt"))
    rows = []
    for i, t in enumerate(s.turns, 1):
        u = t.usage
        rows.append([
            str(i), local(t.timestamp, with_date=False), str(len(t.calls)),
            fmt(u.input + u.cache_write), fmt(u.cache_read), fmt(u.output), fmt(u.total),
            clip(t.prompt or "(before first prompt)", 60),
        ])
    print(table(["#", "Time", "Calls", "In+Write", "CacheRead", "Output", "Total", "Prompt"],
                rows, right={0, 2, 3, 4, 5, 6}))
    if show_calls:
        print()
        print(bold("Every API call"))
        rows = []
        n = 0
        for ti, t in enumerate(s.turns, 1):
            for call in t.calls:
                n += 1
                u = call.usage
                rows.append([
                    str(n), str(ti), local(call.timestamp, with_date=False),
                    call.model + (" (sub)" if call.subagent else ""),
                    fmt(u.input), fmt(u.cache_write), fmt(u.cache_read), fmt(u.output), fmt(u.total),
                ])
        print(table(["#", "Prompt", "Time", "Model", "Input", "CacheWrite", "CacheRead", "Output", "Total"],
                    rows, right={0, 1, 4, 5, 6, 7, 8}))


def find_session(sessions: list[Session], ref: str) -> Session | None:
    if ref in ("latest", "last", "0"):
        return sessions[0]
    if ref.isdigit() and int(ref) <= len(sessions):
        return sessions[int(ref) - 1]
    matches = [s for s in sessions if s.session_id.startswith(ref)]
    return matches[0] if len(matches) == 1 else None


# --------------------------------------------------------------------------- #
# HTML report
# --------------------------------------------------------------------------- #

HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Token Usage</title>
<style>
:root {{
  --bg: #f7f7f5; --card: #ffffff; --text: #1d1d1b; --muted: #6b6b66; --line: #e4e3de;
  --accent: #c2410c; --bar-in: #2563eb; --bar-cw: #7c3aed; --bar-cr: #94a3b8; --bar-out: #c2410c;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #161614; --card: #1f1f1c; --text: #ecebe6; --muted: #9a998f; --line: #34332f;
    --accent: #fb923c; --bar-in: #60a5fa; --bar-cw: #a78bfa; --bar-cr: #64748b; --bar-out: #fb923c;
  }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1100px; margin: 0 auto; padding: 24px 16px 64px; }}
h1 {{ font-size: 22px; margin: 0 0 4px; }}
.sub {{ color: var(--muted); margin-bottom: 20px; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 24px; }}
.tile {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }}
.tile .k {{ color: var(--muted); font-size: 12px; }}
.tile .v {{ font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }}
details {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px; margin-bottom: 8px; }}
details[open] {{ border-color: var(--accent); }}
summary {{ cursor: pointer; list-style: none; padding: 12px 14px; display: grid;
  grid-template-columns: 1fr auto; gap: 4px 16px; align-items: center; }}
summary::-webkit-details-marker {{ display: none; }}
summary .title {{ font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
summary .title::before {{ content: "▸ "; color: var(--muted); }}
details[open] summary .title::before {{ content: "▾ "; }}
summary .total {{ font-weight: 600; font-variant-numeric: tabular-nums; text-align: right; }}
summary .meta {{ color: var(--muted); font-size: 12px; }}
.bar {{ display: flex; height: 6px; border-radius: 3px; overflow: hidden; background: var(--line); grid-column: 1 / -1; }}
.bar span {{ display: block; height: 100%; }}
.in {{ background: var(--bar-in); }} .cw {{ background: var(--bar-cw); }}
.cr {{ background: var(--bar-cr); }} .out {{ background: var(--bar-out); }}
.body {{ padding: 0 14px 14px; overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }}
th, td {{ padding: 6px 8px; border-bottom: 1px solid var(--line); text-align: right; white-space: nowrap; }}
th {{ color: var(--muted); font-weight: 500; font-size: 12px; }}
td.p, th.p {{ text-align: left; white-space: normal; min-width: 240px; }}
tr.sub td {{ color: var(--muted); }}
.legend {{ display: flex; gap: 14px; flex-wrap: wrap; color: var(--muted); font-size: 12px; margin: -8px 0 16px; }}
.legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }}
</style>
</head>
<body>
<main>
<h1>Claude token usage</h1>
<div class="sub">{count} sessions · generated {generated} · click a session to expand it</div>
<div class="tiles">{tiles}</div>
<div class="legend"><span><i class="in"></i>Input</span><span><i class="cw"></i>Cache write</span>
<span><i class="cr"></i>Cache read</span><span><i class="out"></i>Output</span></div>
{sessions}
</main>
</body>
</html>
"""


def bar(u: Usage) -> str:
    total = u.total or 1
    parts = [("in", u.input), ("cw", u.cache_write), ("cr", u.cache_read), ("out", u.output)]
    return '<div class="bar">' + "".join(
        f'<span class="{cls}" style="width:{v / total * 100:.2f}%"></span>' for cls, v in parts if v
    ) + "</div>"


def render_html(sessions: list[Session]) -> str:
    esc = html.escape
    grand = Usage()
    for s in sessions:
        grand.add(s.usage)
    tiles = "".join(
        f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div></div>'
        for k, v in [
            ("Total tokens", fmt(grand.total)), ("Output", fmt(grand.output)),
            ("Input + cache write", fmt(grand.input + grand.cache_write)),
            ("Cache read", fmt(grand.cache_read)), ("Sessions", str(len(sessions))),
        ]
    )
    blocks = []
    for idx, s in enumerate(sessions):
        u = s.usage
        rows = []
        for i, t in enumerate(s.turns, 1):
            tu = t.usage
            cls = ' class="sub"' if t.prompt.startswith("[sub-agent]") else ""
            rows.append(
                f"<tr{cls}><td>{i}</td><td>{esc(local(t.timestamp, with_date=False))}</td>"
                f"<td>{len(t.calls)}</td><td>{fmt(tu.input)}</td><td>{fmt(tu.cache_write)}</td>"
                f"<td>{fmt(tu.cache_read)}</td><td>{fmt(tu.output)}</td><td><b>{fmt(tu.total)}</b></td>"
                f'<td class="p">{esc(clip(t.prompt or "(before first prompt)", 300))}</td></tr>'
            )
        meta = " · ".join(filter(None, [
            s.project, s.branch, f"{local(s.start)} ({duration(s)})", ", ".join(s.models),
            f"{len(s.calls)} API calls", s.session_id,
        ]))
        blocks.append(
            f'<details{" open" if idx == 0 else ""}><summary>'
            f'<span class="title">{esc(clip(s.title, 140))}</span>'
            f'<span class="total">{fmt(u.total)}</span>'
            f'<span class="meta">{esc(meta)}</span>'
            f'<span class="meta" style="text-align:right">out {fmt(u.output)}</span>'
            f"{bar(u)}</summary><div class=\"body\"><table><thead><tr>"
            f"<th>#</th><th>Time</th><th>Calls</th><th>Input</th><th>Cache write</th>"
            f'<th>Cache read</th><th>Output</th><th>Total</th><th class="p">Prompt</th>'
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div></details>"
        )
    return HTML_TEMPLATE.format(
        count=len(sessions),
        generated=esc(datetime.now().strftime("%Y-%m-%d %H:%M")),
        tiles=tiles,
        sessions="\n".join(blocks),
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Show how many tokens each Claude Code session spent.",
    )
    ap.add_argument("-a", "--all", action="store_true", help="list every session")
    ap.add_argument("-s", "--session", metavar="ID",
                    help="expanded view of one session: id prefix, list number, or 'latest'")
    ap.add_argument("--calls", action="store_true", help="with -s: also list every API call")
    ap.add_argument("-n", "--recent", type=int, default=10, help="sessions in the overview list (default 10)")
    ap.add_argument("-p", "--project", help="only sessions whose project/path contains this text")
    ap.add_argument("--dir", action="append", type=Path,
                    help="Claude 'projects' directory to scan (default: ~/.claude/projects)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of tables")
    ap.add_argument("--html", metavar="FILE", type=Path, help="write an expandable HTML report")
    args = ap.parse_args(argv)

    roots = [d.expanduser() for d in args.dir] if args.dir else default_roots()
    if not roots:
        print("No Claude transcripts found (looked in ~/.claude/projects). "
              "Use --dir to point at a 'projects' folder.", file=sys.stderr)
        return 1

    sessions = load_sessions(roots, args.project)
    if not sessions:
        print("No sessions with token usage found in: " + ", ".join(map(str, roots)), file=sys.stderr)
        return 1

    if args.html:
        args.html.write_text(render_html(sessions), encoding="utf-8")
        print(f"Wrote {args.html} ({len(sessions)} sessions)")
        return 0

    if args.session:
        s = find_session(sessions, args.session)
        if not s:
            print(f"No unique session matches '{args.session}'.", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(s.to_dict(), indent=2))
        else:
            print_session(s, args.calls)
        return 0

    if args.json:
        chosen = sessions if args.all else sessions[: args.recent]
        print(json.dumps([s.to_dict(with_turns=False) for s in chosen], indent=2))
    elif args.all:
        print_all(sessions)
    else:
        print_overview(sessions, args.recent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
