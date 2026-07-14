"""Stage 4 — CONSULT: staged pool → maker → INDEPENDENT verifier → promote (G1/G4/G7; ARCHITECTURE §6).

The missing half of the loop: knowledge that was only ever written now gets distilled and routed to
where sessions actually read it — workspace memory, STATE.md rules/lessons, or a skill's
"## Learned failure modes (auto)" section.

Structural separations (not conventions):
  - the VERIFIER prompt never contains the maker's reasoning — only item, proposal, shared context;
  - the ENGINE re-gates evidence regardless of any verdict: every proposal quote must be a verbatim
    substring of the item's OWN already-verified quote/evidence (fabrication cannot pass);
  - APPLY is deterministic code; every processed item is archived to staged/promoted.jsonl with its
    verdict + before/after (reversible), then removed from the staged pool (atomic rewrite).

BATCHED calls (seed-completion burn-down, the batched-promotion design): the ranked pool is
grouped by item workspace and processed in chunks of ≤BATCH same-workspace items — ONE maker call
and ONE verifier call per chunk (shared target-context built once per workspace) instead of two
calls per item. Every per-item invariant above survives batching: engine gates run per item BEFORE
the verifier; an item the maker or verifier omits (or returns a malformed entry for) stays staged
this run — an omission is NEVER an auto-drop.

Volume control: promotions per run capped (default 12); overflow stays staged and drains
highest-confidence-first. skill_append sections are hard-capped at 15 bullets — a full section
leaves the item staged (merge pass is owner-queued; existing bullets are never auto-deleted).
"""
import datetime
import glob
import json
import os
import re

from engine import PROJ, STAGED
from engine import agentcall, runlog, store
from engine.transcripts import norm

CAND_DIR = f"{STAGED}/candidates"
LESS_DIR = f"{STAGED}/lessons"
PROMOTED = f"{STAGED}/promoted.jsonl"
WRITES_LOG = f"{STAGED}/writes.jsonl"
SKILLS_DIR = f"{PROJ}/skills"
DEFAULT_CAP = 12                       # promotions per night (ARCHITECTURE §6 volume control)
BATCH = 8                              # same-workspace items per maker/verifier call (§S4 burn-down)
EXAMINE_FACTOR = 5                     # ≤ cap*this items EXAMINED per run — drops/rejects don't count
                                       # toward the promotion cap, so without this bound the 600-item
                                       # migration backlog would cost ~2 calls/item in one night
SKILL_SECTION = "## Learned failure modes (auto)"
SKILL_CAP = 15                         # hard cap on auto bullets per skill (anti-regression)
ACTIONS = ("memory", "state_rule", "state_lesson", "skill_append", "drop")

MAKER_PROMPT = """You are the DISTILLER (maker) in the fleet second-brain's promotion loop. Below is an \
ARRAY of staged knowledge items, all from the same workspace; each item's quote/evidence is already \
verified verbatim against its source conversation. For EACH item, decide where it belongs and write \
its final wording. An INDEPENDENT verifier will check your proposals against the same target \
context — be conservative.

Actions:
- "memory"       durable project fact / operator preference / reference → auto-memory file in the target workspace's store
- "state_rule"   a distilled operating rule → "## General rules" in the target workspace's STATE.md
- "state_lesson" a lesson learned → "## Lessons learned" in the target workspace's STATE.md
- "skill_append" ONLY for a genuinely general rule that squarely matches ONE existing skill's domain (skills listed in the context)
- "drop"         ephemeral, one-off, trivial, or already covered by the existing context

Rules:
- OPERATOR DIRECTIVES FIRST: an item recording an operator directive or an owner-discovery arc (the operator caught something off and ruled on it) is the highest-value class — prefer promotion over drop, and place it where future deploys will actually read it: fleet-wide directives → "main" (state_rule or memory), workspace-scoped → that workspace's STATE.md rules.
- target: workspace-scoped knowledge → its own workspace ("{ws}"); fleet-wide/general → "main"; for skill_append → the exact skill name.
- MERGE/UPDATE over duplicate: if the existing context already covers this, choose "drop" — do not restate what is already written.
- final_text: 1-2 tight sentences, stated generally; no session narrative, no dates.
- evidence: 1-2 quotes copied VERBATIM from that item's own quote/evidence text — never write new text as evidence.
- Judge every item independently; return EXACTLY one proposal per item, carrying its "idx" unchanged.

ITEMS (verified; each tagged "idx"):
{items}

TARGET CONTEXT (existing knowledge — memory indexes, STATE rules/lessons, available skills):
{ctx}

Return ONLY a JSON array, one object per item: [{{"idx":<item idx>,"action":"memory|state_rule|state_lesson|skill_append|drop","target":"<workspace or skill name>","final_text":"...","evidence":["<verbatim quote>"],"reason":"..."}}]"""

