"""Skills, cache savings, resume commands, git commits per session and the context size check."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_token_tracker import context, core, outcomes, pricing, server, sources  # noqa: E402
from isolation import isolate  # noqa: E402


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def line(**kw) -> str:
    return json.dumps(kw)


class SkillsAndSavingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        isolate(self, self.tmp)

    def transcript(self) -> Path:
        usage = {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 1_000_000}
        msg = lambda content: {"id": "msg_1", "model": "claude-sonnet-4-5", "usage": usage, "content": content}  # noqa: E731
        return write(self.tmp / "projects/-home-me-app/s1.jsonl", "\n".join([
            line(type="user", sessionId="s1", timestamp="2026-10-01T10:00:00Z", message={"content": "make a pdf"}),
            # one response written as two lines (one per content block), with two skills
            line(type="assistant", timestamp="2026-10-01T10:00:05Z",
                 message=msg([{"type": "tool_use", "name": "Skill", "input": {"skill": "pdf"}}])),
            line(type="assistant", timestamp="2026-10-01T10:00:05Z",
                 message=msg([{"type": "tool_use", "name": "Skill", "input": {"skill": "brand-voice"}},
                              {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}])),
        ]))

    def test_skills_are_collected_once_per_response(self):
        s = core.load_session(self.transcript(), "app")
        self.assertEqual(len(s.calls), 1)
        self.assertEqual(s.calls[0].skills, ("pdf", "brand-voice"))
        pricing.apply_costs([s])
        payload = server.build_payload([s], [])
        skills = {k["name"]: k for k in payload["skills"]}
        self.assertEqual(set(skills), {"pdf", "brand-voice"})
        self.assertEqual(skills["pdf"]["uses"], 1)
        self.assertAlmostEqual(skills["pdf"]["cost"] * 2, s.usage.cost, places=6)  # cost split between the two

    def test_cache_savings(self):
        s = core.load_session(self.transcript(), "app")
        pricing.apply_costs([s])
        inp, _, read, _, _ = pricing.lookup("claude-sonnet-4-5")
        self.assertAlmostEqual(s.usage.saved, inp - read, places=6)  # 1M cached tokens
        self.assertEqual(server.build_payload([s], [])["sessions"][0]["turns"][0]["c"][0][10], round(inp - read, 6))


class ResumeTests(unittest.TestCase):
    def session(self, tool, sid):
        return core.Session(session_id=sid, project="p", path=Path("x"), tool=tool)

    def test_commands(self):
        self.assertEqual(server.resume_command(self.session("Claude Code", "0b1c-2d")), "claude --resume 0b1c-2d")
        self.assertEqual(server.resume_command(self.session("Codex CLI", "abc")), "codex resume abc")
        self.assertIsNone(server.resume_command(self.session("Gemini CLI", "abc")))

    def test_unsafe_ids_are_never_offered(self):
        for sid in ("a b", "x;rm -rf ~", "$(evil)", "-flag", "agent-123", ""):
            self.assertIsNone(server.resume_command(self.session("Claude Code", sid)), sid)


class CodexTitleTests(unittest.TestCase):
    def test_thread_name_is_used_as_title(self):
        tmp = Path(tempfile.mkdtemp())
        isolate(self, tmp, CODEX_HOME=str(tmp / "codex"))
        write(tmp / "codex/sessions/2026/10/01/rollout-1.jsonl", "\n".join([
            line(type="session_meta", timestamp="2026-10-01T10:00:00Z", payload={"id": "t-1", "cwd": "/x"}),
            line(type="turn_context", timestamp="2026-10-01T10:00:00Z", payload={"model": "gpt-5"}),
            line(type="event_msg", timestamp="2026-10-01T10:00:00Z", payload={"type": "user_message", "message": "hi"}),
            line(type="event_msg", timestamp="2026-10-01T10:00:01Z", payload={"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 10, "output_tokens": 5}, "last_token_usage": {"input_tokens": 10, "output_tokens": 5}}}),
        ]))
        write(tmp / "codex/session_index.jsonl", line(id="t-1", thread_name="Fix the  login page"))
        [s] = sources.load_codex_sessions()
        self.assertEqual(s.title, "Fix the login page")


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class CommitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        isolate(self, self.tmp)
        outcomes._cache.clear()
        outcomes._roots.clear()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "me@example.com")
        self.git("config", "user.name", "Me")

    def git(self, *args, when=None):
        env = dict(os.environ)
        if when:
            env.update(GIT_AUTHOR_DATE=when.isoformat(), GIT_COMMITTER_DATE=when.isoformat())
        subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, env=env)

    def commit(self, msg, when, email="me@example.com"):
        self.git("-c", f"user.email={email}", "commit", "-q", "--allow-empty", "-m", msg, when=when)

    def session(self, sid, times):
        s = core.Session(session_id=sid, project="repo", path=Path("x"), cwd=str(self.repo))
        s.turns.append(core.Turn(prompt="p", timestamp=times[0], calls=[
            core.ApiCall(t, "m", core.Usage(input=1)) for t in times]))
        return s

    def test_commits_link_to_the_one_active_session(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        t0 = now - timedelta(days=2)
        a = self.session("a", [t0, t0 + timedelta(minutes=20)])
        b = self.session("b", [t0 + timedelta(hours=5), t0 + timedelta(hours=5, minutes=10)])
        c = self.session("c", [t0 + timedelta(hours=5, minutes=5)])  # overlaps b
        self.commit("during a", t0 + timedelta(minutes=10))
        self.commit("just after a", t0 + timedelta(minutes=30))
        self.commit("between sessions", t0 + timedelta(hours=2))
        self.commit("during b and c", t0 + timedelta(hours=5, minutes=6))
        self.commit("someone else", t0 + timedelta(minutes=15), email="other@example.com")
        linked = outcomes.link_commits([a, b, c])
        self.assertEqual([x["subject"] for x in linked[0]], ["just after a", "during a"])
        self.assertNotIn(1, linked)  # ambiguous: not guessed
        self.assertNotIn(2, linked)

    def test_resumed_session_does_not_collect_the_gap(self):
        t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=3)
        s = self.session("a", [t0, t0 + timedelta(days=1)])
        self.commit("in the gap", t0 + timedelta(hours=8))
        self.assertEqual(outcomes.link_commits([s]), {})

    def test_not_a_repo(self):
        s = core.Session(session_id="x", project="p", path=Path("x"), cwd=str(self.tmp / "nope"))
        s.turns.append(core.Turn(prompt="", timestamp=None, calls=[core.ApiCall(datetime.now(timezone.utc), "m", core.Usage(input=1))]))
        self.assertEqual(outcomes.link_commits([s]), {})


class ContextTests(unittest.TestCase):
    def test_report(self):
        tmp = Path(tempfile.mkdtemp())
        isolate(self, tmp)
        home = tmp / "home"
        write(home / ".claude/CLAUDE.md", "x" * 400)
        write(home / ".claude/skills/pdf/SKILL.md", "---\nname: pdf\ndescription: " + "y" * 80 + "\n---\nbody " * 500)
        proj = tmp / "proj"
        write(proj / "CLAUDE.md", "z" * 4000)
        write(proj / ".cursor/rules/a.mdc", "w" * 40)
        r = context.report([str(proj), str(proj), str(tmp / "missing")])
        self.assertEqual([g["label"] for g in r["global"]][0], "CLAUDE.md")
        self.assertEqual(r["global"][0]["tokens"], 100)
        self.assertIn("1 installed skills", r["global"][1]["label"])
        self.assertLess(r["global"][1]["tokens"], 60)  # only the name and description count
        [p] = r["projects"]
        self.assertEqual(p["tokens"], 1010)
        self.assertEqual(context.estimate_tokens("日本語abcd"), 4)


if __name__ == "__main__":
    unittest.main()
