"""Tests for the per-tool readers, using small hand-written log files.

The sample lines follow the formats the tools write today (Codex CLI rollout
files, Gemini CLI chat logs, Claude Code transcripts). Run: python -m unittest
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_token_tracker import core, sources  # noqa: E402


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        env = {
            "CODEX_HOME": str(self.tmp / "codex"),
            "GEMINI_CLI_HOME": str(self.tmp / "ghome"),
            "AI_TOKEN_TRACKER_DIR": str(self.tmp / "custom"),
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)


class CodexTests(Base):
    META = {"timestamp": "2026-09-30T08:00:00Z", "type": "session_meta",
            "payload": {"id": "codex-1", "cwd": "/home/me/shop", "originator": "codex_cli_rs",
                        "cli_version": "0.50.0", "git": {"branch": "dev"}}}
    CTX = {"timestamp": "2026-09-30T08:00:01Z", "type": "turn_context",
           "payload": {"model": "gpt-5-codex", "cwd": "/home/me/shop"}}

    def prompt(self, text, ts):
        return {"timestamp": ts, "type": "event_msg", "payload": {"type": "user_message", "message": text}}

    def test_token_usage_records(self):
        rec = lambda rid, inp, cached, out, ts: {  # noqa: E731
            "timestamp": ts, "type": "token_usage_record",
            "payload": {"response_id": rid, "usage": {
                "input_tokens": inp, "cached_input_tokens": cached, "cache_write_input_tokens": 0,
                "output_tokens": out, "reasoning_output_tokens": 5, "total_tokens": inp + out}}}
        write_jsonl(self.tmp / "codex/sessions/2026/09/30/rollout-a.jsonl", [
            self.META, self.CTX, self.prompt("fix checkout", "2026-09-30T08:00:02Z"),
            rec("r1", 1000, 800, 50, "2026-09-30T08:00:05Z"),
            rec("r1", 1000, 800, 50, "2026-09-30T08:00:05Z"),  # duplicate response
            rec("r2", 1200, 1000, 70, "2026-09-30T08:00:09Z"),
            # running totals are ignored when per-response records exist
            {"timestamp": "2026-09-30T08:00:09Z", "type": "event_msg", "payload": {
                "type": "token_count", "info": {"total_token_usage": {"input_tokens": 99999}}}},
        ])
        [s] = sources.load_codex_sessions()
        self.assertEqual((s.tool, s.session_id, s.project, s.branch), ("Codex CLI", "codex-1", "shop", "dev"))
        self.assertEqual(s.title, "fix checkout")
        self.assertEqual(len(s.calls), 2)
        u = s.usage
        self.assertEqual((u.input, u.cache_read, u.output), (400, 1800, 120))
        self.assertEqual(s.models, ["gpt-5-codex"])
        self.assertEqual(s.app, "Codex CLI (terminal)")

    def test_running_totals(self):
        tc = lambda inp, cached, out, ts: {  # noqa: E731
            "timestamp": ts, "type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out},
                "last_token_usage": {}}}}
        write_jsonl(self.tmp / "codex/archived_sessions/rollout-b.jsonl", [
            self.META, self.CTX, self.prompt("one", "2026-09-30T08:00:02Z"),
            tc(1000, 0, 100, "2026-09-30T08:00:05Z"),
            tc(1000, 0, 100, "2026-09-30T08:00:06Z"),  # repeated snapshot
            self.prompt("two", "2026-09-30T08:01:00Z"),
            tc(2500, 900, 180, "2026-09-30T08:01:05Z"),
            {"timestamp": "x", "type": "event_msg", "payload": {"type": "token_count", "info": None}},
        ])
        [s] = sources.load_codex_sessions()
        self.assertEqual([t.prompt for t in s.turns], ["one", "two"])
        self.assertEqual([len(t.calls) for t in s.turns], [1, 1])
        u = s.usage
        self.assertEqual((u.input, u.cache_read, u.output), (1600, 900, 180))


class GeminiTests(Base):
    def test_jsonl_log_and_legacy_json(self):
        proj = self.tmp / "ghome/.gemini/tmp/my-api"
        proj.mkdir(parents=True)
        (proj / ".project_root").write_text("/home/me/my-api\n")
        msg = {"id": "g1", "timestamp": "2026-09-30T09:00:03Z", "type": "gemini",
               "content": "sure", "model": "gemini-2.5-pro"}
        write_jsonl(proj / "chats/session-2026-09-30T09-00-abcd1234.jsonl", [
            {"sessionId": "gem-1", "projectHash": "h", "startTime": "2026-09-30T09:00:00Z"},
            {"id": "u1", "timestamp": "2026-09-30T09:00:01Z", "type": "user", "content": [{"text": "add a route"}]},
            msg,  # written first without tokens...
            dict(msg, tokens={"input": 1000, "output": 40, "cached": 600, "thoughts": 10, "tool": 5, "total": 1055}),
            {"$set": {"lastUpdated": "2026-09-30T09:00:04Z", "summary": "Add API route"}},
            {"$rewindTo": "g1"},  # hidden from the chat, but tokens were spent
        ])
        (proj / "chats/session-old.json").write_text(json.dumps({
            "sessionId": "gem-0", "projectHash": "h", "messages": [
                {"id": "a", "timestamp": "2026-09-29T09:00:01Z", "type": "user", "content": "hello"},
                {"id": "b", "timestamp": "2026-09-29T09:00:02Z", "type": "gemini", "model": "gemini-2.5-flash",
                 "tokens": {"input": 10, "output": 5, "cached": 0, "thoughts": 0, "tool": 0, "total": 15}}]}))
        sessions = {s.session_id: s for s in sources.load_gemini_sessions()}
        self.assertEqual(set(sessions), {"gem-1", "gem-0"})
        s = sessions["gem-1"]
        self.assertEqual((s.tool, s.project, s.title), ("Gemini CLI", "my-api", "Add API route"))
        u = s.usage
        self.assertEqual((u.input, u.cache_read, u.output, len(s.calls)), (405, 600, 50, 1))
        self.assertEqual(sessions["gem-0"].usage.total, 15)


class CustomLogTests(Base):
    def test_add_and_csv(self):
        sources.log_usage("ChatGPT", "gpt-5", 1200, 300, prompt="draft email", session="s1")
        sources.log_usage("ChatGPT", "gpt-5", 800, 100, session="s1")
        folder = sources.custom_dir()
        (folder / "ollama.csv").write_text(
            "timestamp,model,prompt_tokens,completion_tokens\n"
            "2026-09-30T10:00:00Z,llama3.1,500,200\n", encoding="utf-8")
        sessions = {(s.tool, s.session_id): s for s in sources.load_custom_sessions()}
        chat = sessions[("ChatGPT", "s1")]
        self.assertEqual((chat.usage.input, chat.usage.output, len(chat.calls)), (2000, 400, 2))
        self.assertEqual(chat.title, "draft email")
        ollama = next(s for (tool, _), s in sessions.items() if tool == "ollama")
        self.assertEqual(ollama.usage.total, 700)
        self.assertEqual(core.model_family("llama3.1"), "Llama")


class AllToolsTests(Base):
    def test_load_sessions_merges_everything(self):
        claude = self.tmp / "claude/projects/-home-me-web"
        write_jsonl(claude / "c1.jsonl", [
            {"type": "user", "cwd": "/home/me/web", "entrypoint": "cli", "timestamp": "2026-09-30T07:00:00Z",
             "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "timestamp": "2026-09-30T07:00:01Z", "message": {
                "id": "m1", "model": "claude-opus-5-5",
                "usage": {"input_tokens": 3, "output_tokens": 7, "cache_creation_input_tokens": 0,
                          "cache_read_input_tokens": 0}}},
        ])
        sources.log_usage("Grok", "grok-4", 10, 10)
        sessions = core.load_sessions([self.tmp / "claude/projects"])
        self.assertEqual({s.tool for s in sessions}, {"Claude Code", "Grok"})
        self.assertEqual([s.tool for s in core.load_sessions([self.tmp / "claude/projects"], tool_filter="claude")],
                         ["Claude Code"])
        groups = core.breakdown(sessions, core.BY_TOOL)
        self.assertEqual(groups["Claude Code"].usage.total, 10)


if __name__ == "__main__":
    unittest.main()
