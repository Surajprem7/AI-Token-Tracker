"""Plan limits for Codex, Gemini, Cursor, Copilot and Kimi, and service status (no network: replies are faked)."""

import base64
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_token_tracker import cursor_usage, limits, plan  # noqa: E402
from isolation import isolate  # noqa: E402


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{body}.s"


class LimitsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        isolate(self, self.tmp)
        self.home = self.tmp / "home"
        limits._cache.update(at=0.0, value=None)
        limits._status_cache.update(at=0.0, value=[])
        plan._cache.update(at=0.0, value=None)

    def test_nothing_logged_in_means_no_network(self):
        with mock.patch.object(limits, "_request") as req, mock.patch.object(cursor_usage, "_get") as get:
            self.assertEqual(limits.status(), {"providers": [], "incidents": []})
        req.assert_not_called()
        get.assert_not_called()

    def test_codex(self):
        token = jwt({"exp": time.time() + 3600, "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"}})
        write(self.home / ".codex/auth.json", {"tokens": {"access_token": token, "account_id": "acc"}})
        reply = {"plan_type": "pro", "rate_limit": {
            "primary_window": {"used_percent": 42, "limit_window_seconds": 18000, "reset_at": 1790000000},
            "secondary_window": {"used_percent": 7, "limit_window_seconds": 604800, "reset_after_seconds": 100}}}
        with mock.patch.object(limits, "_request", return_value=reply) as req:
            r = limits.codex()
        self.assertEqual(req.call_args[0][1]["ChatGPT-Account-Id"], "acc")
        self.assertEqual(r["plan"], "Pro")
        self.assertEqual([(w["label"], w["percent"]) for w in r["windows"]],
                         [("Current 5-hour window", 42.0), ("This week", 7.0)])
        self.assertNotIn(token, json.dumps(r))

    def test_codex_expired_login_is_not_used(self):
        write(self.home / ".codex/auth.json", {"tokens": {"access_token": jwt({"exp": time.time() - 10})}})
        with mock.patch.object(limits, "_request") as req:
            r = limits.codex()
        req.assert_not_called()
        self.assertIn("expired", r["message"])

    def test_gemini(self):
        write(self.home / ".gemini/oauth_creds.json", {"access_token": "g", "expiry_date": (time.time() + 600) * 1000})
        replies = [{"cloudaicompanionProject": "proj-1", "currentTier": {"name": "Gemini Code Assist"}},
                   {"buckets": [{"modelId": "gemini-2.5-pro", "remainingFraction": 0.75, "resetTime": "2026-10-06T00:00:00Z"},
                                {"modelId": "gemini-2.5-pro", "remainingFraction": 0.5},
                                {"modelId": "gemini-2.5-flash", "remainingFraction": 1}]}]
        with mock.patch.object(limits, "_request", side_effect=replies) as req:
            r = limits.gemini()
        self.assertEqual(req.call_args_list[1][0][2], {"project": "proj-1"})
        self.assertEqual([(w["label"], w["percent"]) for w in r["windows"]],
                         [("Today (gemini-2.5-flash)", 0.0), ("Today (gemini-2.5-pro)", 50.0)])

    def test_copilot(self):
        write(self.home / ".config/github-copilot/apps.json", {"github.com:Iv1.x": {"oauth_token": "gho_1"}})
        reply = {"copilot_plan": "individual_pro", "quota_reset_date": "2026-11-01",
                 "quota_snapshots": {"premium_interactions": {"percent_remaining": 80, "entitlement": 300},
                                     "chat": {"unlimited": True}}}
        with mock.patch.object(limits, "_request", return_value=reply) as req:
            r = limits.copilot()
        self.assertEqual(req.call_args[0][1]["Authorization"], "token gho_1")
        self.assertEqual(r["plan"], "Individual Pro")
        self.assertEqual(r["windows"], [{"label": "Premium requests this month", "percent": 20.0,
                                         "resets_at": "2026-11-01T00:00:00Z"}])

    def test_kimi(self):
        write(self.home / ".kimi-code/credentials/kimi-code.json", {"access_token": "k", "expires_at": time.time() + 600})
        reply = {"usage": {"limit": 100, "used": 25, "resetTime": "2026-10-06T00:00:00Z"},
                 "limits": [{"detail": {"limit": 50, "remaining": 40}}], "user": {"membership": {"level": "pro"}}}
        with mock.patch.object(limits, "_request", return_value=reply):
            r = limits.kimi()
        self.assertEqual([w["percent"] for w in r["windows"]], [25.0, 20.0])
        self.assertEqual(r["plan"], "Pro")

    def test_cursor(self):
        summary = {"membershipType": "pro", "billingCycleEnd": "2026-11-01T00:00:00Z",
                   "individualUsage": {"plan": {"totalPercentUsed": 33.3, "autoPercentUsed": 10, "apiPercentUsed": 60}}}
        with mock.patch.object(cursor_usage, "read_login", return_value={"cookie": "c", "token": "t"}), \
                mock.patch.object(cursor_usage, "_get", return_value=json.dumps(summary).encode()):
            r = limits.cursor()
        self.assertEqual([w["label"] for w in r["windows"]], ["This billing month", "Auto model", "API models"])
        self.assertEqual(r["plan"], "Pro")

    def test_status_reports_incidents_only_for_your_ais(self):
        write(self.home / ".codex/auth.json", {"tokens": {"access_token": jwt({"exp": time.time() + 3600})}})

        def fake(url, headers, body=None, timeout=15):
            if "status.openai.com" in url:
                return {"status": {"indicator": "major", "description": "Partial outage"}}
            if "status." in url or "githubstatus" in url:
                raise AssertionError("only AIs you use are checked")
            return {"rate_limit": {"primary_window": {"used_percent": 5, "limit_window_seconds": 18000}}}

        with mock.patch.object(limits, "_request", side_effect=fake):
            s = limits.status()
            limits.status()  # cached
        self.assertEqual([p["id"] for p in s["providers"]], ["codex"])
        self.assertEqual(s["incidents"], [{"id": "codex", "name": "ChatGPT / OpenAI", "level": "major",
                                           "description": "Partial outage", "url": "https://status.openai.com"}])

    def test_one_failing_provider_does_not_hide_others(self):
        write(self.home / ".codex/auth.json", {"tokens": {"access_token": jwt({"exp": time.time() + 3600})}})
        with mock.patch.object(limits, "gemini", side_effect=RuntimeError("boom")), \
                mock.patch.object(limits, "PROVIDERS", (limits.codex, limits.gemini)), \
                mock.patch.object(limits, "_request", return_value={"status": {"indicator": "none"}}):
            s = limits.status(force=True)
        self.assertEqual(len(s["providers"]), 2)


if __name__ == "__main__":
    unittest.main()