VERIFIER_PROMPT = """You are the INDEPENDENT VERIFIER in the fleet second-brain's promotion loop. \
A maker proposed promoting each staged item below into durable knowledge. You are NOT shown the \
maker's reasoning — judge only what is in front of you, each item INDEPENDENTLY.

Rubric — verdict "promote" only if ALL hold:
1. Every proposal evidence quote appears VERBATIM in the item's own quote/evidence text (the engine re-checks this too).
2. final_text's generalization is actually supported by the evidence — no overreach beyond what was said or observed.
3. It does not duplicate the target context. If it CONTRADICTS something there, verdict "flag" and name the contradicted entry in "contradicts".
4. For skill_append: final_text is general enough to belong in that skill and is consistent with the skill's domain/description.
Otherwise verdict "reject" (duplicate, overreach, ephemera, wrong target).

ITEMS AND PROPOSALS (each tagged "idx"; "item" is the verified original, "proposal" is the maker's):
{pairs}

TARGET CONTEXT (the same context the maker saw):
{ctx}

Return ONLY a JSON array, one object per item: [{{"idx":<item idx>,"verdict":"promote|reject|flag","reason":"...","contradicts":"<existing memory/rule name it contradicts, or empty>"}}]"""


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z"


# ---------------------------------------------------------------- pool
def _load_pool():
    """Every staged candidate line + lesson item, with (file, idx) for consumption."""
    pool = []
    for f in sorted(glob.glob(f"{CAND_DIR}/*.jsonl")):
        for i, line in enumerate(open(f)):
            if not line.strip():
                continue
            try:
                pool.append({"kind": "candidate", "file": f, "idx": i, "data": json.loads(line)})
            except json.JSONDecodeError:
                continue
    for f in sorted(glob.glob(f"{LESS_DIR}/*.json")):
        try:
            with open(f) as fh:
                arr = json.load(fh)
        except Exception:
            continue
        for i, d in enumerate(arr if isinstance(arr, list) else []):
            if isinstance(d, dict):
                pool.append({"kind": "lesson", "file": f, "idx": i, "data": d})
    return pool


def _rank(entry):
    """Highest-confidence-first: high > medium > low; lessons (failures/review) rank equal to high."""
    if entry["kind"] == "lesson":
        return 0
    d = entry["data"]   # a malformed (non-dict) staged line must not crash the sort
    return {"high": 0, "medium": 1, "low": 2}.get(d.get("confidence") if isinstance(d, dict) else None, 1)


def _rewrite_pool(consumed):
    """Remove consumed items from their staged files (atomic tmp+rename; empty files deleted)."""
    by_file = {}
    for e in consumed:
        by_file.setdefault(e["file"], set()).add(e["idx"])
    for f, idxs in by_file.items():
        if not os.path.exists(f):
            continue
        if f.endswith(".jsonl"):
            keep = [ln for i, ln in enumerate(open(f)) if i not in idxs]
            keep = [ln for ln in keep if ln.strip()]
            if keep:
                tmp = f + ".tmp"
                with open(tmp, "w") as fh:
                    fh.writelines(keep)
                os.replace(tmp, f)
            else:
                os.remove(f)
        else:
            try:
                with open(f) as fh:
                    arr = json.load(fh)
            except Exception:
                continue
            keep = [d for i, d in enumerate(arr) if i not in idxs]
            if keep:
                tmp = f + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump(keep, fh, indent=1, ensure_ascii=False)
                os.replace(tmp, f)
            else:
                os.remove(f)


