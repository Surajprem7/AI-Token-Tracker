"""Plan limits for every AI subscription logged in on this computer, plus "is it down?" checks.

Each AI tool saves its own login on your computer. When it's there, we use it to ask
that company how much of your plan's limits you've used, the same numbers its own
settings page shows. Logins are read, sent only to that company's own address, and
never saved, logged or shown to the page. An expired login is never refreshed by us
(that could sign the tool out); opening the tool once refreshes it.

    Claude          ~/.claude/.credentials.json or the macOS Keychain   -> api.anthropic.com
    ChatGPT/Codex   ~/.codex/auth.json                                   -> chatgpt.com
    Gemini          ~/.gemini/oauth_creds.json                           -> cloudcode-pa.googleapis.com
    Cursor          Cursor's state.vscdb                                 -> cursor.com
    GitHub Copilot  ~/.config/github-copilot/{apps,hosts}.json           -> api.github.com
    Kimi            ~/.kimi-code (or ~/.kimi)/credentials/kimi-code.json -> api.kimi.com

Service status comes from each company's public status page (no login).

These are the endpoints the tools themselves use; the companies haven't documented
them publicly, so a format change shows up as "couldn't read" rather than wrong numbers.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, plan

CACHE_SECONDS = 120
STATUS_SECONDS = 300
_cache: dict = {"at": 0.0, "value": None}
_status_cache: dict = {"at": 0.0, "value": []}
_lock = threading.Lock()

STATUS_PAGES = {  # provider id -> (name, Statuspage JSON, page people open)
    "claude": ("Claude", "https://status.claude.com/api/v2/status.json", "https://status.claude.com"),
    "codex": ("ChatGPT / OpenAI", "https://status.openai.com/api/v2/status.json", "https://status.openai.com"),
    "cursor": ("Cursor", "https://status.cursor.com/api/v2/status.json", "https://status.cursor.com"),
    "copilot": ("GitHub", "https://www.githubstatus.com/api/v2/status.json", "https://www.githubstatus.com"),
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _jwt_claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return claims if isinstance(claims, dict) else {}
    except (IndexError, ValueError):
        return {}


def _request(url: str, headers: dict, body: dict | None = None, timeout: float = 15) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json", "User-Agent": f"ai-token-tracker/{__version__}", **headers}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout, context=plan._ssl_context()) as resp:  # noqa: S310 - fixed https URLs
        return json.loads(resp.read(2 * 1024 * 1024).decode("utf-8"))


def _pct(value) -> float | None:
    return plan._percent(value)


def _iso(value) -> str | None:
    """Reset times arrive as ISO text, epoch seconds or a bare date; the page wants ISO."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if len(text) == 10 and text[4] == "-":
            text += "T00:00:00Z"
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
            return text
        except ValueError:
            return None
    return None


def _row(label: str, percent, resets_at=None) -> dict | None:
    pct = _pct(percent)
    return {"label": label, "percent": pct, "resets_at": _iso(resets_at)} if pct is not None else None


def _result(pid: str, name: str, plan_name=None, rows=None, message=None) -> dict:
    rows = [r for r in rows or [] if r]
    return {"id": pid, "name": name, "plan": plan_name, "windows": rows,
            "available": bool(rows), "message": message if not rows else None}


def _http_message(name: str, exc: Exception, tool_hint: str) -> str:
    if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403):
        return f"{name} didn't accept the saved login. {tool_hint}"
    if isinstance(exc, urllib.error.HTTPError):
        return f"Couldn't get {name} limits ({name} replied {exc.code})."
    return f"Couldn't get {name} limits: {exc}"


# --------------------------------------------------------------------------- #
# Providers: each returns None when that AI isn't logged in on this computer
# --------------------------------------------------------------------------- #


def claude() -> dict | None:
    if plan._read_login() is None:
        # No Claude Code login here: use what the browser extension last saw on claude.ai.
        from . import web_usage

        windows = web_usage.recent_limits()
        return _result("claude", "Claude", rows=windows) if windows else None
    s = plan.status()
    return _result("claude", "Claude", s.get("plan"), s.get("windows"), s.get("message"))


