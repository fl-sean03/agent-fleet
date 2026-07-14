"""Unit tests for brain.pipelines — NO agent calls, NO live stores.

Run:  cd brain && python3 tests/test_pipelines.py   (or python3 -m pytest tests/ -q)
agentcall.call/call_json are monkeypatched with canned outputs; sessions/ledger/runlog/staging
paths are redirected into a tmpdir (same pattern as test_engine.py).
"""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from engine import agentcall, ledger, runlog, sessions   # noqa: E402
from pipelines import harvest as harvest_p                # noqa: E402
from pipelines import review as review_p                  # noqa: E402

TS = "2026-06-15T12:00:00Z"          # turn timestamps, inside every test window
SINCE = "2026-06-01T00:00:00"        # test review window start


def mk_session(store, enc, sid, texts, pad_to=6000):
    """A fake session jsonl: real user/assistant turns + an inert pad line to clear
    MIN_SESSION_BYTES without polluting parse_transcript output."""
    d = f"{store}/{enc}"
    os.makedirs(d, exist_ok=True)
    path = f"{d}/{sid}.jsonl"
    with open(path, "w") as fh:
        for role, text in texts:
            fh.write(json.dumps({"type": role, "timestamp": TS,
                                 "message": {"role": role,
                                             "content": [{"type": "text", "text": text}]}}) + "\n")
        n = fh.tell()
        if n < pad_to:
            fh.write(json.dumps({"type": "meta", "pad": "x" * (pad_to - n)}) + "\n")
    return path


