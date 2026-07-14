"""Unit tests for brain.engine — the pure-python parts (no agent calls, no live stores).

Run:  cd brain && python3 -m pytest tests/ -q   (or python3 tests/test_engine.py)
Live paths are redirected into a tmpdir via monkeypatching module constants.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import engine                      # noqa: E402
from engine import transcripts     # noqa: E402


class TestScopeWall(unittest.TestCase):
    """The confinement wall + junk filters, exercised through a fake store layout."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import engine.sessions as S
        self.S = S
        self._orig = (S.LIVE_STORE, S.ARCHIVE_STORE, S.DESC, S.ATTIC)
        S.LIVE_STORE = f"{self.tmp.name}/live"
        S.ARCHIVE_STORE = f"{self.tmp.name}/arch"
        S.DESC = f"{self.tmp.name}/desc"
        S.ATTIC = f"{self.tmp.name}/attic"
        os.makedirs(S.DESC)

    def tearDown(self):
        (self.S.LIVE_STORE, self.S.ARCHIVE_STORE, self.S.DESC, self.S.ATTIC) = self._orig
        self.tmp.cleanup()

    def _mk(self, enc, sid, size=5000):
        d = f"{self.S.LIVE_STORE}/{enc}"
        os.makedirs(d, exist_ok=True)
        with open(f"{d}/{sid}.jsonl", "w") as fh:
            fh.write("x" * size)

    def test_client_wall_and_junk(self):
        self._mk(self.S.INCLUDE_PREFIX + "-work-platform-ws-gamma", "a" * 8)
        self._mk(self.S.CONFINED_PREFIX + "example-confined", "b" * 8)   # THE leak enc — must be excluded
        self._mk(self.S.INCLUDE_PREFIX + "-work-x-scratchpad-foo", "c" * 8)  # junk
        self._mk(self.S.INCLUDE_PREFIX + "-work-research-tiny", "d" * 8, size=100)  # too small
        counters = {}
        got = self.S.enumerate_sessions(counters)
        self.assertEqual(len(got), 1)
        self.assertEqual(next(iter(got.values())).enc, self.S.INCLUDE_PREFIX + "-work-platform-ws-gamma")
        self.assertEqual(counters.get("confined-wall"), 1)
        self.assertEqual(counters.get("junk"), 1)
        self.assertEqual(counters.get("too-small"), 1)

    def test_forward_identity_not_last_segment(self):
        with open(f"{self.S.DESC}/ws-beta.env", "w") as fh:
            fh.write('ROOT="$HOME/work/research/ws-beta-shear"\n')
        ws = self.S.workspace_of(self.S.INCLUDE_PREFIX + "-work-research-ws-beta-shear")
        self.assertEqual(ws.name, "ws-beta")          # descriptor name, not "shear"
        unk = self.S.workspace_of(self.S.INCLUDE_PREFIX + "-work-unknown-thing")
        self.assertEqual(unk.name, self.S.INCLUDE_PREFIX + "-work-unknown-thing")  # full enc, never last-segment


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import engine.ledger as L
        self.L = L
        self._orig = L.LEDGER_DIR
        L.LEDGER_DIR = f"{self.tmp.name}/ledger"

    def tearDown(self):
        self.L.LEDGER_DIR = self._orig
        self.tmp.cleanup()

    def test_state_machine(self):
        led = self.L.Ledger("t")
        units = {"s1": 100, "s2": 100, "s3": 100}
        self.assertEqual(sorted(led.due(units)), ["s1", "s2", "s3"])
        led.mark_done("s1", mtime=100, output_count=3, run_id="r1")
        led.mark_done("s2", mtime=100, output_count=0, run_id="r1")   # → empty, distinct from error
        led.mark_error("s3", mtime=100, error="boom", run_id="r1")
        led2 = self.L.Ledger("t")   # reload from disk
        self.assertEqual(led2.status("s1")["status"], "done")
        self.assertEqual(led2.status("s2")["status"], "empty")
        self.assertEqual(led2.due(units), ["s3"])                     # error ALWAYS retried
        self.assertEqual(led2.due({"s1": 200}), ["s1"])               # mtime change → due again
        led2.mark_excluded("s3", reason="confined-wall")
        self.assertEqual(self.L.Ledger("t").due(units), [])           # excluded never due

    def test_error_attempts_accumulate(self):
        led = self.L.Ledger("t2")
        led.mark_error("u", mtime=1, error="e1", run_id="r")
        led.mark_error("u", mtime=1, error="e2", run_id="r")
        self.assertEqual(led.status("u")["attempts"], 2)

    def test_poison_unit_attempt_cap(self):
        led = self.L.Ledger("t3")
        for i in range(3):
            self.assertIn("u", led.due({"u": 1}))        # retried while attempts < cap
            led.mark_error("u", mtime=1, error=f"e{i}", run_id="r")
        self.assertEqual(led.due({"u": 1}), [])          # gave up at cap
        self.assertIn("u", led.due({"u": 2}))            # fresh transcript mtime → due again


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import engine.store as St
        self.St = St
        self._orig = engine.MEMORY_ROOT, St.MEMORY_ROOT
        engine.MEMORY_ROOT = St.MEMORY_ROOT = f"{self.tmp.name}/memory"

    def tearDown(self):
        engine.MEMORY_ROOT, self.St.MEMORY_ROOT = self._orig
        self.tmp.cleanup()

    def test_quote_gate(self):
        src = "The  operator said:\n  use worktrees for parallel safety."
        self.assertTrue(self.St.verify_quote("use worktrees for  parallel safety", src))
        self.assertFalse(self.St.verify_quote("never use worktrees", src))
        self.assertFalse(self.St.verify_quote("", src))

    def test_state_roundtrip_preserves_agent_text(self):
        self.St.state_write("wsx", {"rules": "- rule one"})
        p = self.St.state_path("wsx")
        txt = open(p).read()
        # agent adds free text outside AUTO blocks
        open(p, "w").write(txt + "\n## Agent scratch\nmy own notes\n")
        self.St.state_write("wsx", {"rules": "- rule one\n- rule two", "resume": "2026-07-09: x"})
        final = open(p).read()
        self.assertIn("- rule two", final)
        self.assertIn("my own notes", final)                 # preserved
        got = self.St.state_read("wsx")
        self.assertEqual(got["rules"], "- rule one\n- rule two")
        self.assertEqual(got["resume"], "2026-07-09: x")

    def test_auto_memory_requires_evidence(self):
        mem = {"name": "x", "type": "project", "statement": "s", "description": "d"}
        self.assertIsNone(self.St.write_auto_memory("wsy", mem, provenance=[]))
        got = self.St.write_auto_memory("wsy", mem, provenance=[("a quote", None)])
        self.assertEqual(got, "auto_project_x.md")
        idx = open(f"{self.St.store_dir('wsy')}/MEMORY.md").read()
        self.assertIn("auto_project_x.md", idx)

    def test_memory_index_pointer_added_once_state_exists(self):
        self.St.write_auto_memory("wsz", {"name": "y", "type": "project", "statement": "s",
                                          "description": "d"}, provenance=[("q", None)])
        self.St.state_write("wsz", {"facts": "- f"})
        idx = open(f"{self.St.store_dir('wsz')}/MEMORY.md").read()
        self.assertIn("Read [STATE.md](STATE.md) before working", idx)
        self.assertEqual(idx.count("Read [STATE.md]"), 1)
        self.St.state_write("wsz", {"facts": "- f2"})
        idx = open(f"{self.St.store_dir('wsz')}/MEMORY.md").read()
        self.assertEqual(idx.count("Read [STATE.md]"), 1)     # idempotent


