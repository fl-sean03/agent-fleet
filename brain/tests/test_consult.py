"""Unit tests for the consult / failures / state pipelines — NO real agent calls, no live stores.

Run:  cd brain && python3 tests/test_consult.py   (or python3 -m pytest tests/ -q)
agentcall.call_json is monkeypatched; all engine + pipeline paths are redirected into a tmpdir
(the test_engine.py pattern).
"""
import datetime
import glob
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import engine                          # noqa: E402
import engine.agentcall as AC          # noqa: E402
import engine.runlog as RL             # noqa: E402
import engine.sessions as SS           # noqa: E402
import engine.store as ST              # noqa: E402
from pipelines import consult, failures, state as state_p   # noqa: E402

QUOTE = "use worktrees for parallel edits to avoid clobbering the shared checkout"
TODAY = datetime.date.today().isoformat()


class Base(unittest.TestCase):
    """tmpdir-redirected engine + pipelines, with a dispatching fake agentcall.call_json."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self._saved = []

        def patch(mod, attr, val):
            self._saved.append((mod, attr, getattr(mod, attr)))
            setattr(mod, attr, val)

        patch(engine, "MEMORY_ROOT", f"{t}/memory")
        patch(ST, "MEMORY_ROOT", f"{t}/memory")
        patch(RL, "RUNS_DIR", f"{t}/runs")
        patch(RL, "HEALTH", f"{t}/health.json")
        patch(RL, "notify_main", lambda msg: None)
        patch(consult, "CAND_DIR", f"{t}/staged/candidates")
        patch(consult, "LESS_DIR", f"{t}/staged/lessons")
        patch(consult, "PROMOTED", f"{t}/staged/promoted.jsonl")
        patch(consult, "WRITES_LOG", f"{t}/staged/writes.jsonl")
        patch(consult, "SKILLS_DIR", f"{t}/skills")
        patch(failures, "CKPT", f"{t}/state/ledger/failures-ckpt.json")
        patch(failures, "LESSONS_DIR", f"{t}/staged/lessons")
        patch(state_p, "DAILY_DIR", f"{t}/reviews/daily")
        patch(SS, "LIVE_STORE", f"{t}/live")
        patch(SS, "ARCHIVE_STORE", f"{t}/arch")
        patch(SS, "DESC", f"{t}/desc")
        patch(SS, "ATTIC", f"{t}/attic")
        patch(AC, "call_json", self._fake_call)
        self.maker = self.verifier = self.analyst = None     # set per test
        self.maker_calls = self.verifier_calls = 0

    def tearDown(self):
        for mod, attr, val in reversed(self._saved):
            setattr(mod, attr, val)
        self.tmp.cleanup()

    def _fake_call(self, prompt, *, shape=list, **kw):
        if "FAILURE ANALYST" in prompt:
            return self.analyst(prompt)
        if "INDEPENDENT VERIFIER" in prompt:
            self.verifier_calls += 1
            return self.verifier(prompt)
        if "DISTILLER (maker)" in prompt:
            self.maker_calls += 1
            return self.maker(prompt)
        raise AssertionError(f"unexpected prompt: {prompt[:80]}")

    # -- staging helpers -----------------------------------------------------
    def stage_candidate(self, ws="ws-gamma", quote=QUOTE, **kw):
        os.makedirs(consult.CAND_DIR, exist_ok=True)
        d = {"type": "project", "topic": "worktrees",
             "statement": "Use worktrees for parallel edits", "why": "", "quote": quote,
             "turn_ts": "2026-07-08T12:00:00Z", "confidence": "high",
             "_session": "abcdef12", "_workspace": ws}
        d.update(kw)
        with open(f"{consult.CAND_DIR}/{ws}.jsonl", "a") as fh:
            fh.write(json.dumps(d) + "\n")
        return d

    def cand_lines(self, ws="ws-gamma"):
        f = f"{consult.CAND_DIR}/{ws}.jsonl"
        return [ln for ln in open(f)] if os.path.exists(f) else []

    def promoted_records(self):
        p = consult.PROMOTED
        return [json.loads(ln) for ln in open(p)] if os.path.exists(p) else []

    def mk_skill(self, name, n_bullets=0, desc="Orchestration rules for subordinate agents."):
        d = f"{consult.SKILLS_DIR}/{name}"
        os.makedirs(d, exist_ok=True)
        body = ["---", f"name: {name}", f"description: {desc}", "---", "", f"# {name}", "body text"]
        if n_bullets:
            body += ["", consult.SKILL_SECTION]
            body += [f"- existing rule {i} _(2026-07-01, from x)_" for i in range(n_bullets)]
        with open(f"{d}/SKILL.md", "w") as fh:
            fh.write("\n".join(body) + "\n")
        return f"{d}/SKILL.md"

    # -- batched-prompt helpers (array contract) -------------------------------
    @staticmethod
    def _embedded(prompt, marker):
        """Parse the JSON array a batched prompt embeds between `marker` and TARGET CONTEXT."""
        seg = prompt.split(marker, 1)[1].split("TARGET CONTEXT", 1)[0]
        return json.loads(seg[seg.index("["):].strip())

    def maker_items(self, prompt):
        return self._embedded(prompt, 'ITEMS (verified; each tagged "idx"):')

    def verifier_pairs(self, prompt):
        return self._embedded(prompt, "ITEMS AND PROPOSALS")

    def proposals(self, prompt, **fields):
        """One identical maker proposal per embedded item, idx carried through."""
        return [dict({"idx": it["idx"]}, **fields) for it in self.maker_items(prompt)]

    def verdicts(self, prompt, **fields):
        """One identical verifier verdict per embedded pair, idx carried through."""
        return [dict({"idx": pr["idx"]}, **fields) for pr in self.verifier_pairs(prompt)]

    def last_run_counters(self):
        f = sorted(glob.glob(f"{RL.RUNS_DIR}/*-consult.json"))[-1]
        return json.load(open(f))["counters"]


class TestConsult(Base):

    def test_evidence_gate_blocks_regardless_of_verdict(self):
        """(1) maker evidence not drawn from the item's verified quote → NOT applied."""
        self.stage_candidate()
        self.maker = lambda p: self.proposals(p, action="state_rule", target="ws-gamma",
                                              final_text="Always use worktrees.",
                                              evidence=["a quote the maker invented"], reason="r")
        self.verifier = lambda p: self.verdicts(p, verdict="promote", reason="looks fine")
        consult.run()
        self.assertNotIn("Always use worktrees", ST.state_read("ws-gamma").get("rules", ""))
        recs = self.promoted_records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["verdict"], "reject")
        self.assertIn("evidence gate", recs[0]["reason"])
        self.assertEqual(self.verifier_calls, 0)          # gated before the verifier is even asked
        self.assertEqual(self.cand_lines(), [])           # consumed (archived, removed from pool)

    def test_verifier_reject_archived_and_removed(self):
        """(2) verifier reject → archived with verdict, removed from pool, nothing written."""
        self.stage_candidate()
        self.maker = lambda p: self.proposals(p, action="state_rule", target="ws-gamma",
                                              final_text="Always use worktrees.",
                                              evidence=[QUOTE], reason="r")
        self.verifier = lambda p: self.verdicts(p, verdict="reject", reason="overreach")
        consult.run()
        self.assertNotIn("Always use worktrees", ST.state_read("ws-gamma").get("rules", ""))
        recs = self.promoted_records()
        self.assertEqual((recs[0]["verdict"], recs[0]["reason"]), ("reject", "overreach"))
        self.assertIsNone(recs[0]["applied"])
        self.assertEqual(self.cand_lines(), [])

    def test_skill_append_cap_leaves_item_staged(self):
        """(3) a full 15-bullet auto section → nothing written, item STAYS staged."""
        path = self.mk_skill("agent-orchestration", n_bullets=consult.SKILL_CAP)
        before = open(path).read()
        self.stage_candidate()
        self.maker = lambda p: self.proposals(p, action="skill_append",
                                              target="agent-orchestration",
                                              final_text="Brand-new general rule.",
                                              evidence=[QUOTE], reason="r")
        self.verifier = lambda p: self.verdicts(p, verdict="promote", reason="ok")
        consult.run()
        self.assertEqual(open(path).read(), before)                 # no auto-deletion, no growth
        self.assertEqual(len(self.cand_lines()), 1)                 # stays staged
        self.assertEqual(self.promoted_records(), [])               # not archived either

    def test_skill_append_writes_and_dedupes(self):
        """skill_append happy path: exact section format, then dedupe on rerun."""
        path = self.mk_skill("agent-orchestration", n_bullets=1)
        self.stage_candidate()
        self.maker = lambda p: self.proposals(p, action="skill_append",
                                              target="agent-orchestration",
                                              final_text="Brand-new general rule.",
                                              evidence=[QUOTE], reason="r")
        self.verifier = lambda p: self.verdicts(p, verdict="promote", reason="ok")
        consult.run()
        txt = open(path).read()
        self.assertIn(consult.SKILL_SECTION, txt)
        self.assertIn(f"- Brand-new general rule. _({TODAY}, from ws-gamma)_", txt)
        # rerun with the same rule → dedupe-skip, no second bullet
        self.stage_candidate()
        consult.run()
        self.assertEqual(open(path).read().count("Brand-new general rule."), 1)
        self.assertEqual(self.promoted_records()[-1]["applied"], "dedupe-skip")

    def test_state_rule_dedupe(self):
        """(4) a normalized-duplicate STATE bullet is skipped (item still consumed + archived)."""
        ST.state_write("ws-gamma", {"rules": "- Always use Worktrees for parallel edits _(2026-07-01, ws-gamma)_"})
        before = ST.state_read("ws-gamma")["rules"]
        self.stage_candidate()
        self.maker = lambda p: self.proposals(p, action="state_rule", target="ws-gamma",
                                              final_text="always use worktrees for parallel edits",
                                              evidence=[QUOTE], reason="r")
        self.verifier = lambda p: self.verdicts(p, verdict="promote", reason="ok")
        consult.run()
        self.assertEqual(ST.state_read("ws-gamma")["rules"], before)
        self.assertEqual(self.promoted_records()[0]["applied"], "dedupe-skip")
        self.assertEqual(self.cand_lines(), [])

    def test_promotion_cap_honored_mid_chunk(self):
        """(5) cap=1 with 3 same-workspace items in ONE chunk → exactly one promotion; the two
        unprocessed chunk items stay staged untouched (consumed stays consumed)."""
        for i in range(3):
            self.stage_candidate(topic=f"t{i}")
        self.maker = lambda p: [{"idx": it["idx"], "action": "state_rule", "target": "ws-gamma",
                                 "final_text": f"Rule number {it['idx']}.",
                                 "evidence": [QUOTE], "reason": "r"}
                                for it in self.maker_items(p)]
        self.verifier = lambda p: self.verdicts(p, verdict="promote", reason="ok")
        consult.run(cap=1)
        rules = ST.state_read("ws-gamma")["rules"]
        self.assertEqual(rules, f"- Rule number 0. _({TODAY}, ws-gamma)_")   # exact bullet format
        self.assertEqual(len(self.cand_lines()), 2)                       # 2 stay staged
        self.assertEqual(len(self.promoted_records()), 1)
        self.assertEqual(self.maker_calls, 1)       # ONE batched call covered all three items
        self.assertEqual(self.verifier_calls, 1)

    def test_promote_memory_happy_path(self):
        """promote → memory: file written to the workspace store, writes log + index updated."""
        self.stage_candidate()
        self.maker = lambda p: self.proposals(p, action="memory", target="ws-gamma",
                                              final_text="Use worktrees for parallel edits.",
                                              evidence=[QUOTE], reason="r")
        self.verifier = lambda p: self.verdicts(p, verdict="promote", reason="ok")
        consult.run()
        mem = f"{ST.MEMORY_ROOT}/ws-gamma/auto_project_worktrees.md"
        self.assertTrue(os.path.exists(mem))
        self.assertIn(QUOTE, open(mem).read())                            # provenance quote carried
        self.assertIn("auto_project_worktrees.md",
                      open(f"{ST.MEMORY_ROOT}/ws-gamma/MEMORY.md").read())
        self.assertTrue(os.path.exists(consult.WRITES_LOG))
        rec = self.promoted_records()[0]
        self.assertEqual((rec["verdict"], rec["applied"]), ("promote", "auto_project_worktrees.md"))

    def test_flag_contradiction_writes_discrepancy_memory(self):
        """flag + contradicts → flagged-discrepancy memory (v1 contradiction rule)."""
        self.stage_candidate()
        self.maker = lambda p: self.proposals(p, action="state_rule", target="ws-gamma",
                                              final_text="Use worktrees for parallel edits.",
                                              evidence=[QUOTE], reason="r")
        self.verifier = lambda p: self.verdicts(p, verdict="flag",
                                                reason="conflicts with existing",
                                                contradicts="auto_project_old.md")
        consult.run()
        mem = f"{ST.MEMORY_ROOT}/ws-gamma/auto_project_worktrees.md"
        self.assertIn("CONTRADICTION with [[auto_project_old.md]]", open(mem).read())
        self.assertNotIn("worktrees", ST.state_read("ws-gamma").get("rules", ""))  # no rule written
        self.assertEqual(self.promoted_records()[0]["verdict"], "flag")
        self.assertEqual(self.cand_lines(), [])

    def test_lessons_rank_before_medium_candidates(self):
        """pool ordering survives batching: within the chunk, the lesson precedes the
        medium-confidence candidate."""
        self.stage_candidate(confidence="medium")
        os.makedirs(consult.LESS_DIR, exist_ok=True)
        lesson = {"workspace": "ws-gamma", "lesson": "L", "type": "failure-fix",
                  "evidence": QUOTE, "how_to_apply": "h", "source": "failures"}
        json.dump([lesson], open(f"{consult.LESS_DIR}/x.json", "w"))
        seen = []
        def maker(p):
            for it in self.maker_items(p):
                seen.append("lesson" if it.get("type") == "failure-fix" else "candidate")
            return self.proposals(p, action="drop", target="", final_text="",
                                  evidence=[], reason="r")
        self.maker = maker
        consult.run()
        self.assertEqual(seen, ["lesson", "candidate"])
        self.assertFalse(os.path.exists(f"{consult.LESS_DIR}/x.json"))   # consumed → file removed

    def test_maker_omission_leaves_item_staged(self):
        """(new-1) an item the maker omits from its array stays staged — NEVER auto-dropped."""
        self.stage_candidate(topic="kept")
        self.stage_candidate(topic="omitted")
        def maker(p):
            items = self.maker_items(p)
            self.assertEqual(len(items), 2)      # both items were in the ONE batched call
            return [{"idx": items[0]["idx"], "action": "drop", "target": "",
                     "final_text": "", "evidence": [], "reason": "r"}]   # second item omitted
        self.maker = maker
        consult.run()
        lines = self.cand_lines()
        self.assertEqual(len(lines), 1)                       # omitted item stays staged
        self.assertIn("omitted", lines[0])
        self.assertEqual(len(self.promoted_records()), 1)     # only the drop was archived
        self.assertEqual(self.verifier_calls, 0)
        self.assertEqual(self.last_run_counters().get("maker_omitted"), 1)

    def test_verifier_omission_leaves_item_staged(self):
        """(new-2) an item the verifier omits a verdict for stays staged, nothing written."""
        self.stage_candidate()
        self.maker = lambda p: self.proposals(p, action="state_rule", target="ws-gamma",
                                              final_text="Always use worktrees.",
                                              evidence=[QUOTE], reason="r")
        self.verifier = lambda p: []                          # no verdict at all
        consult.run()
        self.assertEqual(len(self.cand_lines()), 1)           # stays staged
        self.assertEqual(self.promoted_records(), [])         # not archived, not consumed
        self.assertNotIn("Always use worktrees", ST.state_read("ws-gamma").get("rules", ""))
        self.assertEqual(self.last_run_counters().get("verifier_omitted"), 1)

    def test_maker_reason_never_reaches_verifier(self):
        """(new-3) structural separation: the maker's reasoning never appears in the verifier
        prompt — proposals are stripped to action/target/final_text/evidence before serialize."""
        self.stage_candidate()
        secret = "SECRET-MAKER-REASONING-a9f3"
        self.maker = lambda p: self.proposals(p, action="state_rule", target="ws-gamma",
                                              final_text="Always use worktrees.",
                                              evidence=[QUOTE], reason=secret)
        captured = []
        def verifier(p):
            captured.append(p)
            return self.verdicts(p, verdict="promote", reason="ok")
        self.verifier = verifier
        consult.run()
        self.assertEqual(len(captured), 1)
        self.assertNotIn(secret, captured[0])
        for pair in self.verifier_pairs(captured[0]):
            self.assertNotIn("reason", pair["proposal"])
        # ...but the maker reason IS still preserved in the audit trail
        self.assertEqual(self.promoted_records()[0]["maker_reason"], secret)

    def test_mixed_workspaces_chunked_separately(self):
        """(new-4) mixed-workspace pool → one chunk per workspace, each with its OWN context."""
        ST.state_write("alpha", {"rules": "- ALPHA-MARKER rule"})
        ST.state_write("beta", {"rules": "- BETA-MARKER rule"})
        self.stage_candidate(ws="alpha")
        self.stage_candidate(ws="beta")
        prompts = []
        def maker(p):
            prompts.append(p)
            return self.proposals(p, action="drop", target="", final_text="",
                                  evidence=[], reason="r")
        self.maker = maker
        consult.run()
        self.assertEqual(len(prompts), 2)          # never mixed into one chunk
        by_ws = {self.maker_items(p)[0]["_workspace"]: p for p in prompts}
        self.assertEqual(set(by_ws), {"alpha", "beta"})
        self.assertIn("ALPHA-MARKER", by_ws["alpha"])
        self.assertNotIn("BETA-MARKER", by_ws["alpha"])
        self.assertIn("BETA-MARKER", by_ws["beta"])
        self.assertNotIn("ALPHA-MARKER", by_ws["beta"])
        self.assertEqual(self.cand_lines("alpha") + self.cand_lines("beta"), [])  # both consumed

    def test_batch_chunking_sizes(self):
        """(new-5) a same-workspace group is processed in chunks of `batch` (10 @ 4 → 4,4,2)."""
        for i in range(10):
            self.stage_candidate(topic=f"t{i}")
        sizes = []
        def maker(p):
            sizes.append(len(self.maker_items(p)))
            return self.proposals(p, action="drop", target="", final_text="",
                                  evidence=[], reason="r")
        self.maker = maker
        consult.run(batch=4)
        self.assertEqual(sizes, [4, 4, 2])
        self.assertEqual(self.cand_lines(), [])    # all dropped → all consumed

    def test_malformed_staged_line_archived_without_call(self):
        """malformed item guard: a non-dict staged line is archived + consumed, no agent call."""
        os.makedirs(consult.CAND_DIR, exist_ok=True)
        with open(f"{consult.CAND_DIR}/ws-gamma.jsonl", "a") as fh:
            fh.write('"not a dict"\n')
        consult.run()      # maker/verifier fakes are None — any call would blow up
        recs = self.promoted_records()
        self.assertEqual(len(recs), 1)
        self.assertEqual((recs[0]["verdict"], recs[0]["reason"]),
                         ("reject", "engine: malformed item"))
        self.assertEqual(self.cand_lines(), [])
        self.assertEqual(self.maker_calls, 0)
        self.assertEqual(self.verifier_calls, 0)


