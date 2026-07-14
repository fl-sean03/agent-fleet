"""Stage 5 — STATE: refresh STATE.md resume/failures AUTO blocks from the latest daily review
(G2; ARCHITECTURE §5). Deterministic — NO agent calls.

Per reviewed workspace: "## Last session" gets `<date>: <headline>` + the top advanced bullets;
"## Open failures" is REPLACED with the review's blocked items (it is current-state, not a log).
Content inside AUTO blocks only — agent-authored text outside them is preserved by store.state_write.
"""
import glob
import json
import os

from engine import REVIEWS
from engine import runlog, store

DAILY_DIR = f"{REVIEWS}/daily"
MAX_ADVANCED = 3     # top advanced bullets carried into the resume pointer


def run(dry=False, only_ws=None):
    with runlog.Run("state", dry=dry) as run:
        files = sorted(glob.glob(f"{DAILY_DIR}/*.json"))
        if not files:
            run.note("no daily review json yet — nothing to refresh")
            return run
        latest = files[-1]
        date = os.path.basename(latest)[:-5]
        try:
            with open(latest) as fh:
                reviews = json.load(fh)
        except Exception as e:
            run.error(latest, e)
            raise
        for r in reviews if isinstance(reviews, list) else []:
            ws = (r.get("workspace") or "").strip()
            if not ws or (only_ws and ws != only_ws):
                continue
            run.count("units_due")
            head = (r.get("headline") or "").strip()
            resume = [f"{date}: {head}" if head else date]
            resume += [f"- {a}" for a in (r.get("advanced") or [])[:MAX_ADVANCED]]
            failures = "\n".join(f"- {b}" for b in (r.get("blocked") or []))
            store.state_write(ws, {"resume": "\n".join(resume), "failures": failures}, dry=dry)
            run.count("outputs")
        run.note(f"refreshed from {os.path.basename(latest)}")
        return run