class TestTranscripts(unittest.TestCase):
    def test_operator_detection(self):
        self.assertTrue(transcripts.is_real_operator("please fix the bug"))
        self.assertFalse(transcripts.is_real_operator("<system-reminder>x</system-reminder>"))
        self.assertFalse(transcripts.is_real_operator("Caveat: The messages below were generated"))
        self.assertFalse(transcripts.is_real_operator(
            "This session is being continued from a previous conversation"))

    def test_chunking_covers_everything(self):
        turns = [("user", "2026-01-01T00:00:00Z", "A" * 100_000),
                 ("assistant", "2026-01-01T00:01:00Z", "B" * 100_000)]
        chs = transcripts.chunks_from_turns(turns)
        self.assertGreater(len(chs), 1)
        joined = "".join(chs)
        self.assertIn("A" * 1000, joined)
        self.assertIn("B" * 1000, joined)



class TestVerifierRegressions(unittest.TestCase):
    """Defects found by the 2026-07-09 independent verification pass."""

    def test_resub_escape_safe_state_write(self):
        import engine.store as St
        import engine as E
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            orig = E.MEMORY_ROOT, St.MEMORY_ROOT
            E.MEMORY_ROOT = St.MEMORY_ROOT = f"{tmp}/memory"
            try:
                evil = r"- use \s in regex and \g<0> groups _(2026-07-09, ws-gpu)_"
                St.state_write("wsr", {"rules": evil})
                St.state_write("wsr", {"rules": evil + "\n- second"})   # replace pass, not create
                got = St.state_read("wsr")["rules"]
                self.assertIn(r"\s in regex", got)
                self.assertIn(r"\g<0>", got)
                self.assertEqual(got.count(r"\g<0>"), 1)   # no silent duplication
            finally:
                E.MEMORY_ROOT, St.MEMORY_ROOT = orig

    def test_gpu_tests_not_junk_but_test_dirs_are(self):
        import engine.sessions as S
        self.assertFalse(S.is_junk_enc(S.INCLUDE_PREFIX + "-work-compute-ws-gpu-tests"))
        self.assertTrue(S.is_junk_enc(S.INCLUDE_PREFIX + "-work-foo-test"))
        self.assertTrue(S.is_junk_enc(S.INCLUDE_PREFIX + "-work-foo-test-bar"))
        self.assertTrue(S.is_junk_enc(S.INCLUDE_PREFIX + "-work-x-scratchpad-y"))

    def test_descriptor_map_marks_clients(self):
        import engine.sessions as S
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            orig = S.DESC, S.ATTIC
            S.DESC, S.ATTIC = f"{tmp}/desc", f"{tmp}/attic"
            os.makedirs(S.DESC)
            open(f"{S.DESC}/example-confined.env", "w").write(f'ROOT="{S.CONFINED_ROOT}/example-confined"\n')
            open(f"{S.DESC}/ws-gamma.env", "w").write(f'ROOT="{S.HOME}/work/platform/ws-gamma"\n')
            try:
                m = S.descriptor_map()
                kinds = {w.name: w.kind for w in m.values()}
                self.assertEqual(kinds["example-confined"], "confined")
                self.assertEqual(kinds["ws-gamma"], "project")
            finally:
                S.DESC, S.ATTIC = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