class TestFailures(Base):

    def _mk_session(self, enc=None):
        enc = enc or (SS.INCLUDE_PREFIX + "-work-agents-ws-gamma")
        d = f"{SS.LIVE_STORE}/{enc}"
        os.makedirs(d, exist_ok=True)
        ts = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z"
        rows = [
            {"type": "user", "timestamp": ts,
             "message": {"content": [{"type": "text", "text": "please fix the failing deploy"}]}},
            {"type": "assistant", "timestamp": ts,
             "message": {"content": [{"type": "text", "text":
                "The deploy failed because the systemd unit lacked PATH; I exported "
                "~/.local/bin on PATH in the wrapper and the run succeeded."}]}},
            {"type": "assistant", "timestamp": ts,
             "message": {"content": [{"type": "text", "text": "padding " * 900}]}},
        ]
        with open(f"{d}/{'a' * 12}.jsonl", "w") as fh:
            fh.write("\n".join(json.dumps(r) for r in rows))
        return enc

    def test_fabricated_and_low_confidence_dropped(self):
        """(6) fabricated evidence + low confidence are dropped; the verified arc is staged."""
        enc = self._mk_session()
        self.analyst = lambda p: [
            {"failure": "deploy failed", "root_cause": "systemd PATH missing",
             "general_rule": "Export ~/.local/bin on PATH in systemd wrappers.",
             "evidence": "the systemd unit lacked PATH", "workspace": enc, "confidence": "high"},
            {"failure": "x", "root_cause": "y", "general_rule": "Fabricated rule.",
             "evidence": "this quote exists nowhere in the conversation",
             "workspace": enc, "confidence": "high"},
            {"failure": "z", "root_cause": "w", "general_rule": "Low-confidence rule.",
             "evidence": "the systemd unit lacked PATH", "workspace": enc, "confidence": "low"},
        ]
        failures.run()
        staged = json.load(open(f"{failures.LESSONS_DIR}/{TODAY}-failures.json"))
        self.assertEqual(len(staged), 1)
        les = staged[0]
        self.assertEqual(les["lesson"], "Export ~/.local/bin on PATH in systemd wrappers.")
        self.assertEqual((les["type"], les["source"], les["workspace"]),
                         ("failure-fix", "failures", enc))
        self.assertIn("systemd PATH missing", les["how_to_apply"])
        # all workspaces completed → checkpoint advanced
        self.assertIn("since", json.load(open(failures.CKPT)))

    def test_capped_does_not_advance_checkpoint(self):
        self._mk_session()
        def analyst(p):
            raise AC.Capped("usage limit reached")
        self.analyst = analyst
        failures.run()
        self.assertFalse(os.path.exists(failures.CKPT))