def codex_auth() -> dict | None:
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    tokens = (_json(home / "auth.json") or {}).get("tokens")
    return tokens if isinstance(tokens, dict) and tokens.get("access_token") else None


def _codex_window(w, fallback: str) -> dict | None:
    if not isinstance(w, dict):
        return None
    seconds = w.get("limit_window_seconds")
    label = {18000: "Session (5h)", 604800: "Weekly"}.get(seconds, fallback)
    reset = w.get("reset_at")
    if reset is None and isinstance(w.get("reset_after_seconds"), (int, float)):
        reset = time.time() + w["reset_after_seconds"]
    return _row(label, w.get("used_percent"), reset)


def codex() -> dict | None:
    tokens = codex_auth()
    if not tokens:
        return None
    token = tokens["access_token"]
    claims = _jwt_claims(token)
    auth = claims.get("https://api.openai.com/auth") if isinstance(claims.get("https://api.openai.com/auth"), dict) else {}
    plan_name = str(auth.get("chatgpt_plan_type") or "").title() or None
    if isinstance(claims.get("exp"), (int, float)) and claims["exp"] < time.time():
        return _result("codex", "ChatGPT (Codex)", plan_name,
                       message="Your Codex login has expired. Open Codex once to refresh it.")
    headers = {"Authorization": f"Bearer {token}"}
    account = tokens.get("account_id") or auth.get("chatgpt_account_id")
    if account:
        headers["ChatGPT-Account-Id"] = str(account)
    try:
        body = _request("https://chatgpt.com/backend-api/wham/usage", headers)
    except Exception as exc:
        return _result("codex", "ChatGPT (Codex)", plan_name,
                       message=_http_message("ChatGPT", exc, "Open Codex once to refresh it."))
    rate = body.get("rate_limit") if isinstance(body.get("rate_limit"), dict) else {}
    rows = [_codex_window(rate.get("primary_window"), "Short window"),
            _codex_window(rate.get("secondary_window"), "Long window")]
    credit = (body.get("spend_control") or {}).get("individual_limit") if isinstance(body.get("spend_control"), dict) else None
    if isinstance(credit, dict):
        pct = credit.get("used_percent")
        if pct is None and credit.get("limit"):
            try:
                pct = float(credit.get("used") or 0) / float(credit["limit"]) * 100
            except (TypeError, ValueError, ZeroDivisionError):
                pct = None
        rows.append(_row("Credits", pct, credit.get("reset_at")))
    plan_name = str(body.get("plan_type") or "").title() or plan_name
    return _result("codex", "ChatGPT (Codex)", plan_name, rows, "ChatGPT didn't report any limits for this account.")


def gemini() -> dict | None:
    home = Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()).expanduser() / ".gemini"
    creds = _json(home / "oauth_creds.json")
    if not isinstance(creds, dict) or not creds.get("access_token"):
        return None
    expiry = creds.get("expiry_date")
    if isinstance(expiry, (int, float)) and expiry / 1000 < time.time():
        return _result("gemini", "Gemini", message="Your Gemini CLI login has expired. Run gemini once to refresh it.")
    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    try:
        assist = _request("https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist", headers,
                          {"metadata": {"ideType": "GEMINI_CLI", "pluginType": "GEMINI"}})
        project = assist.get("cloudaicompanionProject")
        if isinstance(project, dict):
            project = project.get("id") or project.get("projectId")
        tier = (assist.get("currentTier") or {}).get("name") or (assist.get("currentTier") or {}).get("id")
        body = _request("https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota", headers,
                        {"project": project} if isinstance(project, str) and project else {})
    except Exception as exc:
        return _result("gemini", "Gemini", message=_http_message("Gemini", exc, "Run gemini once to refresh it."))
    worst: dict[str, dict] = {}
    for b in body.get("buckets") or []:
        if not isinstance(b, dict) or not isinstance(b.get("modelId"), str):
            continue
        try:
            remaining = float(b.get("remainingFraction"))
        except (TypeError, ValueError):
            continue
        if b["modelId"] not in worst or remaining < worst[b["modelId"]]["remaining"]:
            worst[b["modelId"]] = {"remaining": remaining, "reset": b.get("resetTime")}
    rows = [_row(f"Today ({model})", (1 - v["remaining"]) * 100, v["reset"]) for model, v in sorted(worst.items())]
    return _result("gemini", "Gemini", str(tier) if tier else None, rows, "Gemini didn't report any limits.")


