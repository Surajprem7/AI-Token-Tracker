"""Command-line interface: `ai-tokens`."""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .core import (
    BY_APP, BY_MODEL, BY_PROJECT, BY_TOOL, Session, Usage, breakdown, clip, duration, fmt,
    load_sessions, local, short,
)
from .sources import custom_dir, log_usage

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
        ("Output", u.output, "tokens the AI wrote (incl. thinking)"),
        ("Cache write", u.cache_write, "context stored in the prompt cache"),
        ("Cache read", u.cache_read, "context re-read from cache (cheap)"),
    ]
    lines = [f"  {name:<12}{fmt(v):>14}   {dim(note)}" for name, v, note in rows]
    lines.append(f"  {bold('Total'.ljust(12))}{bold(fmt(u.total).rjust(14))}")
    return "\n".join(lines)


def session_header(s: Session) -> str:
    return "\n".join([
        f"{bold(s.tool)}  {s.session_id}   {dim(s.project)}" + (dim(f" @ {s.branch}") if s.branch else ""),
        f"  {clip(s.title, 100)}",
        f"  {local(s.start)} → {local(s.end, with_date=False)}  ({duration(s)})   "
        f"models: {', '.join(s.models) or '-'}   API calls: {len(s.calls)}",
    ])


def session_rows(sessions: list[Session], title_width: int = 48) -> str:
    rows = []
    for i, s in enumerate(sessions, 1):
        u = s.usage
        rows.append([
            str(i), clip(s.tool, 14), s.session_id[:8], local(s.start), clip(s.project, 20), str(len(s.calls)),
            short(u.input + u.cache_write), short(u.cache_read), short(u.output), short(u.total),
            clip(s.title, title_width),
        ])
    return table(
        ["#", "AI", "ID", "Started", "Project", "Calls", "In+Write", "CacheRead", "Output", "Total", "First prompt"],
        rows, right={0, 5, 6, 7, 8, 9},
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


def print_stats(sessions: list[Session]) -> None:
    grand = Usage()
    for s in sessions:
        grand.add(s.usage)
    for title, key in (("By AI tool", BY_TOOL), ("By engine (model)", BY_MODEL), ("By AI app", BY_APP), ("By project", BY_PROJECT)):
        rows = []
        for name, g in breakdown(sessions, key).items():
            u = g.usage
            share = u.total / grand.total * 100 if grand.total else 0
            rows.append([clip(name, 32), str(len(g.session_ids)), str(g.calls),
                         short(u.input + u.cache_write), short(u.cache_read), short(u.output),
                         fmt(u.total), f"{share:.1f}%"])
        print(bold(title))
        print(table(["Name", "Sessions", "Calls", "In+Write", "CacheRead", "Output", "Total", "Share"],
                    rows, right={1, 2, 3, 4, 5, 6, 7}))
        print()


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
<title>AI Token Usage</title>
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
<h1>AI token usage</h1>
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
    try:
        return _main(argv)
    except BrokenPipeError:  # output piped into `head` etc.
        return 0


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="ai-tokens",
        description="Show how many tokens each AI session spent "
                    "(Claude Code, Codex CLI, Gemini CLI and anything in your custom log).",
    )
    ap.add_argument("-a", "--all", action="store_true", help="list every session")
    ap.add_argument("-s", "--session", metavar="ID",
                    help="expanded view of one session: id prefix, list number, or 'latest'")
    ap.add_argument("--calls", action="store_true", help="with -s: also list every API call")
    ap.add_argument("-n", "--recent", type=int, default=10, help="sessions in the overview list (default 10)")
    ap.add_argument("-p", "--project", help="only sessions whose project/path contains this text")
    ap.add_argument("--dir", action="append", type=Path,
                    help="Claude Code 'projects' directory to scan (default: ~/.claude/projects)")
    ap.add_argument("-t", "--tool", help="only sessions from this AI tool, e.g. codex, gemini, claude")
    ap.add_argument("--add", nargs=4, metavar=("TOOL", "MODEL", "INPUT", "OUTPUT"),
                    help="log usage from any other AI by hand, e.g. --add ChatGPT gpt-5 1200 350")
    ap.add_argument("--prompt", default="", help="with --add: what the request was about")
    ap.add_argument("--stats", action="store_true", help="totals per AI tool, engine (model), app and project")
    ap.add_argument("--json", action="store_true", help="print JSON instead of tables")
    ap.add_argument("--html", metavar="FILE", type=Path, help="write an expandable HTML report")
    args = ap.parse_args(argv)

    if args.add:
        tool, model, inp, out = args.add
        try:
            path = log_usage(tool, model, int(inp), int(out), prompt=args.prompt)
        except ValueError:
            print("INPUT and OUTPUT must be whole numbers of tokens.", file=sys.stderr)
            return 2
        print(f"Logged {int(inp) + int(out):,} tokens for {tool} ({model}) in {path}")
        return 0

    roots = [d.expanduser() for d in args.dir] if args.dir else None
    sessions = load_sessions(roots, args.project, args.tool)
    if not sessions:
        print("No sessions with token usage found. Looked for Claude Code (~/.claude/projects), "
              f"Codex CLI (~/.codex/sessions), Gemini CLI (~/.gemini/tmp) and {custom_dir()}.",
              file=sys.stderr)
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

    if args.stats:
        print_stats(sessions)
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
