"""Command-line interface: `ai-tokens`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .core import (
    BY_APP, BY_MODEL, BY_PROJECT, BY_TOOL, Session, Usage, breakdown, clip, duration, fmt,
    load_sessions, local, short,
)
from .pricing import pricing_info
from .sources import custom_dir, log_usage, source_status

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


def money(v: float) -> str:
    if not v:
        return "$0"
    return "<$0.01" if v < 0.01 else f"${v:,.2f}"


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
    lines.append(f"  {'Est. cost'.ljust(12)}{money(u.cost).rjust(14)}   {dim('at API list prices' + (f'; {fmt(u.unpriced)} tokens have no known price' if u.unpriced else ''))}")
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
            short(u.input + u.cache_write), short(u.cache_read), short(u.output), short(u.total), money(u.cost),
            clip(s.title, title_width),
        ])
    return table(
        ["#", "AI", "ID", "Started", "Project", "Calls", "In+Write", "CacheRead", "Output", "Total", "Cost", "First prompt"],
        rows, right={0, 5, 6, 7, 8, 9, 10},
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
    print(dim("More: --all  |  -s <id> for a session's per-prompt breakdown  |  --stats  |  ai-tokens-gui for the dashboard"))


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
                         fmt(u.total), money(u.cost), f"{share:.1f}%"])
        print(bold(title))
        print(table(["Name", "Sessions", "Calls", "In+Write", "CacheRead", "Output", "Total", "Est. cost", "Share"],
                    rows, right={1, 2, 3, 4, 5, 6, 7, 8}))
        print()


def find_session(sessions: list[Session], ref: str) -> Session | None:
    if ref in ("latest", "last", "0"):
        return sessions[0]
    if ref.isdigit() and 1 <= int(ref) <= len(sessions):
        return sessions[int(ref) - 1]
    matches = [s for s in sessions if s.session_id.startswith(ref)]
    return matches[0] if len(matches) == 1 else None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _prepare_console() -> None:
    """Windows consoles may not print ▶ / → or ANSI colours by default."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    if os.name == "nt" and USE_COLOR:
        os.system("")  # switches on ANSI colour handling in the classic Windows console


def main(argv: list[str] | None = None) -> int:
    _prepare_console()
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
    ap.add_argument("--serve", action="store_true", help="run the dashboard in your browser (no app window)")
    ap.add_argument("--port", type=int, default=None, help="with --serve: port to use (default 47690, or any free port)")
    ap.add_argument("--no-open", action="store_true", help="with --serve: don't open the browser")
    ap.add_argument("--sources", action="store_true", help="show which AI tools were found and where")
    ap.add_argument("--update-prices", action="store_true",
                    help="download the latest model prices (LiteLLM's public list) for cost estimates")
    args = ap.parse_args(argv)

    if args.update_prices:
        from .pricing import update_prices
        try:
            n = update_prices()
        except Exception as exc:
            print(f"Could not update prices: {exc}", file=sys.stderr)
            return 1
        print(f"Updated prices for {n:,} models.")
        return 0

    if args.sources:
        for row in source_status([d.expanduser() for d in args.dir] if args.dir else None):
            state = f"{row['files']:,} files" if row["found"] else "not found"
            print(f"{bold(row['tool']):<24} {state}")
            for p in row["paths"]:
                print(f"    {dim(p)}")
            if row.get("also_checked"):
                more = row["also_checked"]
                print(f"    {dim(f'(and {more} more places)')}")
        info = pricing_info()
        print(f"{bold('Prices'):<24} {info['models']:,} models ({info['source']}, {info['updated']}), "
              f"{info['overrides']} of your own")
        return 0

    if args.serve:
        from .gui import serve_in_browser
        from .server import start
        httpd, app, url = start(args.port, [d.expanduser() for d in args.dir] if args.dir else None)
        return serve_in_browser(httpd, app, url, open_browser=not args.no_open, idle_exit=False)

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