# ---------------------------------------------------------------- context
def _skills_index():
    """{skill-name: description} from skills/*/SKILL.md frontmatter."""
    out = {}
    for p in sorted(glob.glob(f"{SKILLS_DIR}/*/SKILL.md")):
        name = os.path.basename(os.path.dirname(p))
        try:
            head = open(p).read(2500)
        except OSError:
            continue
        m = re.search(r"^description:\s*[\"']?(.+?)[\"']?\s*$", head, re.M)
        out[name] = (m.group(1)[:220] if m else "")
    return out


def _target_context(ws, skills):
    """The shared context BOTH maker and verifier see: memory indexes + STATE rules/lessons for the
    item's workspace and main, plus the skill catalogue."""
    st = store.state_read(ws)
    ctx = {"item_workspace": ws,
           "workspace_memory_index": store.existing_index(ws),
           "workspace_state": {k: st.get(k, "") for k in ("rules", "lessons")},
           "skills": [f"{n}: {d}" for n, d in skills.items()]}
    if ws != "main":
        stm = store.state_read("main")
        ctx["main_memory_index"] = store.existing_index("main")
        ctx["main_state"] = {k: stm.get(k, "") for k in ("rules", "lessons")}
    return json.dumps(ctx, ensure_ascii=False, indent=1)[:60_000]


# ---------------------------------------------------------------- gates + apply
def _gate(proposal, item_quote):
    """THE hard gate: every proposal evidence quote must be drawn verbatim from the item's own
    already-verified quote/evidence text. Runs regardless of any verdict."""
    evs = [q for q in (proposal.get("evidence") or []) if isinstance(q, str) and q.strip()]
    return bool(evs) and all(store.verify_quote(q, item_quote) for q in evs)


def _blockless(txt):
    return "" if (txt or "").strip() == "(nothing yet)" else (txt or "").strip()


def _apply_memory(target, proposal, item, kind, dry, contradiction=""):
    ft = proposal["final_text"].strip()
    name = item.get("topic") or "-".join(re.findall(r"[a-z0-9]+", ft.lower())[:6]) or "promoted"
    typ = item.get("type") if item.get("type") in ("project", "feedback", "reference") else "project"
    mem = {"name": name, "type": typ, "statement": ft, "description": ft[:140],
           "why": item.get("why", "")}
    if contradiction:
        mem["contradiction"] = contradiction
    prov = [(q, item if kind == "candidate" else None) for q in proposal.get("evidence") or []]
    return store.write_auto_memory(target, mem, prov, dry=dry, writes_log=WRITES_LOG)


def _apply_state(target, key, final_text, src_ws, dry):
    """Append one bullet to the rules/lessons AUTO block. Returns (result, before, after)."""
    before = _blockless(store.state_read(target).get(key, ""))
    if norm(final_text) in norm(before):
        return "dup", before, before
    bullet = f"- {final_text} _({datetime.date.today().isoformat()}, {src_ws})_"
    after = (before + "\n" + bullet).strip()
    store.state_write(target, {key: after}, dry=dry)
    return "ok", before, after


