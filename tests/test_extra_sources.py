"""Tests for the readers in extra_sources.py and cursor_usage.py, with small sample files
shaped like each tool's own records."""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_token_tracker import core, cursor_usage, sources  # noqa: E402
from ai_token_tracker import extra_sources as extra  # noqa: E402
from isolation import isolate  # noqa: E402


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def jsonl(path: Path, records: list) -> Path:
    return write(path, "\n".join(json.dumps(r) for r in records) + "\n")


def db(path: Path, script: str, rows: list[tuple[str, tuple]] = ()) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(script)
    for sql, values in rows:
        con.execute(sql, values)
    con.commit()
    con.close()
    return path


def totals(sessions):
    u = core.Usage()
    for s in sessions:
        u.add(s.usage)
    return (u.input, u.output, u.cache_read, u.cache_write)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        isolate(self, self.tmp)
        self.home = self.tmp / "home"


class FileToolsTests(Base):
    def test_kimi_cli(self):
        write(self.home / ".kimi/config.toml",
              'default_model = "moonshot/k2"\n[models."moonshot/k2"]\nmodel = "kimi-k2-turbo"\n')
        status = lambda mid, n: {"timestamp": 1790000000, "message": {"type": "StatusUpdate", "payload": {  # noqa: E731
            "message_id": mid, "token_usage": {"input_other": n, "output": 10, "input_cache_read": 500,
                                               "input_cache_creation": 5}}}}
        jsonl(self.home / ".kimi/sessions/ws1/s1/wire.jsonl", [
            {"timestamp": 1790000000, "message": {"type": "TurnBegin", "payload": {"user_input": "fix the bug"}}},
            status("m1", 100), status("m1", 100), status("m2", 50)])
        [s] = extra.load_kimi_sessions()
        self.assertEqual(s.tool, "Kimi")
        self.assertEqual(s.title, "fix the bug")
        self.assertEqual(s.models, ["kimi-k2-turbo"])
        self.assertEqual(totals([s]), (150, 20, 1000, 10))  # the repeated message counts once

    def test_kimi_code(self):
        step = lambda uid, usage: {"type": "context.append_loop_event", "time": 1790000000000,  # noqa: E731
                                   "event": {"type": "step.end", "uuid": uid, "usage": usage}}
        jsonl(self.home / ".kimi-code/sessions/h/sess/agents/main/wire.jsonl", [
            {"type": "config.update", "modelAlias": "kimi-code/kimi-k2.6"},
            step("a", {"inputOther": 100, "output": 20, "inputCacheRead": 300, "inputCacheCreation": 0}),
            step("a", {"inputOther": 100, "output": 20, "inputCacheRead": 300, "inputCacheCreation": 0}),
            step("b", {"input_tokens": 400, "output_tokens": 10, "input_tokens_details": {"cached_tokens": 300}})])
        [s] = extra.load_kimi_sessions()
        self.assertEqual(s.models, ["kimi-k2.6"])
        self.assertEqual(totals([s]), (200, 30, 600, 0))

    def test_codebuddy_and_workbuddy(self):
        raw = {"prompt_tokens": 1000, "completion_tokens": 50, "prompt_tokens_details": {"cached_tokens": 600},
               "cache_creation_input_tokens": 100}
        jsonl(self.home / ".codebuddy/projects/proj/s1.jsonl", [
            {"type": "message", "role": "user", "content": [{"type": "text", "text": "add tests"}], "timestamp": 1790000000000},
            {"type": "message", "role": "assistant", "id": "r1", "timestamp": 1790000001000,
             "providerData": {"model": "deepseek-v3", "rawUsage": raw}},
            {"type": "message", "role": "assistant", "id": "r1", "providerData": {"rawUsage": raw}}])
        jsonl(self.home / ".workbuddy/projects/proj/s2.jsonl", [
            {"type": "function_call", "id": "x", "providerData": {"messageId": "x", "rawUsage": raw}}])
        jsonl(self.home / ".workbuddy/projects/proj/s2/subagents/agent-1.jsonl", [
            {"type": "message", "role": "assistant", "id": "y", "providerData": {"messageId": "y", "rawUsage": raw}}])
        [cb] = extra.load_codebuddy_sessions()
        self.assertEqual((cb.title, cb.models), ("add tests", ["deepseek-v3"]))
        self.assertEqual(totals([cb]), (300, 50, 600, 100))
        [wb] = extra.load_workbuddy_sessions()
        self.assertEqual(wb.session_id, "s2")
        self.assertEqual(len(wb.calls), 2)
        self.assertTrue(any(c.subagent for c in wb.calls))

    def test_pi_family_and_minimax(self):
        usage = {"input": 100, "output": 20, "cacheRead": 50, "cacheWrite": 5, "reasoningTokens": 7}
        jsonl(self.home / ".pi/agent/sessions/--home-me-app--/2026_x.jsonl", [
            {"type": "session", "id": "pi1", "cwd": "/home/me/app"},
            {"type": "message", "id": "u1", "message": {"role": "user", "content": "hello", "timestamp": 1790000000000}},
            {"type": "message", "id": "a1", "message": {"role": "assistant", "model": "claude-sonnet-4-5",
                                                        "usage": usage, "timestamp": 1790000001000}},
            {"type": "message", "id": "a1", "message": {"role": "assistant", "model": "claude-sonnet-4-5", "usage": usage}}])
        jsonl(self.home / ".omo/agent/sessions/--x--/1_omo.jsonl", [
            {"type": "message", "id": "b1", "message": {"role": "assistant", "model": "grok-4",
                                                        "usage": dict(usage, reasoning=7), "timestamp": 1790000000000}}])
        jsonl(self.home / ".minimax/v2/sessions/2026/10/01/10-00-00-000-session_1/messages.jsonl", [
            {"message_id": "m1", "message": {"role": "assistant", "model": "MiniMax-M2", "usage": usage,
                                             "timestamp": 1790000000000}}])
        pi = {s.tool: s for s in extra.load_pi_sessions()}
        self.assertEqual(pi["Pi"].project, "app")
        self.assertEqual(totals([pi["Pi"]]), (100, 27, 50, 5))   # reasoning added to output
        self.assertEqual(totals([pi["OmO"]]), (100, 20, 50, 5))  # OmO: reasoning is already in output
        self.assertEqual(totals(extra.load_minimax_sessions()), (100, 27, 50, 5))

    def test_craft_droid(self):
        header = {"id": "260430-swift", "model": "claude-sonnet-4-6", "lastMessageAt": 1790000000000, "name": "Refactor",
                  "tokenUsage": {"inputTokens": 1234, "outputTokens": 567, "cacheReadTokens": 5500,
                                 "cacheCreationTokens": 1100}}
        jsonl(self.home / ".craft-agent/workspaces/w1/sessions/s1/session.jsonl", [header, {"type": "message"}])
        [craft] = extra.load_craft_sessions()
        self.assertEqual((craft.title, totals([craft])), ("Refactor", (1234, 567, 5500, 1100)))

        settings = {"model": "custom:GLM-5.1-[Proxy]-0", "tokenUsage": {
            "inputTokens": 100, "outputTokens": 20, "cacheCreationTokens": 3, "cacheReadTokens": 40, "thinkingTokens": 5}}
        write(self.home / ".factory/sessions/proj/abc.settings.json", json.dumps(settings))
        jsonl(self.home / ".factory/sessions/proj/abc.jsonl", [
            {"type": "session_start", "cwd": "/home/me/site"},
            {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "ship it"}]}}])
        smaller = dict(settings, tokenUsage={"inputTokens": 1})
        write(self.home / ".factory/sessions/other/abc.settings.json", json.dumps(smaller))
        [droid] = extra.load_droid_sessions()
        self.assertEqual(droid.models, ["glm-5-1-0"])
        self.assertEqual((droid.project, droid.title), ("site", "ship it"))
        self.assertEqual(totals([droid]), (100, 25, 40, 3))  # the larger copy wins

    def test_grok(self):
        folder = self.home / ".grok/sessions/-home-me-app/s1"
        write(folder / "summary.json", json.dumps({"title": "Grok task", "model": "grok-4.5-build"}))
        done = {"params": {"update": {"sessionUpdate": "turn_completed", "usage": {
            "inputTokens": 1000, "cachedReadTokens": 800, "outputTokens": 60, "reasoningTokens": 10,
            "costUsdTicks": 25_000_000}}, "_meta": {"eventId": "e1", "timestampMs": 1790000000000}}}
        jsonl(folder / "updates.jsonl", [done, done])
        [s] = extra.load_grok_sessions()
        self.assertEqual((s.title, s.models), ("Grok task", ["grok-4.5-build"]))
        self.assertEqual(totals([s]), (200, 60, 800, 0))
        self.assertAlmostEqual(s.calls[0].reported_cost, 0.0025)

    def test_lmstudio(self):
        log = ('[2026-10-01 10:00:00] response: {\n  "id": "chatcmpl-1",\n  "created": 1790000000,\n'
               '  "model": "qwen3-8b",\n  "usage": {\n    "prompt_tokens": 120,\n    "completion_tokens": 30\n  }\n}\n'
               '[again] {"id": "chatcmpl-1", "model": "qwen3-8b", "usage": {"prompt_tokens": 120, "completion_tokens": 30}}\n')
        write(self.home / ".lmstudio/server-logs/2026-10/2026-10-01.1.log", log)
        [s] = extra.load_lmstudio_sessions()
        self.assertEqual(s.models, ["qwen3-8b"])
        self.assertEqual(totals([s]), (120, 30, 0, 0))
        self.assertEqual(s.calls[0].reported_cost, 0.0)