def cursor() -> dict | None:
    from . import cursor_usage

    login = cursor_usage.read_login()
    if not login:
        return None
    try:
        body = json.loads(cursor_usage._get(cursor_usage.SUMMARY_URL, login).decode("utf-8"))
    except Exception as exc:
        return _result("cursor", "Cursor", message=_http_message("Cursor", exc, "Open Cursor once to refresh it."))
    ind = body.get("individualUsage") if isinstance(body.get("individualUsage"), dict) else {}
    plan_usage = ind.get("plan") if isinstance(ind.get("plan"), dict) else {}
    end = body.get("billingCycleEnd")
    pct = plan_usage.get("totalPercentUsed")
    if pct is None and plan_usage.get("limit"):
        try:
            pct = float(plan_usage.get("used") or 0) / float(plan_usage["limit"]) * 100
        except (TypeError, ValueError, ZeroDivisionError):
            pct = None
    rows = [_row("This billing month", pct, end),
            _row("Auto model", plan_usage.get("autoPercentUsed"), end),
            _row("API models", plan_usage.get("apiPercentUsed"), end)]
    on_demand = ind.get("onDemand") if isinstance(ind.get("onDemand"), dict) else {}
    if on_demand.get("limit"):
        try:
            rows.append(_row("On-demand spending", float(on_demand.get("used") or 0) / float(on_demand["limit"]) * 100, end))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    membership = str(body.get("membershipType") or "").title() or None
    return _result("cursor", "Cursor", membership, rows, "Cursor didn't report any limits.")


