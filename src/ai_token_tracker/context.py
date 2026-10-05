"""Context size check: instruction files your AI tools add to every message.

Files like CLAUDE.md, AGENTS.md or GEMINI.md are sent along with every request, so a
long one costs tokens on every single message. This lists the ones on this computer
(your global ones, and those in projects you used recently) with an estimated token
count. Only sizes are reported; file contents never leave this module.
"""

from __future__ import annotations

import os
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024

# (file inside a project, which AI reads it)
PROJECT_FILES = (
    ("CLAUDE.md", "Claude Code"), ("CLAUDE.local.md", "Claude Code"), (".claude/CLAUDE.md", "Claude Code"),
    ("AGENTS.md", "Codex, OpenCode and others"), ("GEMINI.md", "Gemini CLI"), ("QWEN.md", "Qwen Code"),
    (".cursorrules", "Cursor"), (".windsurfrules", "Windsurf"), (".clinerules", "Cline"),
    (".github/copilot-instructions.md", "GitHub Copilot"), (".kiro/steering", "Kiro"), (".cursor/rules", "Cursor"),
)


def _global_files() -> list[tuple[Path, str]]:
    home = Path.home()
    claude = Path((os.environ.get("CLAUDE_CONFIG_DIR") or "").split(",")[0].strip() or home / ".claude").expanduser()
    codex = Path(os.environ.get("CODEX_HOME") or home / ".codex").expanduser()
    return [(claude / "CLAUDE.md", "Claude Code"), (codex / "AGENTS.md", "Codex"),
            (home / ".gemini" / "GEMINI.md", "Gemini CLI"), (home / ".qwen" / "QWEN.md", "Qwen Code"),
            (home / ".config" / "opencode" / "AGENTS.md", "OpenCode")]


def estimate_tokens(text: str) -> int:
    """About 4 characters per token for English and code; CJK characters are about one each."""
    wide = sum(1 for ch in text if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" or "가" <= ch <= "힯")
    return wide + (len(text) - wide + 3) // 4


def _measure(path: Path) -> list[tuple[str, int]]:
    """[(name, tokens)] for a file, or for each rule file in a folder."""
    files = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix in (".md", ".mdc", ".txt", "")) \
        if path.is_dir() else [path] if path.is_file() else []
    out = []
    for f in files[:200]:
        try:
            if f.stat().st_size > MAX_BYTES:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.append((str(f), estimate_tokens(text)))
    return out


def _skills_overhead() -> list[dict]:
    """Claude Code lists every installed skill's name and description in each request."""
    home = Path.home()
    claude = Path((os.environ.get("CLAUDE_CONFIG_DIR") or "").split(",")[0].strip() or home / ".claude").expanduser()
    total, count = 0, 0
    for skill in sorted((claude / "skills").glob("*/SKILL.md"))[:500]:
        try:
            head = skill.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        if head.startswith("---"):
            front = head.split("---", 2)[1] if head.count("---") >= 2 else ""
            total += estimate_tokens(front)
            count += 1
    return [{"path": str(claude / "skills"), "tokens": total, "label": f"{count} installed skills (names and descriptions)",
             "tool": "Claude Code"}] if count else []


def report(cwds: list[str]) -> dict:
    """{global: [...], projects: [{project, cwd, files, tokens}]} sorted by size."""
    glob = []
    for path, tool in _global_files():
        for name, tokens in _measure(path):
            glob.append({"path": name, "tokens": tokens, "tool": tool, "label": Path(name).name})
    glob += _skills_overhead()
    projects = []
    for cwd in dict.fromkeys(c for c in cwds if c):
        root = Path(cwd)
        if not root.is_dir():
            continue
        files = []
        for rel, tool in PROJECT_FILES:
            for name, tokens in _measure(root / rel):
                files.append({"path": name, "tokens": tokens, "tool": tool,
                              "label": os.path.relpath(name, root) if name.startswith(str(root)) else name})
        if files:
            projects.append({"project": root.name or cwd, "cwd": cwd, "files": files,
                             "tokens": sum(f["tokens"] for f in files)})
    projects.sort(key=lambda p: -p["tokens"])
    return {"global": glob, "global_tokens": sum(g["tokens"] for g in glob), "projects": projects[:50]}
