"""What the tokens produced: git commits made during each AI session.

For every project folder an AI session ran in, we ask git (on this computer only) for
your own commits in that time. A commit counts for a session when it was made while
the session was active (or within 15 minutes after a reply), and only when exactly
one session fits, so a commit is never counted twice or guessed at.

Only commit times, short hashes and subject lines are read; no code or diffs.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

GRACE = timedelta(minutes=15)
CACHE_SECONDS = 300
_cache: dict = {}  # repo -> (fetched_at, since, commits)
_roots: dict = {}  # folder -> repo root (or None)


def _git(args: list[str], cwd: str, timeout: float = 10) -> str | None:
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW: no console flash
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=timeout, creationflags=flags,
                             env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def repo_root(cwd: str) -> str | None:
    if not cwd or not Path(cwd).is_dir():
        return None
    out = _git(["rev-parse", "--show-toplevel"], cwd)
    return out.strip() if out and out.strip() else None


def _commits(root: str, since: datetime) -> list[dict]:
    """Your own non-merge commits in this repo since `since` (any branch)."""
    cached = _cache.get(root)
    if cached and time.time() - cached[0] < CACHE_SECONDS and cached[1] <= since:
        return [c for c in cached[2] if c["at"] >= since]
    email = (_git(["config", "user.email"], root) or "").strip()
    args = ["log", "--all", "--no-merges", f"--since={since.isoformat()}", "--format=%h%x1f%aI%x1f%s", "-n", "5000"]
    if email:
        args.append(f"--author={email}")
    out = _git(args, root, timeout=20)
    commits = []
    for line in (out or "").splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3:
            continue
        try:
            at = datetime.fromisoformat(parts[1].replace("Z", "+00:00"))
        except ValueError:
            continue
        commits.append({"hash": parts[0], "at": at, "subject": parts[2][:200]})
    _cache[root] = (time.time(), since, commits)
    return commits


def active_windows(times: list[datetime], gap: timedelta = timedelta(minutes=30)) -> list[tuple[datetime, datetime]]:
    """Stretches of activity: replies less than `gap` apart. A session resumed days later
    is two stretches, so the days in between don't collect commits."""
    out: list[list[datetime]] = []
    for t in sorted(times):
        if out and t - out[-1][1] <= gap:
            out[-1][1] = t
        else:
            out.append([t, t])
    return [(a, b) for a, b in out]


def link_commits(sessions) -> dict[int, list[dict]]:
    """{index in `sessions`: [commit, ...]} for sessions that clearly produced commits."""
    by_repo: dict[str, list[int]] = {}
    roots: dict[str, str | None] = {}
    for i, s in enumerate(sessions):
        if not s.cwd or not s.calls:
            continue
        if s.cwd not in roots:
            if s.cwd not in _roots:
                _roots[s.cwd] = repo_root(s.cwd)
            roots[s.cwd] = _roots[s.cwd]
        if roots[s.cwd]:
            by_repo.setdefault(roots[s.cwd], []).append(i)
    linked: dict[int, list[dict]] = {}
    for root, idxs in by_repo.items():
        spans = []  # (session index, start, end) for each stretch of activity
        for i in idxs:
            for start, end in active_windows([c.timestamp for c in sessions[i].calls if c.timestamp]):
                spans.append((i, start, end + GRACE))
        if not spans:
            continue
        year_ago = datetime.now().astimezone() - timedelta(days=365)
        for commit in _commits(root, max(min(start for _, start, _ in spans), year_ago)):
            owners = {i for i, start, end in spans if start <= commit["at"] <= end}
            if len(owners) == 1:
                linked.setdefault(owners.pop(), []).append(commit)
    return linked
