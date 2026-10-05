"""Tests for Claude plan usage (no network: Anthropic's reply is faked)."""

import io
import json
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_token_tracker import plan  # noqa: E402
from isolation import isolate  # noqa: E402

REPLY = {
    "five_hour": {"utilization": 42.0, "resets_at": "2026-10-05T15:00:00Z"},
    "seven_day": {"utilization": 61.5, "resets_at": "2026-10-08T09:00:00Z"},
    "seven_day_opus": None,
    "limits": [{"kind": "weekly_scoped", "percent": 12, "resets_at": "2026-10-08T09:00:00Z",
                "scope": {"model": {"display_name": "Fable"}}},
               {"kind": "something_else", "percent": 99}],
}


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        isolate(self, self.tmp)
        plan._cache.update(at=0.0, value=None)
        self.creds = self.tmp / "home" / ".claude" / ".credentials.json"

    def save_login(self, **extra):
        self.creds.parent.mkdir(parents=True, exist_ok=True)
        login = {"accessToken": "secret-token", "expiresAt": (time.time() + 3600) * 1000, "subscriptionType": "max"}
        login.update(extra)
        self.creds.write_text(json.dumps({"claudeAiOauth": login}))

    def test_parse_reply(self):
        rows = plan.parse_usage(REPLY)
        self.assertEqual([r["label"] for r in rows],
                         ["Current 5-hour window", "This week (all models)", "This week (Fable)"])
        self.assertEqual(rows[0]["percent"], 42.0)
        self.assertEqual(plan.parse_usage({"five_hour": {"utilization": 250}})[0]["percent"], 100.0)

    def test_status_with_saved_login(self):
        self.save_login()
        with mock.patch.object(plan, "_fetch_usage", return_value=REPLY) as fetch:
            s = plan.status()
            plan.status()  # cached for a minute
        fetch.assert_called_once_with("secret-token")
        self.assertTrue(s["available"])
        self.assertEqual(s["plan"], "Max")
        self.assertNotIn("secret-token", json.dumps(s))  # the login never reaches the page

    def test_no_login(self):
        s = plan.status()
        self.assertFalse(s["available"])
        self.assertIn("Log in to Claude Code", s["message"])

    def test_expired_login_is_not_used(self):
        self.save_login(expiresAt=(time.time() - 60) * 1000)
        with mock.patch.object(plan, "_fetch_usage") as fetch:
            s = plan.status()
        fetch.assert_not_called()
        self.assertIn("expired", s["message"])

    def test_rejected_login(self):
        self.save_login()
        err = urllib.error.HTTPError(plan.USAGE_URL, 401, "Unauthorized", {}, io.BytesIO(b""))
        with mock.patch.object(plan, "_fetch_usage", side_effect=err):
            s = plan.status()
        self.assertIn("Open Claude Code once", s["message"])


if __name__ == "__main__":
    unittest.main()
