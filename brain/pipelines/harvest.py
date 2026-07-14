"""Harvest pipeline — stage 1 (ARCHITECTURE §2): mine the operator's durable decisions / feedback /
references from every in-scope fleet transcript into staged/candidates/<workspace>.jsonl.

Port of memory-harvest's EXTRACT+stage (prompt kept verbatim). v1's synth/promote step is NOT here —
promotion belongs to the consult pipeline (maker/verifier). Integrity rules this module enforces:
  - a session is marked done ONLY after the staged lines are re-counted (the ledger's output_count);
  - staging happens after ALL of a session's chunks extracted — a mid-session failure stages nothing,
    so the error-retry next run cannot duplicate candidates;
  - AgentFailure → mark_error + continue (retried next run); Capped → checkpoint and STOP, the
    in-flight session is left unmarked (resumable);
  - dry mode: extraction runs, but no staging and no ledger writes.
"""
import json
import os

from engine import STAGED, agentcall, ledger, runlog, sessions, store, transcripts

CAND_DIR = f"{STAGED}/candidates"

# v1 EXTRACT_PROMPT, verbatim (memory-harvest/harvest.py — proven in production).
EXTRACT_PROMPT = """You are a memory-extraction worker for a persistent AI-agent system. Below is a chunk of a Claude Code agent conversation from workspace '{ws}' (session {sid}). Extract ONLY durable, reusable LEARNINGS that should persist across future sessions.

Types:
- project: ongoing work state, goals, DECISIONS, constraints NOT derivable from code/git.
- feedback: guidance the USER gave on how the agent should work (corrections, confirmed approaches). Include the WHY.
- reference: pointers to external resources (URLs, dashboards, tickets, important file paths).

STRICT RULES (a violation makes the item useless):
- Every item MUST include `quote`: a verbatim string copied EXACTLY (character-for-character) from the transcript below, that is the direct evidence. If you cannot copy an exact quote, DO NOT emit the item.
- Do NOT emit: ephemeral chatter, one-off task mechanics, restating code/git, anything not grounded in a quote, or anything already obviously permanent knowledge.
- Prefer the USER's own words for decisions and feedback.
- Keep `statement` self-contained and specific; convert relative dates to absolute using the turn timestamp.

Return ONLY a JSON array, no prose. Each item:
{{"type":"project|feedback|reference","topic":"<short-kebab-slug>","statement":"<the durable fact, 1-3 sentences>","why":"<for feedback: the reason; else \\"\\">","quote":"<exact verbatim excerpt from the chunk below>","turn_ts":"<ISO timestamp of the nearest turn>","confidence":"high|medium|low"}}

If nothing durable is present, return [].

TRANSCRIPT CHUNK:
<<<
{chunk}
>>>"""


def _extract(ws, sid, chunk):
    """One chunk → verified candidates (v1 filters kept: dict shape, verbatim quote gate, no
    low-confidence, known type, substantive statement). Capped/AgentFailure propagate to run()."""
    items = agentcall.call_json(EXTRACT_PROMPT.format(ws=ws, sid=sid, chunk=chunk), shape=list)
    good = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if not store.verify_quote(it.get("quote", ""), chunk):   # anti-fabrication: quote must exist
            continue
        if it.get("confidence", "low") == "low":                 # autonomous safety: no low-confidence
            continue
        if it.get("type") not in ("project", "feedback", "reference"):
            continue
        if len(transcripts.norm(it.get("statement", ""))) < 15:
            continue
        good.append(it)
    return good


def _session_candidates(sid, meta, run):
    """All verified candidates for one session (nothing staged here — see run())."""
    turns = transcripts.parse_transcript(meta.path)
    if not turns:
        return []
    chs = transcripts.chunks_from_turns(turns)
    if len(chs) > transcripts.MAX_CHUNKS_PER_SESSION:            # cap huge sessions — LOGGED, never silent
        run.note(f"session {sid[:8]} large: {len(chs)} chunks -> capped at "
                 f"{transcripts.MAX_CHUNKS_PER_SESSION} (sampled evenly; not silent)")
        step = len(chs) / transcripts.MAX_CHUNKS_PER_SESSION
        chs = [chs[int(i * step)] for i in range(transcripts.MAX_CHUNKS_PER_SESSION)]
    found = []
    for ci, ch in enumerate(chs):
        cands = _extract(meta.workspace, sid, ch)
        for c in cands:
            c["_session"] = sid
            c["_workspace"] = meta.workspace                     # canonical name (never enc-derived)
            c["_chunk"] = ci
        found.extend(cands)
    return found


def _stage(ws, cands):
    """Append candidates to CAND_DIR/<ws>.jsonl and return the RE-COUNTED number of lines added —
    this recount is the verified output_count that mark_done requires."""
    path = f"{CAND_DIR}/{ws}.jsonl"
    os.makedirs(CAND_DIR, exist_ok=True)
    before = sum(1 for _ in open(path)) if os.path.exists(path) else 0
    with open(path, "a") as fh:
        for c in cands:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return sum(1 for _ in open(path)) - before


def run(limit=None, only_ws=None, dry=False):
    with runlog.Run("harvest", dry=dry) as run:
        metas = sessions.enumerate_sessions(run.excluded)
        led = ledger.Ledger("harvest")
        units = {sid: m.mtime for sid, m in metas.items()
                 if only_ws is None or m.workspace == only_ws}
        due = sorted(led.due(units), key=lambda sid: -metas[sid].size)   # biggest/richest first (v1)
        if limit and len(due) > limit:
            run.note(f"{len(due)} sessions due; limited to {limit} this run")
            due = due[:limit]
        run.count("units_due", len(due))
        for sid in due:
            meta = metas[sid]
            try:
                found = _session_candidates(sid, meta, run)
            except agentcall.Capped as e:
                run.cap(e)        # STOP, resumable: in-flight session unmarked, nothing staged for it
                break
            except agentcall.AgentFailure as e:
                run.error(sid, e)
                if not dry:
                    led.mark_error(sid, mtime=meta.mtime, error=e, run_id=run.id, ws=meta.workspace)
                continue
            if dry:
                run.count("units_done")
                run.count("outputs", len(found))
                run.note(f"[dry] session {sid[:8]} ({meta.workspace}) -> {len(found)} candidate(s)")
                continue
            n = _stage(meta.workspace, found) if found else 0
            if n != len(found):   # recount IS the verification — a mismatch must not become "done"
                run.error(sid, f"stage recount {n} != {len(found)} extracted")
                led.mark_error(sid, mtime=meta.mtime, error=f"stage recount {n}!={len(found)}",
                               run_id=run.id, ws=meta.workspace)
                continue
            led.mark_done(sid, mtime=meta.mtime, output_count=n, run_id=run.id, ws=meta.workspace)
            run.count("units_done")
            run.count("outputs", n)
        return run