class TestStatePipeline(Base):

    def test_resume_and_failures_blocks_from_daily_json(self):
        """(7) latest daily json → resume/failures AUTO blocks (replace semantics)."""
        os.makedirs(state_p.DAILY_DIR, exist_ok=True)
        json.dump([{"workspace": "old", "headline": "stale"}],
                  open(f"{state_p.DAILY_DIR}/2026-07-07.json", "w"))
        json.dump([{"workspace": "ws-gamma", "headline": "Shipped X",
                    "advanced": ["a1", "a2", "a3", "a4"], "blocked": ["waiting on Y"],
                    "notable": []},
                   {"workspace": "ws-arc", "headline": "Quiet day", "advanced": [], "blocked": []}],
                  open(f"{state_p.DAILY_DIR}/2026-07-08.json", "w"))
        # pre-existing open-failures content must be REPLACED (current-state, not a log)
        ST.state_write("ws-arc", {"failures": "- ancient blocker"})
        state_p.run()
        got = ST.state_read("ws-gamma")
        self.assertTrue(got["resume"].startswith("2026-07-08: Shipped X"))
        self.assertIn("- a1", got["resume"])
        self.assertNotIn("- a4", got["resume"])                      # top-3 only
        self.assertEqual(got["failures"], "- waiting on Y")
        self.assertEqual(ST.state_read("ws-arc")["failures"], "(nothing yet)")   # replaced
        self.assertFalse(os.path.exists(f"{ST.MEMORY_ROOT}/old"))    # only the LATEST file is read

    def test_no_daily_json_exits_green(self):
        state_p.run()    # must not raise
        self.assertEqual(RL.health().get("state", {}).get("status"), "green")


if __name__ == "__main__":
    unittest.main(verbosity=2)
