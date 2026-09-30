"""Regression tests for the problems found in the full code audit."""

import json
import sys
import tempfile
import unittest
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_token_tracker import cli, core, more_sources, pricing, server, sources  # noqa: E402
from isolation import isolate  # noqa: E402


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def claude_reply(msg_id, ts, out=10):
    return {"type": "assistant", "timestamp": ts, "requestId": "req_" + msg_id, "message": {
        "id": msg_id, "model": "claude-opus-5-5",
        "usage": {"input_tokens": 1, "output_tokens": out, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.claude = self.tmp / "claude"
        isolate(self, self.tmp, AI_TOKEN_TRACKER_DIR=str(self.tmp / "att"))
        pricing.reload()
        self.addCleanup(pricing.reload)

    def load(self):
        return core.load_sessions([self.claude])


class TimestampTests(Base):
    def test_naive_timestamps_mix_with_utc_ones(self):
        # A plain datetime.now() and a CSV time without offset used to crash sorting.
        write_jsonl(self.claude / "p" / "a.jsonl", [claude_reply("m1", "2026-09-30T08:00:00Z")])
        sources.log_usage("My app", "gpt-5", 10, 5, timestamp=datetime(2026, 9, 30, 10, 0))
        (sources.custom_dir() / "more.csv").write_text("timestamp,tool,model,input_tokens,output_tokens\n"
                                                       "2026-09-30 11:00:00,Other,gpt-5,1,1\n,Other,gpt-5,2,2\n")
        names = {s.tool for s in self.load()}
        self.assertEqual(names, {"Claude Code", "My app", "Other"})
        self.assertIsNotNone(core.parse_ts("2026-09-30 10:00:00").tzinfo)


class CustomCsvTests(Base):
    def test_bad_csvs_never_hide_other_rows(self):
        folder = sources.custom_dir()
        folder.mkdir(parents=True)
        sources.log_usage("Kept", "gpt-5", 10, 5)
        (folder / "excel.csv").write_bytes("tool,model,input_tokens,output_tokens,prompt\nX,gpt-5,5,5,caf\xe9\n".encode("cp1252"))
        (folder / "weird.csv").write_text('tool,model,input_tokens,output_tokens\nY,gpt-5,inf,1\nZ,gpt-5,"unterminated,1\n')
        tools = {s.tool for s in sources.load_custom_sessions()}
        self.assertTrue({"Kept", "X", "Y"} <= tools)


class ReaderRobustnessTests(Base):
    def test_deeply_nested_line_is_skipped(self):
        deep = "[" * 5000 + "]" * 5000
        write_jsonl(self.claude / "p" / "a.jsonl", [deep, claude_reply("m1", "2026-09-30T08:00:00Z")])
        self.assertEqual(len(self.load()), 1)

    def test_forked_claude_session_counted_once(self):
        original = [claude_reply("m1", "2026-09-30T08:00:00Z"), claude_reply("m2", "2026-09-30T08:01:00Z")]
        fork = original + [claude_reply("m3", "2026-09-30T09:00:00Z", out=99)]
        write_jsonl(self.claude / "p" / "orig.jsonl", original)
        write_jsonl(self.claude / "p" / "fork.jsonl", fork)
        sessions = {s.session_id: s for s in self.load()}
        self.assertEqual(len(sessions["orig"].calls), 2)
        self.assertEqual([c.key for c in sessions["fork"].calls], ["m3"])

    def test_cline_deleted_requests_still_count(self):
        user_dir = self.tmp / "Code" / "User"
        path = user_dir / "globalStorage" / "saoudrizwan.claude-dev" / "tasks" / "t" / "ui_messages.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps([
            {"ts": 1, "type": "say", "say": "task", "text": "go"},
            {"ts": 2, "type": "say", "say": "api_req_started", "text": json.dumps({"tokensIn": 10, "tokensOut": 1, "cost": 0.01})},
            {"ts": 3, "type": "say", "say": "deleted_api_reqs", "text": json.dumps({"tokensIn": 70, "tokensOut": 7, "cost": 0.07})},
        ]))
        [s] = more_sources.load_vscode_extension_tasks([user_dir])
        self.assertEqual(s.usage.input, 80)


class PricingTests(Base):
    def test_bad_override_values_are_ignored_not_fatal(self):
        pricing.override_file().parent.mkdir(parents=True, exist_ok=True)
        pricing.override_file().write_text(json.dumps({
            "my-model": {"input": 1.0, "output": "4", "cache_read": None},
            "broken": {"input": "1"}}))
        pricing.reload()
        self.assertAlmostEqual(pricing.cost("my-model", core.Usage(input=1_000_000, output=1_000_000, cache_read=1_000_000)), 1 + 0 + 1)
        self.assertIsNone(pricing.lookup("broken"))

    def test_prefix_match_prefers_model_maker_and_skips_local_models(self):
        self.assertEqual(pricing.lookup("deepseek-v4-flash-0801"), pricing.lookup("deepseek/deepseek-v4-flash"))
        self.assertIsNone(pricing.lookup("llama3.1-8b-instant"))  # hosted Groq model, not free local Ollama
        self.assertEqual(pricing.lookup("llama3.1")[:2], (0.0, 0.0))  # the local model itself is free


class CliTests(Base):
    def test_session_numbers_start_at_one(self):
        s1 = core.Session("a", "p", Path("a"))
        self.assertIsNone(cli.find_session([s1], "00"))
        self.assertIs(cli.find_session([s1], "1"), s1)


class ServerTests(Base):
    def test_fixed_content_types_and_non_ascii_token(self):
        httpd, app, url = server.start(0, roots=[self.claude])
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = url.split("/?")[0]
        with urllib.request.urlopen(base + "/app.js") as r:
            self.assertEqual(r.headers["Content-Type"], "text/javascript; charset=utf-8")
        with self.assertRaises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "/api/data?t=%C3%A9")
        self.assertEqual(err.exception.code, 403)

    def test_logs_reread_only_when_they_change(self):
        write_jsonl(self.claude / "p" / "a.jsonl", [claude_reply("m1", "2026-09-30T08:00:00Z")])
        app = server.Dashboard([self.claude])
        first = app.data()
        self.assertIs(app.data(), first)  # nothing changed: cached bytes
        write_jsonl(self.claude / "p" / "b.jsonl", [claude_reply("m2", "2026-09-30T09:00:00Z")])
        self.assertIsNot(app.data(), first)


if __name__ == "__main__":
    unittest.main()
