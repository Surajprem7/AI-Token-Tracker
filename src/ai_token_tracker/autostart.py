"""Start with the computer: open the small widget when you log in.

Windows: a value under HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run (your account only).
macOS:   ~/Library/LaunchAgents/io.github.surajprem7.aitokentracker.plist
Linux:   ~/.config/autostart/ai-token-tracker.desktop

Only the installed app (or the `ai-tokens-gui` command) can be started this way.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import sys
from pathlib import Path

NAME = "AITokenTracker"
LABEL = "io.github.surajprem7.aitokentracker"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def command() -> list[str] | None:
    """How to start the app in widget mode, or None when we can't tell."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        return [str(exe), "--widget"]
    gui = shutil.which("ai-tokens-gui")
    return [gui, "--widget"] if gui else None


def _mac_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _linux_desktop() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / "ai-token-tracker.desktop"


def _quote_windows(args: list[str]) -> str:
    return " ".join(f'"{a}"' if " " in a or not a else a for a in args)


def is_enabled() -> bool:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, NAME)
            return True
        except OSError:
            return False
    if sys.platform == "darwin":
        return _mac_plist().is_file()
    return _linux_desktop().is_file()


def status() -> dict:
    return {"available": command() is not None, "enabled": is_enabled()}


def set_enabled(enabled: bool) -> dict:
    cmd = command()
    if enabled and cmd is None:
        raise RuntimeError("Starting with the computer works with the installed app.")
    if sys.platform == "win32":
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, _quote_windows(cmd))
            else:
                try:
                    winreg.DeleteValue(key, NAME)
                except OSError:
                    pass
    elif sys.platform == "darwin":
        path = _mac_plist()
        if enabled:
            # Inside an .app bundle, start the bundle's own executable (keeps the Dock icon and name right).
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as fh:
                plistlib.dump({"Label": LABEL, "ProgramArguments": cmd, "RunAtLoad": True,
                               "ProcessType": "Interactive"}, fh)
        else:
            path.unlink(missing_ok=True)
    else:
        path = _linux_desktop()
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            exec_line = " ".join(f'"{a}"' if " " in a else a for a in cmd)
            path.write_text("[Desktop Entry]\nType=Application\nName=AI Token Tracker\n"
                            f"Exec={exec_line}\nX-GNOME-Autostart-enabled=true\nTerminal=false\n", encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    return status()
