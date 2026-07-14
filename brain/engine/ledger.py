"""Checkpoint ledgers with integrity (ARCHITECTURE §4.3).

One JSON file per pipeline under brain/state/ledger/. Per-unit state machine:
    done      unit fully processed AND its output artifact verified by the caller
    empty     processed, genuinely nothing found (distinct from error — the v1 silent-loss bug
              conflated "errored" with "done with 0 candidates")
    error     processing failed — ALWAYS retried on the next run
    excluded  permanently out of scope (confinement wall, junk) — never retried

Invariant enforced here: callers may only mark `done` via mark_done(), which requires the verified
output count; `error` entries always reappear in due().
"""
import datetime
import json
import os

from . import STATE

LEDGER_DIR = f"{STATE}/ledger"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z"


class Ledger:
    def __init__(self, pipeline):
        self.path = f"{LEDGER_DIR}/{pipeline}.json"
        try:
            with open(self.path) as fh:
                self.d = json.load(fh)
        except Exception:
            self.d = {}

    def _save(self):
        os.makedirs(LEDGER_DIR, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.d, fh, indent=1)
        os.replace(tmp, self.path)

    def status(self, unit_id):
        return self.d.get(unit_id)

    def mark_done(self, unit_id, *, mtime, output_count, run_id, **fields):
        """`output_count` is the caller's VERIFIED artifact count (e.g. staged lines written and
        re-counted) — passing it is what distinguishes done from empty; 0 means empty."""
        status = "done" if output_count > 0 else "empty"
        self.d[unit_id] = {"status": status, "mtime": mtime, "count": output_count,
                           "run": run_id, "ts": _now(), **fields}
        self._save()

    def mark_error(self, unit_id, *, mtime, error, run_id, **fields):
        prev = self.d.get(unit_id, {})
        self.d[unit_id] = {"status": "error", "mtime": mtime, "error": str(error)[:300],
                           "attempts": prev.get("attempts", 0) + 1,
                           "run": run_id, "ts": _now(), **fields}
        self._save()

    def mark_excluded(self, unit_id, *, reason, **fields):
        self.d[unit_id] = {"status": "excluded", "reason": reason, "ts": _now(), **fields}
        self._save()

    MAX_ATTEMPTS = 3   # poison-unit cap: beyond this, a unit stops burning calls nightly forever
                       # (2026-07-10 audit); it surfaces as gave-up in counts() until its mtime changes

    def due(self, units):
        """units: {unit_id: mtime}. A unit is due if: unseen, mtime changed since done/empty,
        or status==error with attempts < MAX_ATTEMPTS. `excluded` is never due; an error unit at
        the attempt cap becomes due again only when its transcript changes (fresh mtime)."""
        out = []
        for uid, mtime in units.items():
            e = self.d.get(uid)
            if e is None:
                out.append(uid)
            elif e["status"] == "error":
                if e.get("attempts", 1) < self.MAX_ATTEMPTS or e.get("mtime") != mtime:
                    out.append(uid)
            elif e["status"] in ("done", "empty") and e.get("mtime") != mtime:
                out.append(uid)
        return out

    def counts(self):
        c = {}
        for e in self.d.values():
            c[e["status"]] = c.get(e["status"], 0) + 1
        return c