class PipelineTestCase(unittest.TestCase):
    """Redirects every live path into a tmpdir; restores + un-patches on teardown."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self._saved = (sessions.LIVE_STORE, sessions.ARCHIVE_STORE, sessions.DESC, sessions.ATTIC,
                       ledger.LEDGER_DIR, runlog.RUNS_DIR, runlog.HEALTH, runlog.notify_main,
                       agentcall.call, agentcall.call_json, harvest_p.CAND_DIR,
                       review_p.DAILY, review_p.WEEKLY, review_p.LESSONS, review_p.CKPT,
                       review_p.git_since)
        sessions.LIVE_STORE = f"{t}/live"
        sessions.ARCHIVE_STORE = f"{t}/arch"
        sessions.DESC = f"{t}/desc"
        sessions.ATTIC = f"{t}/attic"
        ledger.LEDGER_DIR = f"{t}/ledger"
        runlog.RUNS_DIR = f"{t}/runs"
        runlog.HEALTH = f"{t}/health.json"
        runlog.notify_main = lambda msg: None            # no agentctl in unit tests
        harvest_p.CAND_DIR = f"{t}/staged/candidates"
        review_p.DAILY = f"{t}/reviews/daily"
        review_p.WEEKLY = f"{t}/reviews/weekly"
        review_p.LESSONS = f"{t}/staged/lessons"
        review_p.CKPT = f"{t}/ledger/review-ckpt.json"
        review_p.git_since = lambda root, since_iso: ""  # no real git in unit tests
        os.makedirs(sessions.DESC)
        os.makedirs(sessions.ATTIC)

    def tearDown(self):
        (sessions.LIVE_STORE, sessions.ARCHIVE_STORE, sessions.DESC, sessions.ATTIC,
         ledger.LEDGER_DIR, runlog.RUNS_DIR, runlog.HEALTH, runlog.notify_main,
         agentcall.call, agentcall.call_json, harvest_p.CAND_DIR,
         review_p.DAILY, review_p.WEEKLY, review_p.LESSONS, review_p.CKPT,
         review_p.git_since) = self._saved
        self.tmp.cleanup()


# ---------------------------------------------------------------- harvest
class TestHarvest(PipelineTestCase):
    ENC = sessions.INCLUDE_PREFIX + "-zzqa-alpha"      # no descriptor → canonical name is the full enc

    def test_quote_gate_drops_fabrications(self):
        sid = "s" * 8
        mk_session(sessions.LIVE_STORE, self.ENC, sid,
                   [("user", "please always use worktrees for parallel safety")])
        real = {"type": "feedback", "topic": "worktrees",
                "statement": "the operator wants parallel work done in git worktrees for safety.",
                "why": "safety", "quote": "always use worktrees for parallel safety",
                "turn_ts": TS, "confidence": "high"}
        fake = dict(real, topic="fabricated", quote="never use worktrees at all costs")
        agentcall.call_json = lambda prompt, **kw: [real, fake]
        harvest_p.run()
        lines = [json.loads(l) for l in open(f"{harvest_p.CAND_DIR}/{self.ENC}.jsonl")]
        self.assertEqual(len(lines), 1)                       # fabricated quote dropped
        self.assertEqual(lines[0]["topic"], "worktrees")
        self.assertEqual(lines[0]["_session"], sid)
        self.assertEqual(lines[0]["_workspace"], self.ENC)
        e = ledger.Ledger("harvest").status(sid)
        self.assertEqual(e["status"], "done")
        self.assertEqual(e["count"], 1)                       # ledger count == recounted staged lines

    def test_agentfailure_marks_error_then_unit_is_due_again(self):
        sid = "e" * 8
        mk_session(sessions.LIVE_STORE, self.ENC, sid,
                   [("user", "some real operator text about the run")])

        def boom(prompt, **kw):
            raise agentcall.AgentFailure("no parseable JSON list in reply", retryable=True)
        agentcall.call_json = boom
        harvest_p.run()
        led = ledger.Ledger("harvest")
        self.assertEqual(led.status(sid)["status"], "error")
        metas = sessions.enumerate_sessions()
        self.assertIn(sid, led.due({s: m.mtime for s, m in metas.items()}))   # error → retried
        agentcall.call_json = lambda prompt, **kw: []
        harvest_p.run()
        self.assertEqual(ledger.Ledger("harvest").status(sid)["status"], "empty")

    def test_capped_stops_without_marking_inflight(self):
        big, small = "b" * 8, "c" * 8
        mk_session(sessions.LIVE_STORE, self.ENC, big,
                   [("user", "big session with lots of text " * 300)], pad_to=20000)
        mk_session(sessions.LIVE_STORE, self.ENC, small, [("user", "small session text here")])
        calls = []

        def capped(prompt, **kw):
            calls.append(1)
            raise agentcall.Capped("usage limit reached")
        agentcall.call_json = capped
        harvest_p.run()
        self.assertEqual(len(calls), 1)                       # biggest-first, stopped at once
        led = ledger.Ledger("harvest")
        self.assertIsNone(led.status(big))                    # in-flight session NOT marked
        self.assertIsNone(led.status(small))                  # remaining session untouched
        self.assertEqual(runlog.health()["harvest"]["status"], "yellow")   # cap-pause, resumable

    def test_dry_stages_nothing_and_keeps_ledger_clean(self):
        sid = "d" * 8
        mk_session(sessions.LIVE_STORE, self.ENC, sid, [("user", "operator text for dry mode")])
        agentcall.call_json = lambda prompt, **kw: [
            {"type": "project", "topic": "t", "statement": "a statement long enough to keep",
             "why": "", "quote": "operator text for dry mode", "turn_ts": TS, "confidence": "high"}]
        harvest_p.run(dry=True)
        self.assertFalse(os.path.exists(f"{harvest_p.CAND_DIR}/{self.ENC}.jsonl"))
        self.assertIsNone(ledger.Ledger("harvest").status(sid))


# ---------------------------------------------------------------- review
class TestReview(PipelineTestCase):
    def _desc(self, fname, root):
        d = sessions.DESC if fname.endswith(".env") else sessions.ATTIC
        with open(f"{d}/{fname}", "w") as fh:
            fh.write(f'ROOT="{root}"\n')

    def test_two_encs_of_one_descriptor_group_into_one_unit(self):
        # renamed root: live descriptor + retired descriptor, same canonical name → ONE review
        self._desc("ws-alpha.env", "$HOME/zzqa-ws-alpha")
        self._desc("ws-alpha.env.retired-20260101", "$HOME/zzqa-WsAlpha")
        mk_session(sessions.LIVE_STORE, sessions.INCLUDE_PREFIX + "-zzqa-ws-alpha", "a" * 8,
                   [("user", "keep improving the science agent planner")])
        mk_session(sessions.LIVE_STORE, sessions.INCLUDE_PREFIX + "-zzqa-WsAlpha", "b" * 8,
                   [("user", "older transcripts from the renamed root")])
        calls = []

        def fake(prompt, **kw):
            calls.append(prompt)
            return {"workspace": "ws-alpha", "headline": "planner improved",
                    "advanced": ["planner refactor"], "blocked": [], "notable": [], "lessons": []}
        agentcall.call_json = fake
        review_p.run_daily(since=SINCE)
        self.assertEqual(len(calls), 1)                       # v1 reviewed this twice (per-enc)
        self.assertIn("keep improving the science agent planner", calls[0])
        self.assertIn("older transcripts from the renamed root", calls[0])   # both encs, one window
        today = datetime.date.today().isoformat()
        got = json.load(open(f"{review_p.DAILY}/{today}.json"))
        self.assertEqual([r["workspace"] for r in got], ["ws-alpha"])

    def test_fabricated_lesson_evidence_dropped(self):
        mk_session(sessions.LIVE_STORE, sessions.INCLUDE_PREFIX + "-zzqa-alpha", "d" * 8,
                   [("user", "always mark the ledger before retrying failed sessions")])
        good = {"lesson": "Mark the ledger before any retry", "type": "preference",
                "evidence": "mark the ledger before retrying failed sessions",
                "how_to_apply": "call mark_error before moving on"}
        bad = dict(good, evidence="the operator said to skip the ledger entirely")
        agentcall.call_json = lambda prompt, **kw: {
            "workspace": sessions.INCLUDE_PREFIX + "-zzqa-alpha", "headline": "h", "advanced": ["x"],
            "blocked": [], "notable": [], "lessons": [good, bad]}
        review_p.run_daily(since=SINCE)
        today = datetime.date.today().isoformat()
        staged = json.load(open(f"{review_p.LESSONS}/{today}.json"))
        self.assertEqual(len(staged), 1)                      # fabricated evidence dropped
        self.assertEqual(staged[0]["evidence"], good["evidence"])
        self.assertEqual(staged[0]["workspace"], sessions.INCLUDE_PREFIX + "-zzqa-alpha")
        self.assertEqual(staged[0]["how_to_apply"], good["how_to_apply"])

    def test_checkpoint_not_advanced_when_capped(self):
        os.makedirs(os.path.dirname(review_p.CKPT), exist_ok=True)
        with open(review_p.CKPT, "w") as fh:
            json.dump({"daily": SINCE}, fh)
        mk_session(sessions.LIVE_STORE, sessions.INCLUDE_PREFIX + "-zzqa-aaa", "f" * 8,
                   [("user", "real work happened in aaa today")])
        mk_session(sessions.LIVE_STORE, sessions.INCLUDE_PREFIX + "-zzqa-bbb", "g" * 8,
                   [("user", "real work happened in bbb today")])
        state = {"n": 0}

        def fake(prompt, **kw):
            state["n"] += 1
            if state["n"] == 2:                               # second (sorted) unit hits the cap
                raise agentcall.Capped("session limit reached")
            return {"workspace": "w", "headline": "ok", "advanced": ["z"],
                    "blocked": [], "notable": [], "lessons": []}
        agentcall.call_json = fake
        review_p.run_daily()
        self.assertEqual(json.load(open(review_p.CKPT))["daily"], SINCE)   # window NOT advanced
        self.assertEqual(runlog.health()["review"]["status"], "yellow")
        today = datetime.date.today().isoformat()
        self.assertTrue(os.path.exists(f"{review_p.DAILY}/{today}.json"))  # partial digest kept
        # a clean rerun (redoing the whole window) advances the checkpoint
        agentcall.call_json = lambda prompt, **kw: {
            "workspace": "w", "headline": "ok", "advanced": ["z"],
            "blocked": [], "notable": [], "lessons": []}
        review_p.run_daily()
        self.assertNotEqual(json.load(open(review_p.CKPT))["daily"], SINCE)

    def test_heartbeat_only_window_makes_no_agent_call(self):
        # no git, no operator turns, negligible agent text → pre-filtered, zero calls
        mk_session(sessions.LIVE_STORE, sessions.INCLUDE_PREFIX + "-zzqa-quiet", "h" * 8,
                   [("assistant", "No response requested.")])
        calls = []

        def fake(prompt, **kw):
            calls.append(1)
            return {}
        agentcall.call_json = fake
        review_p.run_daily(since=SINCE)
        self.assertEqual(calls, [])

    def test_weekly_synthesis(self):
        os.makedirs(review_p.DAILY, exist_ok=True)
        with open(f"{review_p.DAILY}/2026-07-08.md", "w") as fh:
            fh.write("# Fleet work review — 2026-07-08\n- ws-gamma advanced\n")
        agentcall.call = lambda prompt, **kw: "## Themes\n- ws-gamma momentum\n"
        review_p.run_weekly()
        wk = datetime.date.today().isoformat()
        txt = open(f"{review_p.WEEKLY}/{wk}.md").read()
        self.assertIn("ws-gamma momentum", txt)
        self.assertIn("weekly", json.load(open(review_p.CKPT)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