def copilot_token() -> str | None:
    bases = [Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config").expanduser(), Path.home() / ".config"]
    if os.name == "nt":
        bases.append(Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"))
    fallback = None
    for base, name in ((b, n) for b in dict.fromkeys(bases) for n in ("apps.json", "hosts.json")):
        doc = _json(base / "github-copilot" / name)
        if not isinstance(doc, dict):
            continue
        for key, value in doc.items():
            token = value.get("oauth_token") if isinstance(value, dict) else None
            if isinstance(token, str) and token:
                if str(key).split(":")[0] == "github.com":
                    return token
                fallback = fallback or token
    return fallback


def copilot() -> dict | None:
    token = copilot_token()
    if not token:
        return None
    try:
        body = _request("https://api.github.com/copilot_internal/user", {
            "Authorization": f"token {token}", "X-Github-Api-Version": "2025-04-01",
            "Editor-Version": "vscode/1.96.2", "Editor-Plugin-Version": "copilot-chat/0.26.7"})
    except Exception as exc:
        return _result("copilot", "GitHub Copilot",
                       message=_http_message("GitHub", exc, "Sign in to Copilot again in your editor."))
    reset = body.get("quota_reset_date")
    snaps = body.get("quota_snapshots") if isinstance(body.get("quota_snapshots"), dict) else {}
    rows = []
    for key, label in (("premium_interactions", "Premium requests this month"), ("chat", "Chat this month"),
                       ("completions", "Code completions this month")):
        snap = snaps.get(key)
        if not isinstance(snap, dict) or snap.get("unlimited"):
            continue
        remaining = snap.get("percent_remaining")
        if isinstance(remaining, (int, float)) and not isinstance(remaining, bool):
            rows.append(_row(label, 100 - remaining, reset))
    plan_name = str(body.get("copilot_plan") or "").replace("_", " ").title() or None
    return _result("copilot", "GitHub Copilot", plan_name, rows, "Copilot reports no limits for your plan.")


def kimi() -> dict | None:
    creds = None
    for home in (Path(os.environ.get("KIMI_CODE_HOME") or Path.home() / ".kimi-code"),
                 Path(os.environ.get("KIMI_HOME") or Path.home() / ".kimi")):
        creds = _json(home.expanduser() / "credentials" / "kimi-code.json")
        if isinstance(creds, dict) and creds.get("access_token"):
            break
        creds = None
    if not creds:
        return None
    expires = creds.get("expires_at")
    if isinstance(expires, (int, float)) and expires < time.time():
        return _result("kimi", "Kimi", message="Your Kimi login has expired. Open Kimi once to refresh it.")
    try:
        body = _request("https://api.kimi.com/coding/v1/usages", {"Authorization": f"Bearer {creds['access_token']}"})
    except Exception as exc:
        return _result("kimi", "Kimi", message=_http_message("Kimi", exc, "Open Kimi once to refresh it."))

    def window(label, d):
        if not isinstance(d, dict):
            return None
        try:
            limit = float(d.get("limit"))
            used = float(d["used"]) if d.get("used") is not None else limit - float(d.get("remaining"))
        except (TypeError, ValueError, KeyError):
            return None
        return _row(label, used / limit * 100, d.get("resetTime") or d.get("reset_at") or d.get("resetAt")) if limit > 0 else None

    first = (body.get("limits") or [None])[0]
    detail = first.get("detail") if isinstance(first, dict) and isinstance(first.get("detail"), dict) else first
    rows = [window("Current window", body.get("usage")), window("Rate limit", detail),
            window("Total quota", body.get("totalQuota"))]
    level = ((body.get("user") or {}).get("membership") or {}).get("level") if isinstance(body.get("user"), dict) else None
    return _result("kimi", "Kimi", str(level).title() if level else None, rows, "Kimi didn't report any limits.")


PROVIDERS = (claude, codex, gemini, cursor, copilot, kimi)


# --------------------------------------------------------------------------- #
# Service status
# --------------------------------------------------------------------------- #


def service_status(provider_ids: list[str]) -> list[dict]:
    """Incidents for the AIs you use. Only problems are returned; an empty list means all is well."""
    if time.time() - _status_cache["at"] < STATUS_SECONDS:
        return [s for s in _status_cache["value"] if s["id"] in provider_ids]

    def check(pid):
        name, url, page = STATUS_PAGES[pid]
        try:
            body = _request(url, {}, timeout=6)
        except Exception:
            return None
        st = body.get("status") if isinstance(body.get("status"), dict) else {}
        indicator = st.get("indicator")
        if indicator in (None, "none"):
            return None
        return {"id": pid, "name": name, "level": indicator if indicator in ("minor", "major", "critical") else "minor",
                "description": str(st.get("description") or "Service problems reported"), "url": page}

    ids = [p for p in STATUS_PAGES if p in provider_ids]
    with ThreadPoolExecutor(max_workers=4) as pool:
        found = [r for r in pool.map(check, ids) if r]
    _status_cache.update(at=time.time(), value=found)
    return found


# --------------------------------------------------------------------------- #
# What the dashboard asks for
# --------------------------------------------------------------------------- #


def status(force: bool = False) -> dict:
    """{providers: [...], incidents: [...]}; cached for two minutes so refreshes don't hammer anyone."""
    with _lock:
        if not force and _cache["value"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
            return _cache["value"]
        if force:
            plan._cache.update(at=0.0, value=None)

        def run(fn):
            try:
                return fn()
            except Exception as exc:  # one provider's surprise must not hide the others
                name = getattr(fn, "__name__", "provider")
                return _result(name, name.title(), message=f"Couldn't read limits: {exc}")

        with ThreadPoolExecutor(max_workers=len(PROVIDERS)) as pool:
            providers = [p for p in pool.map(run, PROVIDERS) if p]
        try:
            incidents = service_status([p["id"] for p in providers])
        except Exception:
            incidents = []
        value = {"providers": providers, "incidents": incidents}
        _cache.update(at=time.time(), value=value)
        return value
