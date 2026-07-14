"""Unit tests for the token-usage accounting system (engine/usagedb.py + pipelines/usage.py).

Pins the invariants that make the numbers trustworthy:
  - PRICES → cost math, and the SQL aggregate COST expression matches the python row_cost exactly;
  - msg-id dedup (streaming writes the SAME message.id on several jsonl lines — must count ONCE);
  - the CLIENT WALL holds in the scanner (confined workspace transcripts never enter the DB);
  - account attribution follows the swap timeline;
  - the engine-call log ingests as agent='brain-engine' (the deployed-sub-agent category).
No live claude calls, no real stores — everything is tmp + monkeypatched. Run: python3 tests/test_usage.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from engine import sessions, usagedb, runlog  # noqa: E402
from pipelines import usage as usage_p  # noqa: E402


def _asst_line(msg_id, model, ts, input_t=0, cread=0, cw5m=0, cw1h=0, out=0, sidechain=False):
    return json.dumps({
        "type": "assistant", "isSidechain": sidechain, "timestamp": ts,
        "message": {"id": msg_id, "model": model, "usage": {
            "input_tokens": input_t, "cache_read_input_tokens": cread,
            "cache_creation_input_tokens": cw5m + cw1h, "output_tokens": out,
            "cache_creation": {"ephemeral_5m_input_tokens": cw5m, "ephemeral_1h_input_tokens": cw1h},
            "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0}}}})


class CostMath(unittest.TestCase):
    def test_row_cost_fable(self):
        # 1M input + 1M output on fable = $10 + $50 = $60
        self.assertAlmostEqual(usagedb.row_cost("claude-fable-5", 1_000_000, 0, 0, 0, 1_000_000),
                               60.0, places=6)

    def test_cache_multipliers(self):
        # opus input rate $5/Mtok: 1M cache-read = 0.1*5 = $0.50; 1M cw1h = 2*5 = $10
        self.assertAlmostEqual(usagedb.row_cost("claude-opus-4-8", 0, 1_000_000, 0, 0, 0), 0.5, 6)
        self.assertAlmostEqual(usagedb.row_cost("claude-opus-4-8", 0, 0, 0, 1_000_000, 0), 10.0, 6)

    def test_unknown_model_is_free(self):
        self.assertEqual(usagedb.row_cost("<synthetic>", 999, 999, 999, 999, 999), 0.0)

    def test_sql_matches_python(self):
        """The aggregate COST_SQL must equal the sum of per-row row_cost — no drift between the two
        pricing paths."""
        db = usagedb.sqlite3.connect(":memory:")
        db.executescript(usagedb.SCHEMA)
        rows = [("m1", "claude-fable-5", 100, 200, 30, 40, 50),
                ("m2", "claude-opus-4-8", 1000, 5000, 0, 700, 300),
                ("m3", "claude-haiku-4-5", 10, 0, 0, 0, 5),
                ("m4", "<synthetic>", 9, 9, 9, 9, 9)]
        py = 0.0
        for mid, model, i, cr, c5, c1, o in rows:
            py += usagedb.row_cost(model, i, cr, c5, c1, o)
            db.execute("INSERT INTO usage(msg_id,model,input_tokens,cache_read_tokens,"
                       "cache_write_5m_tokens,cache_write_1h_tokens,output_tokens) "
                       "VALUES(?,?,?,?,?,?,?)", (mid, model, i, cr, c5, c1, o))
        sql = db.execute(f"SELECT SUM({usagedb.COST_SQL}) FROM usage").fetchone()[0]
        self.assertAlmostEqual(py, sql, places=9)


class AccountTimeline(unittest.TestCase):
    def test_attribution_picks_active_account(self):
        tl = [("2026-07-04T15:00:00Z", "host"),
              ("2026-07-06T12:00:00Z", "account-b"),
              ("2026-07-10T14:00:00Z", "account-a")]
        self.assertEqual(usagedb._account_at("2026-07-05T09:00:00Z", tl), "host")
        self.assertEqual(usagedb._account_at("2026-07-06T12:00:01Z", tl), "account-b")
        self.assertEqual(usagedb._account_at("2026-07-11T00:00:00Z", tl), "account-a")
        self.assertEqual(usagedb._account_at("2026-07-01T00:00:00Z", tl), "pre-log")

    def test_parse_complete_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = f"{tmp}/swap.log"
            with open(p, "w") as fh:
                fh.write("[2026-07-06T15:19:57Z] A: swapping x → account-b\n")
                fh.write("[2026-07-06T15:28:27Z] === swap-fleet → host COMPLETE ===\n")
                fh.write("noise line\n")
                fh.write("[2026-07-10T14:38:39Z] === swap-fleet → account-b COMPLETE ===\n")
            tl = usagedb.account_timeline(p)
            self.assertEqual(tl, [("2026-07-06T15:28:27Z", "host"),
                                  ("2026-07-10T14:38:39Z", "account-b")])


class ScanPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.live = f"{t}/live"
        self.arch = f"{t}/arch"
        os.makedirs(self.live); os.makedirs(self.arch)
        # redirect the pipeline's copied-in names + usagedb DB + runlog
        self._save = {}
        for mod, attr, val in [
            (usage_p, "LIVE_STORE", self.live), (usage_p, "ARCHIVE_STORE", self.arch),
            (usagedb, "USAGE_DIR", f"{t}/u"), (usagedb, "DB_PATH", f"{t}/u/usage.db"),
            (usagedb, "ENGINE_LOG", f"{t}/u/engine-calls.jsonl"),
            (usagedb, "SWAP_LOG", f"{t}/swap.log"),
            (runlog, "RUNS_DIR", f"{t}/runs"), (runlog, "HEALTH", f"{t}/health.json"),
            (runlog, "notify_main", lambda m: None)]:
            self._save[(mod, attr)] = getattr(mod, attr)
            setattr(mod, attr, val)
        with open(f"{t}/swap.log", "w") as fh:
            fh.write("[2026-07-06T00:00:00Z] === swap-fleet → account-b COMPLETE ===\n")
        # isolate scan roots to the tmp stores — never walk the real ~/work during a unit test
        self._save[(usage_p, "_scan_roots")] = usage_p._scan_roots
        usage_p._scan_roots = lambda: [(self.live, None, None), (self.arch, None, None)]

    def tearDown(self):
        for (mod, attr), val in self._save.items():
            setattr(mod, attr, val)
        self.tmp.cleanup()

    def _write_enc(self, enc, lines):
        d = f"{self.live}/{enc}"
        os.makedirs(d, exist_ok=True)
        with open(f"{d}/sess.jsonl", "w") as fh:
            fh.write("\n".join(lines) + "\n")

    def test_msg_id_dedup(self):
        """Streaming repeats the same message.id across content-block lines — count ONCE."""
        line = _asst_line("msg_dup", "claude-fable-5", "2026-07-07T00:00:00Z", out=100)
        self._write_enc(sessions.INCLUDE_PREFIX + "-work-x", [line, line, line])
        usage_p.run()
        db = usagedb.connect()
        n = db.execute("SELECT COUNT(*) FROM usage WHERE msg_id='msg_dup'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_client_usage_included_and_tagged(self):
        """A confined workspace's USAGE (token counts, never content) IS counted, tagged
        category='confined' — the content wall is unaffected."""
        self._write_enc(sessions.CONFINED_PREFIX + "example-confined",
                        [_asst_line("msg_client", "claude-opus-4-8", "2026-07-07T00:00:00Z", out=9)])
        self._write_enc(sessions.INCLUDE_PREFIX + "-work-x",
                        [_asst_line("msg_ok", "claude-opus-4-8", "2026-07-07T00:00:00Z", out=9)])
        usage_p.run()
        db = usagedb.connect()
        cat, ws = db.execute("SELECT category, workspace FROM usage WHERE msg_id='msg_client'").fetchone()
        self.assertEqual(cat, "confined")
        self.assertTrue(ws.startswith("confined workspace:"))
        self.assertEqual(db.execute("SELECT category FROM usage WHERE msg_id='msg_ok'").fetchone()[0],
                         "real")

    def test_junk_skipped(self):
        self._write_enc(sessions.INCLUDE_PREFIX + "-work-x-benchmark-runs",
                        [_asst_line("msg_junk", "claude-opus-4-8", "2026-07-07T00:00:00Z", out=9)])
        r = usage_p.run()
        db = usagedb.connect()
        ids = [x[0] for x in db.execute("SELECT msg_id FROM usage").fetchall()]
        self.assertNotIn("msg_junk", ids)
        self.assertGreaterEqual(r.counters.get("skipped_junk_files", 0), 1)

    def test_account_and_split_columns(self):
        self._write_enc(sessions.INCLUDE_PREFIX + "-work-x", [
            _asst_line("m_pre", "claude-fable-5", "2026-07-01T00:00:00Z", out=10),
            _asst_line("m_post", "claude-fable-5", "2026-07-08T00:00:00Z", cread=500, cw1h=200, out=10)])
        usage_p.run()
        db = usagedb.connect()
        self.assertEqual(db.execute("SELECT account FROM usage WHERE msg_id='m_pre'").fetchone()[0],
                         "pre-log")
        self.assertEqual(db.execute("SELECT account FROM usage WHERE msg_id='m_post'").fetchone()[0],
                         "account-b")
        cw1h = db.execute("SELECT cache_write_1h_tokens FROM usage WHERE msg_id='m_post'").fetchone()[0]
        self.assertEqual(cw1h, 200)

    def test_engine_log_ingest(self):
        os.makedirs(f"{self.tmp.name}/u", exist_ok=True)
        with open(usagedb.ENGINE_LOG, "w") as fh:
            fh.write(json.dumps({"msg_id": "engine-abc", "model": "claude-fable-5",
                                 "account": "account-b", "stage": "harvest", "ts": "2026-07-08T00:00:00Z",
                                 "usage": {"input_tokens": 100, "output_tokens": 50,
                                           "cache_read_input_tokens": 0,
                                           "cache_creation_input_tokens": 0}}) + "\n")
        usage_p.run()
        db = usagedb.connect()
        row = db.execute("SELECT agent,source,model FROM usage WHERE msg_id='engine-abc'").fetchone()
        self.assertEqual(row, ("brain-engine", "engine", "claude-fable-5"))

    def test_incremental_skips_unchanged(self):
        self._write_enc(sessions.INCLUDE_PREFIX + "-work-x",
                        [_asst_line("m1", "claude-fable-5", "2026-07-08T00:00:00Z", out=10)])
        r1 = usage_p.run()
        self.assertEqual(r1.counters.get("transcript_files_scanned", 0), 1)
        r2 = usage_p.run()   # nothing changed
        self.assertEqual(r2.counters.get("transcript_files_scanned", 0), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
