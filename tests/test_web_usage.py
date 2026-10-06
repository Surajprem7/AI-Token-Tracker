"""Browser extension connection: key check, saving chats, Claude usage from claude.ai."""

import json
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_token_tracker import limits, plan, server, web_usage  # noqa: E402
from ai_token_tracker import extra_sources as extra  # noqa: E402
from isolation import isolate  # noqa: E402

CHAT = {"site": "claude", "id": "0b1c2d3e-aaaa-bbbb-cccc-123456789012", "title": "Plan a trip", "model": "claude-sonnet-4-5",
        "turns": [{"id": "m2", "t": 1790000000000, "prompt": "plan a trip to goa", "input": 120, "output": 800},
                  {"id": "m4", "t": 1790000060000, "prompt": "make it cheaper", "input": 950, "output": 400}]}


class WebUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        isolate(self, self.tmp)
        limits._cache.update(at=0.0, value=None)
        plan._cache.update(at=0.0, value=None)

    def test_key_is_kept(self):
        k = web_usage.connection_key()
        self.assertEqual(k, web_usage.connection_key())
        self.assertTrue(web_usage.key_ok(k))
        self.assertFalse(web_usage.key_ok("wrong"))
        self.assertFalse(web_usage.key_ok(None))

    def test_save_twice_counts_once(self):
        web_usage.save_chat(CHAT)
        web_usage.save_chat(CHAT)
        [s] = [s for s in extra.load_all() if s.tool == "Claude.ai (web)"]
        self.assertEqual((s.title, len(s.calls), s.usage.input, s.usage.output), ("Plan a trip", 2, 1070, 1200))
        self.assertEqual(s.turns[0].prompt, "plan a trip to goa")

    def test_bad_input_is_refused(self):
        for bad in ({"site": "evil", "id": "x"}, {"site": "claude", "id": "../../etc"}, [1, 2]):
            with self.assertRaises(ValueError):
                web_usage.save_chat(bad)

    def test_endpoints_need_the_key(self):
        httpd, app, url = server.start(port=0)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        def call(path, body=None, key=None):
            req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                         method="GET" if body is None else "POST", headers={"X-AITT-Key": key or ""})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())

        with self.assertRaises(urllib.error.HTTPError):
            call("/api/web/chat", CHAT, key="nope")
        key = web_usage.connection_key()
        self.assertTrue(call("/api/web/ping", key=key)["ok"])
        self.assertEqual(call("/api/web/chat", CHAT, key=key)["replies"], 2)
        call("/api/web/limits", {"windows": [{"label": "Session (5h)", "percent": 12, "resets_at": "2026-10-06T12:00:00Z"}]}, key=key)
        code = json.loads(urllib.request.urlopen(
            urllib.request.Request(base + "/api/extension", headers={"X-Token": app.token}), timeout=5).read())
        self.assertEqual(code["code"], f"{httpd.server_address[1]}-{key}")
        self.assertEqual(code["chats"], 1)

    def test_one_click_connect_link(self):
        from unittest import mock
        httpd, app, url = server.start(port=0)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with mock.patch("webbrowser.open") as opened:
            link = json.loads(urllib.request.urlopen(urllib.request.Request(
                base + "/api/extension/connect", method="POST", data=b"", headers={"X-Token": app.token}), timeout=5).read())["url"]
            time.sleep(0.2)
        opened.assert_called_once_with(link)
        page = urllib.request.urlopen(link, timeout=5).read().decode()
        self.assertIn(f'content="{httpd.server_address[1]}-{web_usage.connection_key()}"', page)
        with self.assertRaises(urllib.error.HTTPError) as err:  # an unknown link shows no key
            urllib.request.urlopen(base + "/connect?n=guess", timeout=5)
        self.assertEqual(err.exception.code, 410)
        self.assertNotIn(web_usage.connection_key(), err.exception.read().decode())

    def test_claude_usage_from_the_website_when_claude_code_isnt_logged_in(self):
        self.assertIsNone(limits.claude())
        web_usage.save_limits({"windows": [{"label": "Session (5h)", "percent": 12}, {"label": "Weekly", "percent": 62}]})
        r = limits.claude()
        self.assertEqual([w["percent"] for w in r["windows"]], [12.0, 62.0])


if __name__ == "__main__":
    unittest.main()