class DatabaseToolsTests(Base):
    def test_copilot(self):
        path = db(self.home / ".copilot/session-store.db",
                  "CREATE TABLE assistant_usage_events (id INTEGER PRIMARY KEY, session_id TEXT, model TEXT, "
                  "input_tokens INT, output_tokens INT, cache_read_tokens INT, cache_write_tokens INT, "
                  "reasoning_tokens INT, token_details_json TEXT, created_at TEXT);",
                  [("INSERT INTO assistant_usage_events VALUES (1,'s1','gpt-5',1000,50,600,100,0,NULL,'2026-10-01T10:00:00Z')", ()),
                   ("INSERT INTO assistant_usage_events VALUES (2,'s1','gpt-5',500,10,0,0,0,NULL,'1790000000000')", ())])
        write(self.home / ".copilot/session-state/s1/workspace.yaml", "summary: Fix login\ncwd: /home/me/web\n")
        [s] = extra.load_copilot_sessions()
        self.assertEqual((s.title, s.project), ("Fix login", "web"))
        self.assertEqual(totals([s]), (800, 60, 600, 100))
        self.assertTrue(path.exists())

    def test_goose_and_hermes(self):
        db(extra.goose_db(),
           "CREATE TABLE sessions (id TEXT, name TEXT, working_dir TEXT, model_config_json TEXT, created_at TEXT, "
           "accumulated_input_tokens INT, accumulated_output_tokens INT, accumulated_total_tokens INT);",
           [("INSERT INTO sessions VALUES ('g1','Goose job','/home/me/api','{\"model_name\":\"gpt-4o\"}',"
             "'2026-10-01 10:00:00',1000,200,1250)", ())])
        [g] = extra.load_goose_sessions()
        self.assertEqual((g.title, g.project, g.models), ("Goose job", "api", ["gpt-4o"]))
        self.assertEqual(totals([g]), (1000, 250, 0, 0))
        self.assertEqual(g.calls[0].timestamp.hour, 10)  # SQLite's naive time is UTC

        db(self.home / ".hermes/state.db",
           "CREATE TABLE sessions (id TEXT, model TEXT, started_at REAL, ended_at REAL, input_tokens INT, "
           "output_tokens INT, cache_read_tokens INT, cache_write_tokens INT, reasoning_tokens INT, "
           "message_count INT, title TEXT);",
           [("INSERT INTO sessions VALUES ('h1','claude-opus-4',1790000000.5,1790000100,10,20,30,40,5,3,'Plan trip')", ())])
        [h] = extra.load_hermes_sessions()
        self.assertEqual((h.title, totals([h])), ("Plan trip", (10, 25, 30, 40)))

    def test_zed_json_threads(self):
        thread = {"title": "Explain code", "model": {"provider": "zed.dev", "model": "claude-sonnet-4"},
                  "request_token_usage": {"r1": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100},
                                          "r2": {"input_tokens": 20, "output_tokens": 5}}}
        old = {"model": {"model": "gpt-4o"}, "cumulative_token_usage": {"input_tokens": 7, "output_tokens": 3}}
        con_path = extra.zed_db()
        db(con_path, "CREATE TABLE threads (id TEXT, summary TEXT, updated_at TEXT, data_type TEXT, data BLOB);",
           [("INSERT INTO threads VALUES ('t1','Explain code','2026-10-01T10:00:00Z','json',?)", (json.dumps(thread).encode(),)),
            ("INSERT INTO threads VALUES ('t2','','2026-10-01T11:00:00Z','json',?)", (json.dumps(old).encode(),)),
            ("INSERT INTO threads VALUES ('t3','','2026-10-01T11:00:00Z','zstd',?)", (b"\x00garbage",))])
        sessions = {s.session_id: s for s in extra.load_zed_sessions()}
        self.assertEqual(set(sessions), {"t1", "t2"})  # unreadable compressed rows are skipped
        self.assertEqual(totals([sessions["t1"]]), (30, 10, 100, 0))
        self.assertEqual(totals([sessions["t2"]]), (7, 3, 0, 0))

    def test_kiro_and_anythingllm(self):
        db(extra.kiro_dev_data() / "devdata.sqlite",
           "CREATE TABLE tokens_generated (id INTEGER PRIMARY KEY, model TEXT, provider TEXT, tokens_prompt INT, "
           "tokens_generated INT, timestamp TEXT);",
           [("INSERT INTO tokens_generated VALUES (1,'CLAUDE_SONNET_4_20250514_V1_0','kiro',100,20,'2026-10-01 10:00:00')", ()),
            ("INSERT INTO tokens_generated VALUES (2,'agent','kiro',50,5,'2026-10-01 11:00:00')", ())])
        [k] = extra.load_kiro_sessions()
        self.assertEqual(sorted(k.models), ["claude-sonnet-4", "kiro (model not logged)"])
        self.assertEqual(totals([k]), (150, 25, 0, 0))

        db(extra.anythingllm_db(),
           "CREATE TABLE workspace_chats (id INTEGER PRIMARY KEY, workspaceId INT, prompt TEXT, response TEXT, "
           "createdAt TEXT, thread_id INT);",
           [("INSERT INTO workspace_chats VALUES (1, 3, 'summarise pdf', ?, '2026-10-01T10:00:00Z', NULL)",
             (json.dumps({"text": "...", "metrics": {"prompt_tokens": 900, "completion_tokens": 80, "model": "llama3"}}),)),
            ("INSERT INTO workspace_chats VALUES (2, 3, 'old', '{\"text\":\"no metrics\"}', '2026-10-01T10:00:00Z', NULL)", ())])
        [a] = extra.load_anythingllm_sessions()
        self.assertEqual((a.title, a.models, totals([a])), ("summarise pdf", ["llama3"], (900, 80, 0, 0)))


