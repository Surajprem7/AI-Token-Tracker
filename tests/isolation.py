"""Run tests against an empty, private home folder.

The readers look in many places under the home folder (Claude Code, Codex, Gemini,
Qwen, OpenCode, Cline, VS Code extensions...). Without this, a developer's real
AI tool data would leak into the tests and make them fail.
"""

import os
from pathlib import Path
from unittest import mock

TOOL_VARS = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GEMINI_CLI_HOME", "QWEN_HOME", "CLINE_DIR", "CLINE_DATA_DIR",
             "CLINE_SESSION_DATA_DIR", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "APPDATA", "AI_TOKEN_TRACKER_DIR")


def isolate(test, tmp: Path, **overrides: str) -> None:
    """Point HOME and every tool location at `tmp` until the test (or test class) ends."""
    home = tmp / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in TOOL_VARS}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "AppData" / "Roaming"),
               XDG_CONFIG_HOME=str(home / ".config"), XDG_DATA_HOME=str(home / ".local" / "share"))
    env.update(overrides)
    patcher = mock.patch.dict(os.environ, env, clear=True)
    patcher.start()
    # works for a test (addCleanup) and, from setUpClass, for a whole class (addClassCleanup)
    (test.addClassCleanup if isinstance(test, type) else test.addCleanup)(patcher.stop)
