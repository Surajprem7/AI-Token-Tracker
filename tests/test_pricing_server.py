"""Tests for cost estimates and the local dashboard server."""

import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_token_tracker import core, pricing, server, sources  # noqa: E402


class PricingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patcher = mock.patch.dict(os.environ, {"AI_TOKEN_TRACKER_DIR": str(self.tmp)})
        patcher.start()
        self.addCleanup(patcher.stop)
        pricing.reload()
        self.addCleanup(pricing.reload)

    def test_claude_cost_uses_cache_prices(self):
        # Opus 5.5: $4 in, $20 out, $0.20 cache read, $5 5-minute write, $8 1-hour write per 1M tokens
        u = core.Usage(input=1_000_000, output=1_000_000, cache_read=1_000_000, cache_write=2_000_000, cache_write_1h=1_000_000)
        self.assertAlmostEqual(pricing.cost("claude-opus-5-5", u), 4 + 20 + 0.2 + 5 + 8)

    def test_model_name_variants(self):
        base = pricing.lookup("claude-sonnet-4-5")
        self.assertIsNotNone(base)
        self.assertEqual(pricing.lookup("claude-sonnet-4-5-20250929"), base)
        self.assertEqual(pricing.lookup("claude-sonnet-4-5[1m]"), base)
        self.assertEqual(pricing.lookup("anthropic/claude-sonnet-4-5"), base)
        self.assertIsNotNone(pricing.lookup("gemini-2.5-pro"))
        self.assertEqual(pricing.lookup("llama3.1")[:2], (0.0, 0.0))  # local Ollama model
        self.assertIsNone(pricing.lookup("totally-unknown-model"))

    def test_user_override_wins(self):
        (self.tmp / "prices.json").write_text(json.dumps({"my-model": {"input": 2, "output": 8}}))
        pricing.reload()
        self.assertAlmostEqual(pricing.cost("my-model", core.Usage(input=500_000, output=250_000)), 1 + 2)
        self.assertEqual(pricing.pricing_info()["overrides"], 1)

    def test_trim_litellm_format(self):
        raw = {"gpt-x": {"litellm_provider": "openai", "mode": "chat", "input_cost_per_token": 1e-6,
                         "output_cost_per_token": 4e-6, "cache_read_input_token_cost": 1e-7},
               "embed": {"litellm_provider": "openai", "mode": "embedding", "input_cost_per_token": 1e-7},
               "other": {"litellm_provider": "somecloud", "mode": "chat", "input_cost_per_token": 1e-6,
                         "output_cost_per_token": 1e-6}}
        self.assertEqual(pricing.trim(raw), {"gpt-x": [1.0, 4.0, 0.1]})


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.env = mock.patch.dict(os.environ, {
            "CODEX_HOME": str(cls.tmp / "none"), "GEMINI_CLI_HOME": str(cls.tmp / "none"),
            "AI_TOKEN_TRACKER_DIR": str(cls.tmp / "custom")})
        cls.env.start()
        sources.log_usage("ChatGPT", "gpt-5", 1000, 200, prompt="<script>alert(1)</script>")
        cls.httpd, cls.app, cls.url = server.start(0, roots=[cls.tmp / "empty"])
        cls.base = cls.url.split("/?")[0]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.env.stop()

    def get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_data_needs_token(self):
        self.assertEqual(self.get("/api/data")[0], 403)
        status, body = self.get("/api/data", {"X-Token": self.app.token})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["tools"], ["ChatGPT"])
        call = data["sessions"][0]["turns"][0]["c"][0]
        self.assertEqual(call[3:5], [1000, 200])
        self.assertGreater(call[7], 0)  # gpt-5 has a price
        self.assertTrue({"Claude Code", "Codex CLI", "Gemini CLI", "OpenCode", "Qwen Code", "Custom log"}
                        <= {s["tool"] for s in data["sources"]})

    def test_rejects_other_hosts(self):
        # DNS-rebinding style request: right port, wrong Host header
        self.assertEqual(self.get("/api/data", {"X-Token": self.app.token, "Host": "evil.example"})[0], 403)

    def test_static_files_and_traversal(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"AI Token Tracker", body)
        self.assertEqual(self.get("/app.js")[0], 200)
        self.assertEqual(self.get("/..%2fserver.py")[0], 404)
        self.assertEqual(self.get("/../server.py")[0], 404)


if __name__ == "__main__":
    unittest.main()