class CursorTests(Base):
    CSV = ('Date,Kind,Model,Max Mode,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,'
           'Total Tokens,Cost\n'
           '"2026-10-01T10:00:00.000Z","Included","claude-4-sonnet","No","1200","1000","5000","300","6500","0.12"\n'
           '"2026-10-01T11:00:00.000Z","Included","gpt-5","No","100","100","0","10","110","$0.01"\n')

    def test_reads_cached_csv(self):
        write(cursor_usage.cache_file(), self.CSV)
        [s] = cursor_usage.load_cursor_sessions()
        self.assertEqual(s.tool, "Cursor")
        self.assertEqual(totals([s]), (1100, 310, 5000, 200))
        self.assertEqual(s.calls[0].app, "Cursor (Included)")

    def test_login_and_refresh(self):
        import base64
        payload = base64.urlsafe_b64encode(json.dumps({"sub": "auth0|user_ABC"}).encode()).decode().rstrip("=")
        token = f"h.{payload}.s"
        db(cursor_usage.cursor_app_dir() / "User" / "globalStorage" / "state.vscdb",
           "CREATE TABLE ItemTable (key TEXT, value TEXT);",
           [("INSERT INTO ItemTable VALUES ('cursorAuth/accessToken', ?)", (token,))])
        login = cursor_usage.read_login()
        self.assertEqual(login["cookie"], f"WorkosCursorSessionToken=user_ABC%3A%3A{token}")
        cursor_usage._last_try[0] = float("-inf")
        with mock.patch.object(cursor_usage, "_get", return_value=self.CSV.encode()) as get:
            self.assertTrue(cursor_usage.refresh())
            self.assertFalse(cursor_usage.refresh())  # fresh for 15 minutes
        get.assert_called_once()
        self.assertEqual(len(cursor_usage.load_cursor_sessions()), 1)

    def test_no_login_no_network(self):
        cursor_usage._last_try[0] = float("-inf")
        with mock.patch.object(cursor_usage, "_get") as get:
            self.assertFalse(cursor_usage.refresh(force=True))
        get.assert_not_called()


class RegistryTests(Base):
    def test_sources_page_and_signature_list_new_tools(self):
        names = {r["tool"] for r in sources.source_status([])}
        for name in ("Cursor", "GitHub Copilot", "Kiro", "Zed", "Goose", "Droid (Factory)", "Grok Build", "Kimi"):
            self.assertIn(name, names)
        before = sources.data_signature([])
        jsonl(self.home / ".grok/sessions/p/s/updates.jsonl", [{}])
        self.assertNotEqual(before, sources.data_signature([]))

    def test_one_broken_tool_does_not_hide_others(self):
        jsonl(self.home / ".kimi/sessions/w/s/wire.jsonl", [{"timestamp": 1790000000, "message": {
            "type": "StatusUpdate", "payload": {"message_id": "m", "token_usage": {"input_other": 5, "output": 1}}}}])
        with mock.patch.object(extra, "load_goose_sessions", side_effect=RuntimeError("boom")):
            tools = {s.tool for s in extra.load_all()}
        self.assertIn("Kimi", tools)


if __name__ == "__main__":
    unittest.main()
