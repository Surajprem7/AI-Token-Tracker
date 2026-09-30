"""Tests for the OpenCode, Cline / Roo Code / Kilo Code and Qwen Code readers.

Sample data follows the formats in each tool's source code (OpenCode's session
SQL schema, Cline's MessageWithMetadata.metrics, the ClineApiReqInfo payload of
ui_messages.json, and Qwen Code's ui_telemetry records).
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_token_tracker import more_sources as more  # noqa: E402
from ai_token_tracker import pricing  # noqa: E402


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        env = {"XDG_DATA_HOME": str(self.tmp / "share"), "CLINE_DIR": str(self.tmp / "cline"),
               "QWEN_HOME": str(self.tmp / "qwen"), "AI_TOKEN_TRACKER_DIR": str(self.tmp / "att")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("CLINE_DATA_DIR", "CLINE_SESSION_DATA_DIR"):
            os.environ.pop(key, None)


ASSISTANT = {"role": "assistant", "modelID": "claude-sonnet-4-5", "providerID": "anthropic",
             "path": {"cwd": "/home/me/shop/src", "root": "/home/me/shop"}, "cost": 0.01,
             "tokens": {"input": 100, "output": 40, "reasoning": 10, "cache": {"read": 900, "write": 50}}}


class OpenCodeTests(Base):
    def make_db(self):
        db = self.tmp / "share" / "opencode" / "opencode.db"
        db.parent.mkdir(parents=True)
        con = sqlite3.connect(db)
        con.executescript("""
            CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT, slug TEXT,
                                  directory TEXT, title TEXT, version TEXT, time_created INTEGER);
            CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT);
            CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT);
        """)
        con.execute("INSERT INTO session VALUES ('s1','p','', 'a','/home/me/shop','Fix checkout','1',1)")
        con.execute("INSERT INTO session VALUES ('s2','p','s1','b','/home/me/shop','Explore helper','1',2)")
        con.execute("INSERT INTO session VALUES ('s3','p','', 'c','/home/me/shop','Forked copy','1',3)")
        rows = [
            ("m1", "s1", {"role": "user", "time": {"created": 1759200000000}}),
            ("m2", "s1", dict(ASSISTANT, time={"created": 1759200001000})),
            ("m3", "s2", dict(ASSISTANT, time={"created": 1759200002000}, tokens={"input": 5, "output": 5, "reasoning": 0, "cache": {"read": 0, "write": 0}})),
            # s3 is a fork: the same reply copied under a new id must not be counted twice
            ("m4", "s3", dict(ASSISTANT, time={"created": 1759200001000})),
        ]
        for mid, sid, data in rows:
            con.execute("INSERT INTO message VALUES (?,?,?,?)", (mid, sid, 0, json.dumps(data)))
        con.execute("INSERT INTO part VALUES ('p1','m1','s1',0,?)", (json.dumps({"type": "text", "text": "fix the checkout bug"}),))
        con.commit()
        con.close()

    def test_database(self):
        self.make_db()
        sessions = more.load_opencode_sessions()
        self.assertEqual(len(sessions), 1)  # sub-agent folded into its parent, fork copy dropped
        s = sessions[0]
        self.assertEqual((s.tool, s.title, s.project), ("OpenCode", "Fix checkout", "shop"))
        self.assertEqual([t.prompt for t in s.turns], ["fix the checkout bug", "[sub-agent] Explore helper"])
        u = s.turns[0].usage
        self.assertEqual((u.input, u.output, u.cache_read, u.cache_write), (100, 50, 900, 50))
        self.assertTrue(s.turns[1].calls[0].subagent)

    def test_legacy_files(self):
        storage = self.tmp / "share" / "opencode" / "storage"
        write(storage / "session" / "proj" / "ses_1.json", json.dumps({"id": "ses_1", "title": "Old session", "directory": "/w/app"}))
        write(storage / "message" / "ses_1" / "msg_1.json", json.dumps({"id": "msg_1", "sessionID": "ses_1", "role": "user", "time": {"created": 1}}))
        write(storage / "message" / "ses_1" / "msg_2.json", json.dumps(dict(ASSISTANT, id="msg_2", sessionID="ses_1", time={"created": 2})))
        write(storage / "part" / "msg_1" / "prt_1.json", json.dumps({"type": "text", "text": "hello"}))
        [s] = more.load_opencode_sessions()
        self.assertEqual((s.title, s.turns[0].prompt, s.usage.total), ("Old session", "hello", 1100))


class ClineTests(Base):
    def test_cline_app_sessions(self):
        d = self.tmp / "cline" / "data" / "sessions" / "task-1"
        write(d / "task-1.json", json.dumps({"cwd": "/home/me/api", "title": "Add auth"}))
        write(d / "task-1.messages.json", json.dumps({"version": 1, "messages": [
            {"role": "user", "content": [{"type": "text", "text": "add auth"}], "ts": 1759200000000},
            {"role": "assistant", "content": "ok", "ts": 1759200001000,
             "modelInfo": {"id": "claude-sonnet-4-5", "provider": "anthropic"},
             "metrics": {"inputTokens": 1000, "outputTokens": 80, "cacheReadTokens": 700, "cacheWriteTokens": 200, "cost": 0.004}},
            {"role": "assistant", "content": "no metrics yet", "ts": 1759200002000},
        ]}))
        [s] = more.load_cline_sessions()
        self.assertEqual((s.tool, s.project, s.title), ("Cline", "api", "Add auth"))
        u = s.usage
        self.assertEqual((u.input, u.output, u.cache_read, u.cache_write), (100, 80, 700, 200))

    def test_vscode_extension_tasks(self):
        user_dir = self.tmp / "Code" / "User"
        info = {"request": "...", "tokensIn": 12, "tokensOut": 300, "cacheWrites": 500, "cacheReads": 4000, "cost": 0.0123}
        write(user_dir / "globalStorage" / "rooveterinaryinc.roo-cline" / "tasks" / "t1" / "ui_messages.json", json.dumps([
            {"ts": 1759200000000, "type": "say", "say": "task", "text": "write a README"},
            {"ts": 1759200001000, "type": "say", "say": "api_req_started", "text": json.dumps(info)},
            {"ts": 1759200002000, "type": "say", "say": "text", "text": "done"},
            {"ts": 1759200003000, "type": "say", "say": "user_feedback", "text": "shorter please"},
            {"ts": 1759200004000, "type": "say", "say": "api_req_started", "text": json.dumps(dict(info, tokensIn=3))},
        ]))
        [s] = more.load_vscode_extension_tasks([user_dir])
        self.assertEqual((s.tool, s.title), ("Roo Code", "write a README"))
        self.assertEqual([t.prompt for t in s.turns], ["write a README", "shorter please"])
        self.assertEqual(s.calls[0].app, "Roo Code (Code)")
        # No model in the log, so the tool's own cost figure is used
        pricing.apply_costs([s])
        self.assertAlmostEqual(s.usage.cost, 0.0246)
        self.assertEqual(s.usage.unpriced, 0)


class QwenTests(Base):
    def test_qwen_transcript(self):
        def ev(inp, out, cached, thoughts, auth):
            return {"type": "system", "subtype": "ui_telemetry", "timestamp": "2026-09-30T10:00:05Z", "sessionId": "q1",
                    "systemPayload": {"uiEvent": {"event.name": "qwen-code.api_response", "event.timestamp": "2026-09-30T10:00:05Z",
                                                  "model": "qwen3-coder-plus", "input_token_count": inp, "output_token_count": out,
                                                  "cached_content_token_count": cached, "thoughts_token_count": thoughts,
                                                  "auth_type": auth}}}
        lines = [
            {"type": "user", "timestamp": "2026-09-30T10:00:00Z", "sessionId": "q1", "cwd": "/home/me/cli",
             "provenance": "real_user", "message": {"role": "user", "parts": [{"text": "refactor utils"}]}},
            ev(1000, 50, 600, 20, "qwen-oauth"),   # thoughts already inside output
            ev(500, 10, 0, 7, "gemini-api-key"),  # thoughts separate
            {"type": "system", "subtype": "ui_telemetry", "systemPayload": {"uiEvent": {"event.name": "qwen-code.tool_call"}}},
        ]
        write(self.tmp / "qwen" / "projects" / "-home-me-cli" / "chats" / "q1.jsonl", "\n".join(json.dumps(x) for x in lines))
        write(self.tmp / "qwen" / "projects" / "-home-me-cli" / "chats" / "q1.ledger.jsonl", "{}")
        [s] = more.load_qwen_sessions()
        self.assertEqual((s.tool, s.project, s.title, len(s.calls)), ("Qwen Code", "cli", "refactor utils", 2))
        u = s.usage
        self.assertEqual((u.input, u.cache_read, u.output), (400 + 500, 600, 50 + 17))


if __name__ == "__main__":
    unittest.main()
