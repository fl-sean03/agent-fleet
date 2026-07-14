"""Run records, health surface, alerting (ARCHITECTURE §4.4).

Every pipeline run is wrapped in `with Run("name") as run:` — the context manager writes a run
record, evaluates run-level success criteria, updates health.json, and notifies the main pane on
red/yellow. An unhandled exception still produces a (red) run record and re-raises, so the service
exits nonzero and systemd's OnFailure alert fires too. Silence is structurally impossible.
"""
import datetime
import json
import os
import subprocess
import time

from . import STATE

RUNS_DIR = f"{STATE}/runs"
HEALTH = f"{STATE}/health.json"
HOME = os.path.expanduser("~")

ERROR_RATE_RED = 0.20      # >20% of due units errored → red
ZERO_ANOMALY_MIN_UNITS = 8  # 0 outputs across >= this many due units → yellow


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def notify_main(msg):
    try:
        subprocess.run([f"{HOME}/.local/bin/agentctl", "send", "main", msg],
                       capture_output=True, timeout=20)
    except Exception:
        pass   # notification is best-effort; health.json + nonzero exit are the guarantees


class Run:
    def __init__(self, pipeline, dry=False):
        self.pipeline = pipeline
        self.dry = dry
        self.t0 = time.time()
        self.id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{pipeline}"
        self.counters = {}          # arbitrary counts: units_due, units_done, candidates, ...
        self.excluded = {}          # reason -> n  (pass to sessions.enumerate_sessions)
        self.errors = []            # [{unit, error}]
        self.capped = None
        self.notes = []

    # -- recording ---------------------------------------------------------
    def count(self, key, n=1):
        self.counters[key] = self.counters.get(key, 0) + n

    def error(self, unit, exc):
        self.errors.append({"unit": str(unit)[:80], "error": str(exc)[:300]})

    def cap(self, exc):
        self.capped = str(exc)[:300]

    def note(self, msg):
        self.notes.append(msg)

    # -- context manager ---------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, etype, evalue, tb):
        crashed = etype is not None and not isinstance(evalue, SystemExit)
        due = self.counters.get("units_due", 0)
        produced = self.counters.get("outputs", 0)
        if crashed:
            status, note = "red", f"crashed: {evalue!r}"[:200]
        elif due and len(self.errors) / max(due, 1) > ERROR_RATE_RED:
            status, note = "red", f"{len(self.errors)}/{due} units errored"
        elif self.capped:
            status, note = "yellow", f"capped mid-run (resumable): {self.capped[:80]}"
        elif due >= ZERO_ANOMALY_MIN_UNITS and produced == 0:
            status, note = "yellow", f"0 outputs across {due} due units (anomaly?)"
        else:
            status, note = "green", ""
        rec = {"run": self.id, "pipeline": self.pipeline, "dry": self.dry,
               "started": datetime.datetime.utcfromtimestamp(self.t0).isoformat() + "Z",
               "wall_s": round(time.time() - self.t0, 1), "status": status, "note": note,
               "counters": self.counters, "excluded": self.excluded,
               "errors": self.errors, "capped": self.capped, "notes": self.notes}
        os.makedirs(RUNS_DIR, exist_ok=True)
        with open(f"{RUNS_DIR}/{self.id}.json", "w") as fh:
            json.dump(rec, fh, indent=1)
        # observable outcome: pipelines `return run`, so callers can see a cap that
        # was handled INSIDE the pipeline — the 2026-07-10 alert-flood bug was the runner
        # tight-looping because a capped run returned indistinguishably from a clean one.
        self.status, self.final_note = status, note
        if not self.dry:
            fresh_episode = self._update_health(status, note)
            if status != "green" and fresh_episode:
                notify_main(f"[brain] {self.pipeline}: {status.upper()} — {note} "
                            f"(run {self.id}; `brain status` for detail)")
        return False   # never swallow the exception — the service must exit nonzero

    def _update_health(self, status, note):
        """Update health.json; returns whether this run STARTS a new alert episode. A capped run
        whose previous run was also capped is the SAME ongoing episode → at most one alert per
        episode reaches main (2026-07-10 flood: one alert per ~1.5s retry). Any green run closes
        the episode; red (crash/error-rate) always alerts."""
        try:
            with open(HEALTH) as fh:
                h = json.load(fh)
        except Exception:
            h = {}
        prev = h.get(self.pipeline, {})
        same_cap_episode = (bool(self.capped) and prev.get("capped_episode")
                            and prev.get("status") == status)
        e = dict(prev)
        e.update({"status": status, "note": note, "last_run": _now(), "run_id": self.id,
                  "counters": self.counters, "capped_episode": bool(self.capped)})
        if status == "green":
            e["last_ok"] = _now()
        h[self.pipeline] = e
        os.makedirs(os.path.dirname(HEALTH), exist_ok=True)
        tmp = HEALTH + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(h, fh, indent=1)
        os.replace(tmp, HEALTH)
        return not same_cap_episode


def health():
    try:
        with open(HEALTH) as fh:
            return json.load(fh)
    except Exception:
        return {}
