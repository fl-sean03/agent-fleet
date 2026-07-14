"""Stage 3 — FAILURES: failure→lesson extraction from the fleet's own mistakes (G3; ARCHITECTURE §7).

Hunts FAILURE ARCS in the agent's turns — error → investigation → root cause → fix/lesson — and
distills each completed arc into a generally-applicable rule, staged for consult to promote.
Distinct from review's lessons (operator-interaction-centric): the 0xcodez loop applied to ourselves.

Guarantees:
  - evidence is quote-verified against the conversation window (fabrications dropped, counted);
  - low-confidence arcs are dropped (autonomous-safety rule inherited from v1 harvest);
  - the checkpoint advances ONLY after every active workspace completed this run — a capped or
    errored run leaves it untouched, so no workspace is ever silently skipped.
"""
import datetime
import json
import os

from engine import STAGED, STATE
from engine import agentcall, runlog, sessions, store, transcripts
from engine.transcripts import norm

CKPT = f"{STATE}/ledger/failures-ckpt.json"      # {"since": iso} — this pipeline's own checkpoint
LESSONS_DIR = f"{STAGED}/lessons"
AGENT_CAP = 2600          # agent turns ARE the signal here — much larger than review's 1400
BUDGET = 120_000
DEFAULT_WINDOW_H = 24     # first-run window when no checkpoint exists

PROMPT = """You are the fleet's FAILURE ANALYST. Below is the actual conversation (operator + agent) \
from workspace '{ws}' since {since}. Hunt FAILURE ARCS in the AGENT'S OWN WORK: an error → \
investigation → root cause → fix or lesson. Look for: tool/command errors, test failures, reverts, \
wrong approaches later corrected, operator corrections of agent mistakes, work wasted by a bad assumption.

The HIGHEST-VALUE arc shape is owner-discovery: the operator notices something off in the agent's \
behavior or setup → questions it → root cause surfaces → a directive/fix lands. When you see that \
shape, the general_rule MUST capture the durable rule the directive implies (what every future agent \
should do differently), not just the local fix. Keep hunting after the first arc — cover the whole window.

For each COMPLETED arc (you can see both the failure and what was learned from it), distill ONE \
generally-applicable rule a future agent should follow.

Return ONLY a JSON array. Each item:
{{"failure":"<what went wrong, concretely>","root_cause":"<why it went wrong>","general_rule":"<the distilled, generally-applicable rule>","evidence":"<VERBATIM quote copied exactly from the conversation below>","workspace":"{ws}","confidence":"high|medium|low"}}

Rules: `evidence` MUST be an exact substring of the conversation below — do NOT invent or paraphrase \
quotes. Prefer a FEW sharp, real arcs over many shallow ones. Skip routine friction that taught \
nothing. An empty array [] is fine.

=== CONVERSATION ===
{convo}"""


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def _epoch(iso):
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _ckpt_read():
    try:
        with open(CKPT) as fh:
            return json.load(fh)["since"]
    except Exception:
        return None


def _ckpt_write(iso):
    os.makedirs(os.path.dirname(CKPT), exist_ok=True)
    tmp = CKPT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"since": iso}, fh)
    os.replace(tmp, CKPT)


def _to_lesson(it, ws):
    """Convert a verified failure arc to the common staged-lesson schema."""
    root = (it.get("root_cause") or "").strip()
    fail = (it.get("failure") or "").strip()
    how = f"Root cause: {root}" if root else ""
    if fail:
        how = (how + f" — watch for recurrence of: {fail}") if how else f"Watch for recurrence of: {fail}"
    return {"workspace": ws, "lesson": it["general_rule"].strip(), "type": "failure-fix",
            "evidence": (it.get("evidence") or "").strip(), "how_to_apply": how,
            "source": "failures", "confidence": it.get("confidence", "medium"),
            "date": datetime.date.today().isoformat()}


def _stage(lessons):
    """Merge into today's staged file, deduped by (workspace, normalized lesson). Atomic."""
    os.makedirs(LESSONS_DIR, exist_ok=True)
    path = f"{LESSONS_DIR}/{datetime.date.today().isoformat()}-failures.json"
    try:
        with open(path) as fh:
            cur = json.load(fh)
    except Exception:
        cur = []
    seen = {(c.get("workspace"), norm(c.get("lesson", ""))) for c in cur}
    added = 0
    for les in lessons:
        k = (les["workspace"], norm(les["lesson"]))
        if k in seen:
            continue
        seen.add(k)
        cur.append(les)
        added += 1
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(cur, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    return added, path


def run(dry=False, since=None):
    with runlog.Run("failures", dry=dry) as run:
        now_iso = _now_iso()
        since_iso = since or _ckpt_read() or (
            datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            - datetime.timedelta(hours=DEFAULT_WINDOW_H)).isoformat() + "Z"
        since_epoch = _epoch(since_iso)
        by_ws = {}                      # canonical workspace name -> [session paths]
        for meta in sessions.enumerate_sessions(run.excluded).values():
            if meta.mtime >= since_epoch:
                by_ws.setdefault(meta.workspace, []).append(meta.path)
        run.count("units_due", len(by_ws))
        if not by_ws:
            run.note(f"no workspace activity since {since_iso}")
        lessons, completed = [], True
        for ws in sorted(by_ws):
            convo, n_ops, n_ag = transcripts.conversation_window(
                by_ws[ws], since_epoch, budget=BUDGET, agent_cap=AGENT_CAP)
            if not convo.strip():
                run.count("units_empty")
                continue
            try:
                items = agentcall.call_json(
                    PROMPT.format(ws=ws, since=since_iso, convo=convo), shape=list)
            except agentcall.Capped as e:
                run.cap(e)
                completed = False
                break
            except agentcall.AgentFailure as e:
                run.error(ws, e)
                completed = False
                continue
            for it in items:
                if not isinstance(it, dict) or not (it.get("general_rule") or "").strip():
                    continue
                if it.get("confidence", "low") == "low":       # autonomous safety: drop low
                    run.count("low_confidence_dropped")
                    continue
                if not store.verify_quote(it.get("evidence", ""), convo):
                    run.count("evidence_rejected")              # fabricated quote — the hard gate
                    continue
                lessons.append(_to_lesson(it, ws))
            run.count("units_done")
        run.count("outputs", len(lessons))
        if lessons and not dry:
            added, path = _stage(lessons)
            run.count("staged_new", added)
            run.note(f"staged {added} lesson(s) → {os.path.basename(path)}")
        # never silently skip a workspace: advance only after ALL workspaces completed cleanly
        if completed and not run.errors and not dry:
            _ckpt_write(now_iso)
        else:
            run.note(f"checkpoint NOT advanced (still since={since_iso})")
        return run
