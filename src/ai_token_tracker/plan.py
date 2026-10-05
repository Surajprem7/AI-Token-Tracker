"""Claude plan usage: how much of your Claude subscription's limits you've used.

Uses the login Claude Code saved on this computer to ask Anthropic for the
plan's usage percentages (the same numbers Claude shows under Settings > Usage).
They cover everything on the account: Claude Code here and in the cloud,
claude.ai and the mobile app. It is a percentage of the plan, not per-session
token counts; Anthropic doesn't share those for subscriptions.

The login is read from Claude Code's own store (~/.claude/.credentials.json on
Windows and Linux, the Keychain on macOS), sent only to api.anthropic.com, and
never saved or logged. An expired login is not refreshed by us (that could sign
Claude Code out); opening Claude Code once refreshes it.

Note: the usage endpoint is the one Claude Code itself uses; Anthropic hasn't
documented it publicly, so its format could change.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
CACHE_SECONDS = 60
WINDOWS = (  # (field in Anthropic's reply, label shown in the app)
    ("five_hour", "Current 5-hour window"),
    ("seven_day", "This week (all models)"),
    ("seven_day_opus", "This week (Opus)"),
    ("seven_day_sonnet", "This week (Sonnet)"),
)

_cache: dict = {"at": 0.0, "value": None}


def _config_dir() -> Path:
    first = (os.environ.get("CLAUDE_CONFIG_DIR") or "").split(",")[0].strip()
    return Path(first).expanduser() if first else Path.home() / ".claude"


def _read_login() -> dict | None:
    """Claude Code's saved login ({accessToken, expiresAt, subscriptionType, ...}) or None."""
    payload = None
    path = _config_dir() / ".credentials.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = None
    if payload is None and sys.platform == "darwin":
        account = os.environ.get("USER") or "claude-code-user"
        try:
            out = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
                                  "-a", account, "-w"], capture_output=True, text=True, timeout=5)
            if out.returncode == 0:
                payload = json.loads(out.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            payload = None
    login = payload.get("claudeAiOauth") if isinstance(payload, dict) else None
    return login if isinstance(login, dict) and login.get("accessToken") else None


def _ssl_context():
    from .pricing import _ssl_context as ctx

    return ctx()


def _fetch_usage(token: str) -> dict:
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json", "User-Agent": f"ai-token-tracker/{__version__}"})
    with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as resp:  # noqa: S310 - fixed https URL
        return json.loads(resp.read().decode("utf-8"))


def _percent(value) -> float | None:
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(pct, 100.0)) if pct == pct else None  # NaN check


def parse_usage(body: dict) -> list[dict]:
    """Anthropic's reply -> [{label, percent, resets_at}] in a fixed, readable order."""
    rows = []
    for key, label in WINDOWS:
        entry = body.get(key)
        if isinstance(entry, dict):
            pct = _percent(entry.get("utilization"))
            if pct is not None:
                rows.append({"label": label, "percent": pct, "resets_at": entry.get("resets_at")})
    # Newer replies list extra per-model weekly limits separately.
    shown = {r["label"].lower() for r in rows}
    for entry in body.get("limits") or []:
        if not isinstance(entry, dict) or entry.get("kind") != "weekly_scoped":
            continue
        model = (entry.get("scope") or {}).get("model") or {}
        name = str(model.get("display_name") or model.get("id") or "").strip()
        pct = _percent(entry.get("percent"))
        label = f"This week ({name})"
        if name and pct is not None and label.lower() not in shown:
            rows.append({"label": label, "percent": pct, "resets_at": entry.get("resets_at")})
    return rows


def status(force: bool = False) -> dict:
    """What the dashboard shows. Cached for a minute so refreshes don't hammer Anthropic."""
    if not force and _cache["value"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["value"]
    result = {"available": False, "plan": None, "windows": [], "message": None}
    login = _read_login()
    if login is None:
        result["message"] = "Log in to Claude Code on this computer to see your Claude plan usage here."
    else:
        result["plan"] = str(login.get("subscriptionType") or "").title() or None
        expires = login.get("expiresAt")
        if isinstance(expires, (int, float)) and expires / 1000 < time.time():
            result["message"] = "Your Claude Code login has expired. Open Claude Code once to refresh it."
        else:
            try:
                result["windows"] = parse_usage(_fetch_usage(login["accessToken"]))
                result["available"] = bool(result["windows"])
                if not result["windows"]:
                    result["message"] = "Claude didn't report any plan limits for this account."
            except urllib.error.HTTPError as exc:
                result["message"] = ("Claude didn't accept the saved login. Open Claude Code once to refresh it."
                                     if exc.code in (401, 403) else f"Couldn't get plan usage (Claude replied {exc.code}).")
            except Exception as exc:  # offline, changed format...
                result["message"] = f"Couldn't get plan usage: {exc}"
    _cache.update(at=time.time(), value=result)
    return result