def _apply_skill(skill, final_text, src_ws, dry):
    """Append one bullet to the skill's delimited auto section (created at EOF if missing).
    Returns (result, before, after) — result ∈ ok|dup|full|missing. Never edits the skill body."""
    path = f"{SKILLS_DIR}/{skill}/SKILL.md"
    if not os.path.exists(path):
        return "missing", "", ""
    txt = open(path).read()
    # section BOUNDS: from the header to the next '## ' heading (or EOF) — bullets must be counted
    # and inserted INSIDE them, not at EOF (a later hand-added section would otherwise swallow
    # appends and the cap would count foreign bullets — 2026-07-09 verifier finding)
    if SKILL_SECTION in txt:
        s_start = txt.index(SKILL_SECTION)
        body_start = s_start + len(SKILL_SECTION)
        nxt = txt.find("\n## ", body_start)
        s_end = nxt if nxt != -1 else len(txt)
        section = txt[body_start:s_end]
    else:
        section = ""
    bullets = [ln for ln in section.splitlines() if ln.startswith("- ")]
    if norm(final_text) in norm(section):
        return "dup", section.strip(), section.strip()
    if len(bullets) >= SKILL_CAP:
        return "full", section.strip(), section.strip()
    bullet = f"- {final_text} _({datetime.date.today().isoformat()}, from {src_ws})_"
    if SKILL_SECTION in txt:
        new = txt[:s_end].rstrip() + f"\n{bullet}\n" + (("\n" + txt[s_end:].lstrip("\n")) if txt[s_end:].strip() else "")
    else:
        new = txt.rstrip() + f"\n\n{SKILL_SECTION}\n{bullet}\n"
    if not dry:
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(new)
        os.replace(tmp, path)
    return "ok", section.strip(), (section.strip() + "\n" + bullet).strip()


def _archive(entry, proposal, verdict, applied, before=None, after=None, run=None, dry=False):
    """Append the full processed record to promoted.jsonl — the reversible audit trail."""
    rec = {"ts": _now_iso(), "run": run.id if run else "", "kind": entry["kind"],
           "file": os.path.basename(entry["file"]), "item": entry["data"],
           "proposal": ({k: proposal.get(k) for k in ("action", "target", "final_text", "evidence")}
                        if proposal else None),
           "maker_reason": (proposal or {}).get("reason", ""),
           "verdict": verdict.get("verdict", ""), "reason": verdict.get("reason", ""),
           "applied": applied, "before": before, "after": after}
    if dry:
        return
    os.makedirs(os.path.dirname(PROMOTED), exist_ok=True)
    with open(PROMOTED, "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- run
def _by_idx(arr, n):
    """{idx: entry} from a model-returned array — dict entries carrying an in-range integer "idx"
    only; duplicates keep the first. Anything unmappable simply leaves its item unmatched, and an
    unmatched item stays staged this run (omission is NEVER an auto-drop)."""
    out = {}
    for p in (arr if isinstance(arr, list) else []):
        if not isinstance(p, dict):
            continue
        i = p.get("idx")
        if isinstance(i, bool) or not isinstance(i, int):
            continue
        if 0 <= i < n and i not in out:
            out[i] = p
    return out


def _run_chunk(chunk, ws, ctx, skills, run, dry, consumed, cap, promoted):
    """One ≤BATCH chunk of same-workspace items: ONE batched maker call → per-item ENGINE gates
    (still BEFORE the verifier) → ONE batched verifier call → per-item deterministic apply.
    Returns (promoted, cap_hit); agentcall.Capped propagates to the caller."""
    tagged = []
    for i, entry in enumerate(chunk):
        d = dict(entry["data"])
        d["idx"] = i
        tagged.append(d)
    proposals = _by_idx(agentcall.call_json(
        MAKER_PROMPT.format(ws=ws, items=json.dumps(tagged, ensure_ascii=False), ctx=ctx),
        shape=list), len(chunk))

    to_verify = []          # (idx, entry, proposal) that survived every engine gate
    for i, entry in enumerate(chunk):
        item, kind = entry["data"], entry["kind"]
        proposal = proposals.get(i)
        if proposal is None:            # maker omitted / malformed entry → stays staged this run
            run.count("maker_omitted")
            continue
        action, target = proposal.get("action"), (proposal.get("target") or "").strip()
        if action == "drop":
            _archive(entry, proposal, {"verdict": "drop",
                                       "reason": proposal.get("reason", "")},
                     applied=None, run=run, dry=dry)
            run.count("dropped")
            consumed.append(entry)
            continue
        if action not in ACTIONS or not (proposal.get("final_text") or "").strip():
            _archive(entry, proposal, {"verdict": "reject",
                                       "reason": "engine: malformed proposal"},
                     applied=None, run=run, dry=dry)
            run.count("rejected")
            consumed.append(entry)
            continue
        if action == "skill_append" and target not in skills:
            _archive(entry, proposal, {"verdict": "reject",
                                       "reason": f"engine: unknown skill {target!r}"},
                     applied=None, run=run, dry=dry)
            run.count("rejected")
            consumed.append(entry)
            continue
        if action != "skill_append" and target not in (ws, "main"):
            _archive(entry, proposal, {"verdict": "reject",
                                       "reason": "engine: target must be the item's "
                                                 "workspace or main"},
                     applied=None, run=run, dry=dry)
            run.count("rejected")
            consumed.append(entry)
            continue
        # THE hard gate — before the verifier is even asked, and regardless of its verdict
        item_quote = item.get("quote") or item.get("evidence") or ""
        if not _gate(proposal, item_quote):
            _archive(entry, proposal, {"verdict": "reject",
                                       "reason": "engine evidence gate: proposal evidence "
                                                 "not drawn verbatim from the item's "
                                                 "verified quote"},
                     applied=None, run=run, dry=dry)
            run.count("evidence_gate_rejects")
            consumed.append(entry)
            continue
        to_verify.append((i, entry, proposal))

    if not to_verify:
        return promoted, False

    pairs = [{"idx": i, "item": entry["data"],
              "proposal": {k: p.get(k) for k in ("action", "target", "final_text", "evidence")}}
             for i, entry, p in to_verify]
    # STRUCTURAL separation (ARCHITECTURE §6): the verifier never sees the maker's reasoning —
    # asserted here, not just convention: the payload must carry no maker "reason" field.
    assert not any("reason" in pr["proposal"] for pr in pairs), \
        "maker reason leaked into verifier payload"
    verdicts = _by_idx(agentcall.call_json(
        VERIFIER_PROMPT.format(pairs=json.dumps(pairs, ensure_ascii=False), ctx=ctx),
        shape=list), len(chunk))

    for i, entry, proposal in to_verify:
        if promoted >= cap:             # cap hit mid-chunk: the rest of the chunk stays staged
            return promoted, True
        verdict = verdicts.get(i)
        if verdict is None:             # verifier omitted / malformed entry → stays staged this run
            run.count("verifier_omitted")
            continue
        item, kind = entry["data"], entry["kind"]
        uid = f"{kind}:{os.path.basename(entry['file'])}:{entry['idx']}"
        action, target = proposal.get("action"), (proposal.get("target") or "").strip()
        try:
            v = verdict.get("verdict")
            if v == "flag":
                contra = (verdict.get("contradicts") or "").strip()
                if contra:   # v1 contradiction rule: write as flagged-discrepancy memory
                    tgt = target if action != "skill_append" else ws
                    fn = _apply_memory(tgt, proposal, item, kind, dry, contradiction=contra)
                    _archive(entry, proposal, verdict, applied=fn, run=run, dry=dry)
                    run.count("flagged_contradictions")
                    promoted += 1
                else:
                    _archive(entry, proposal, verdict, applied=None, run=run, dry=dry)
                    run.count("flagged")
                consumed.append(entry)
                continue
            if v != "promote":
                _archive(entry, proposal, verdict, applied=None, run=run, dry=dry)
                run.count("rejected")
                consumed.append(entry)
                continue
            # APPLY (deterministic)
            ft = proposal["final_text"].strip()
            if action == "memory":
                fn = _apply_memory(target, proposal, item, kind, dry)
                _archive(entry, proposal, verdict, applied=fn, run=run, dry=dry)
                if fn:
                    promoted += 1
            elif action in ("state_rule", "state_lesson"):
                key = "rules" if action == "state_rule" else "lessons"
                res, before, after = _apply_state(target, key, ft, ws, dry)
                _archive(entry, proposal, verdict,
                         applied=("dedupe-skip" if res == "dup" else f"STATE.md:{key}"),
                         before=before, after=after, run=run, dry=dry)
                if res == "ok":
                    promoted += 1
            elif action == "skill_append":
                res, before, after = _apply_skill(target, ft, ws, dry)
                if res == "full":       # anti-regression cap: stays staged, owner-queued merge
                    run.note(f"skill '{target}' auto-section at {SKILL_CAP}-bullet cap — "
                             f"{uid} stays staged (merge pass owner-queued)")
                    run.count("skill_cap_deferred")
                    continue            # NOT consumed, NOT archived
                if res == "missing":
                    _archive(entry, proposal, verdict,
                             applied=None, run=run, dry=dry)
                    run.count("rejected")
                    consumed.append(entry)
                    continue
                _archive(entry, proposal, verdict,
                         applied=("dedupe-skip" if res == "dup" else f"skills/{target}"),
                         before=before, after=after, run=run, dry=dry)
                if res == "ok":
                    promoted += 1
            consumed.append(entry)
        except Exception as e:          # per-item apply failure: item stays staged, chunk continues
            run.error(uid, e)
            continue
    return promoted, False


def run(dry=False, cap=None, batch=BATCH):
    cap = DEFAULT_CAP if cap is None else cap
    batch = max(1, batch or BATCH)
    with runlog.Run("consult", dry=dry) as run:
        pool = _load_pool()
        run.count("staged_pool", len(pool))
        if not pool:
            run.note("staged pool empty — nothing to promote")
            return run
        pool.sort(key=_rank)
        skills = _skills_index()
        consumed, promoted, examined = [], 0, 0
        # group the RANKED pool by item workspace (groups ordered by their highest-ranked item;
        # rank order preserved within each group); malformed staged lines are archived + consumed
        # up front without spending an agent call on them
        groups = {}
        for entry in pool:
            item = entry["data"]
            if not isinstance(item, dict):   # malformed staged line: archive + consume, don't crash
                _archive(entry, None, {"verdict": "reject", "reason": "engine: malformed item"},
                         applied=None, run=run, dry=dry)
                run.count("rejected")
                consumed.append(entry)
                continue
            ws = item.get("_workspace") or item.get("workspace") or "main"
            groups.setdefault(ws, []).append(entry)
        stopped = False
        for ws, group in groups.items():
            if stopped:
                break
            ctx = _target_context(ws, skills)    # shared context: built ONCE per workspace
            pos = 0
            while pos < len(group):
                if promoted >= cap:
                    run.note(f"promotion cap {cap} reached — remaining items stay staged")
                    stopped = True
                    break
                budget = cap * EXAMINE_FACTOR - examined
                if budget <= 0:
                    run.note(f"examination bound {cap * EXAMINE_FACTOR} reached — "
                             f"remaining items stay staged for later runs")
                    stopped = True
                    break
                chunk = group[pos:pos + min(batch, budget)]
                pos += len(chunk)
                examined += len(chunk)          # examined counts items included in maker chunks
                run.count("units_due", len(chunk))
                try:
                    promoted, cap_hit = _run_chunk(chunk, ws, ctx, skills, run, dry,
                                                   consumed, cap, promoted)
                except agentcall.Capped as e:
                    run.cap(e)      # consumed stay consumed; unprocessed chunk items stay staged
                    stopped = True
                    break
                except Exception as e:  # per-chunk call failure: chunk stays staged, run continues
                    run.error(f"chunk:{ws}:{pos - len(chunk)}", e)
                    continue
                if cap_hit:
                    run.note(f"promotion cap {cap} reached — remaining items stay staged")
                    stopped = True
                    break
        run.count("outputs", promoted)
        if not dry:
            _rewrite_pool(consumed)
        return run
