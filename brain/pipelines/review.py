"""Review pipeline — stage 2 (ARCHITECTURE §2): nightly per-workspace work review over everything
since the last checkpoint (archive-based: retired/renamed workspaces still covered) → daily digest
+ designed lessons; Sundays roll 7 dailies into a weekly synthesis.

Port of fleet-review daily+weekly (prompts kept verbatim). Changes required by the architecture:
  - units are grouped by CANONICAL workspace name via sessions.descriptor_map()/workspace_of — two
    encs of one descriptor (e.g. a renamed root: ws-alpha/WsAlpha) merge into ONE unit
    (v1 grouped by enc and reviewed them twice);
  - the checkpoint (brain/state/ledger/review-ckpt.json, v1's {daily, weekly} shape) advances ONLY
    when the full unit list completed — capped or errored units keep the window open so the next run
    redoes it (reviewing a workspace twice is acceptable; silently skipping one is not — v1 advanced
    past capped units);
  - outputs live under brain/reviews/, lessons stage to brain/staged/lessons/ for consult to promote;
  - lesson evidence is re-verified against the conversation via store.verify_quote (anti-fabrication).
"""
import datetime
import glob
import json
import os
import subprocess

from engine import REVIEWS, STAGED, STATE, agentcall, runlog, sessions, store, transcripts

DAILY = f"{REVIEWS}/daily"
WEEKLY = f"{REVIEWS}/weekly"
LESSONS = f"{STAGED}/lessons"
CKPT = f"{STATE}/ledger/review-ckpt.json"

# v1 REVIEW_PROMPT + the 2026-07-10 owner charter: the PRIMARY lens is owner-discovery arcs
# ("catching these types of things, analyzing them, and continuing" — the operator, verbatim).
REVIEW_PROMPT = """You are the fleet's reviewer. DEEPLY read the actual conversation below between the operator and the agent and an AI agent working in workspace '{ws}', over the last review window. Understand BOTH sides: what the operator asked for, steered, corrected, approved, or was frustrated by — AND what the agent did, decided, got wrong, and how it recovered. Interpret intent; do not just paraphrase surface text. The git commits are corroborating ground truth for what actually shipped.

YOUR PRIMARY LENS — owner-discovery arcs. Actively hunt the pattern: the operator notices something off → questions it ("explain this...", "why is it doing...", "where is this from?") → an explanation surfaces → a directive or decision lands → a fix ships (or is still owed). EVERY such arc in this window must produce (a) coverage in the review and (b) at least one lesson of type "directive" carrying the durable rule the directive implies, stated generally enough to govern future deploys — these are the single highest-value learnings this system exists to catch. Also hunt the quieter variant: the operator asks a clarifying follow-up because the agent's first answer missed the mark — that arc reveals a durable preference (type "preference") about how the operator wants things done or explained. After recording an arc, keep analyzing the rest of the window — never stop at the first finding.

Produce TWO things in ONE JSON object:
1. A REVIEW of what actually happened this window (concrete, honest, this-window-only).
2. LESSONS you can DESIGN from this interaction — durable, reusable learnings: operator corrections, revealed preferences, agent failures-and-fixes, decisions made, and patterns worth repeating or avoiding. Each lesson MUST quote the conversation VERBATIM as evidence (an exact substring that appears below) and say how a future agent should apply it.

Return ONLY this JSON:
{{"workspace":"{ws}",
 "headline":"<one sentence: the single most important thing that happened>",
 "advanced":["<concrete thing that moved forward, cite a commit hash/file if available>"],
 "blocked":["<what is stalled / failing / waiting on the operator>"],
 "notable":["<a decision, pivot, or risk worth flagging>"],
 "lessons":[{{"lesson":"<the durable learning, stated generally>","type":"directive|correction|preference|failure-fix|decision|pattern","evidence":"<VERBATIM quote copied from the conversation below>","how_to_apply":"<what an agent should do next time>"}}]}}
Ground every claim. Empty lists are fine. Prefer a few sharp, real lessons over many shallow ones. Do NOT invent quotes — copy them exactly.

=== CONVERSATION (operator + agent), this window — READ DEEPLY ===
{convo}

=== GIT COMMITS (corroborating artifact) ===
{git}

=== LOGS (recent) ===
{logs}"""

# v1 weekly prompt, verbatim.
WEEKLY_PROMPT = ("Synthesize these daily fleet work-reviews into a WEEKLY summary. Identify: "
                 "cross-workspace themes, what genuinely advanced this week, what has been "
                 "STALLED/BLOCKED multiple days (flag it), and the 2-3 things most worth the "
                 "operator's attention. Be concise and honest. Return markdown.\n\n")


# ------------------------------------------------------------------ small helpers (v1 ports)
def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()


def _epoch(iso):
    """ISO string → UTC epoch. Checkpoints are naive-UTC; v1 parsed them as LOCAL time, skewing
    the window on non-UTC hosts — parsed as UTC here."""
    dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def _load_ckpt():
    try:
        with open(CKPT) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_ckpt(d):
    os.makedirs(os.path.dirname(CKPT), exist_ok=True)
    tmp = CKPT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(d, fh, indent=2)
    os.replace(tmp, CKPT)


def git_since(root, since_iso):
    """Commits + shortstat in root since a timestamp (empty if not a repo / no commits)."""
    if not root or not os.path.isdir(f"{root}/.git"):
        return ""
    try:
        return subprocess.run(
            ["git", "-C", root, "log", f"--since={since_iso}", "--pretty=format:%h %ad %s",
             "--date=short", "--stat"],
            capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def recent_logs(root, since_epoch):
    """Tail of the 2 most recent files in root/logs/ touched inside the window (v1 behavior)."""
    out = ""
    for lf in sorted(glob.glob(f"{root}/logs/*"), key=os.path.getmtime, reverse=True)[:2]:
        try:
            if os.path.getmtime(lf) >= since_epoch:
                out += f"\n--- {os.path.basename(lf)} ---\n" + open(lf, errors="ignore").read()[-4000:]
        except Exception:
            pass
    return out


# ------------------------------------------------------------------ units + per-unit review
def _units_since(metas, dmap, since_epoch, since_iso):
    """Activity grouped by CANONICAL workspace name: {name: {"root", "paths"}}. Also catches
    workspaces with git commits but no in-window transcripts (v1 descriptor scan)."""
    units = {}
    for m in metas.values():
        if m.mtime < since_epoch:
            continue
        ws = sessions.workspace_of(m.enc, dmap)
        if ws.kind == "confined":     # confinement wall — never review confined-workspace content (belt; transcripts
            continue                # are already excluded upstream in enumerate_sessions)
        u = units.setdefault(ws.name, {"root": None, "paths": []})
        u["paths"].append(m.path)
        # among a unit's encs, prefer a root that still exists on disk
        if ws.root and (u["root"] is None or
                        (not os.path.isdir(u["root"]) and os.path.isdir(ws.root))):
            u["root"] = ws.root
    best = {}
    for w in dmap.values():
        if w.kind == "confined":      # confinement wall — this git/log scan read $CONFINED_ROOT repos before
            continue                # the 2026-07-09 verifier caught it
        cur = best.get(w.name)
        if cur is None or (w.root and os.path.isdir(w.root)
                           and not (cur.root and os.path.isdir(cur.root))):
            best[w.name] = w
    for w in best.values():
        if w.name not in units and w.root and git_since(w.root, since_iso):
            units[w.name] = {"root": w.root, "paths": []}
    return units


def _review_workspace(ws, root, session_paths, since_epoch, since_iso):
    """One unit → review dict, or None (heartbeat-only window: no agent call). Capped/AgentFailure
    propagate to run_daily()."""
    git = git_since(root, since_iso) or "(no git activity / not a repo / path unresolved)"
    logs = recent_logs(root, since_epoch) if root else ""
    convo, n_op, n_ag = transcripts.conversation_window(session_paths, since_epoch)
    real_git = not git.startswith("(")
    # skip heartbeat-only windows WITHOUT an agent call: no commits, no operator turns, negligible
    # agent text (AUTOCONTINUE nudges / "No response requested." — nothing to review or learn from)
    if not real_git and n_op == 0 and len(convo) < 400:
        return None
    if not real_git and not convo.strip():
        return None
    prompt = REVIEW_PROMPT.format(ws=ws, git=git[:24000], logs=(logs or "(none)")[:8000],
                                  convo=(convo or "(no conversation in window)"))
    r = agentcall.call_json(prompt, shape=dict)
    r["workspace"] = ws   # canonical name is the engine's, not the model's echo
    # anti-fabrication: keep only lessons whose verbatim evidence appears in the conversation
    r["lessons"] = [L for L in (r.get("lessons") or [])
                    if isinstance(L, dict)
                    and len(transcripts.norm(L.get("evidence", ""))) >= 12
                    and store.verify_quote(L.get("evidence", ""), convo)]
    r["_meta"] = {"operator_turns": n_op, "agent_turns": n_ag}
    return r


def _empty(r):
    """No-activity backstop for the pre-filter: a lone 'no activity' notable is empty signal (v1)."""
    notable = [x for x in (r.get("notable") or [])
               if "no substantive activity" not in x.lower() and "no activity" not in x.lower()]
    return not (r.get("advanced") or r.get("blocked") or notable or r.get("lessons"))


def _digest_md(reviews, today, since_iso):
    n_lessons = sum(len(r.get("lessons") or []) for r in reviews)
    md = [f"# Fleet work review — {today}",
          f"_Activity since {since_iso}. {len(reviews)} workspace(s) active · "
          f"{n_lessons} lesson(s) designed._", ""]
    for r in reviews:
        md.append(f"## {r['workspace']} — {r.get('headline', '')}")
        for k, hd in (("advanced", "✅ Advanced"), ("blocked", "⚠ Blocked / stalled"),
                      ("notable", "● Notable")):
            items = r.get(k) or []
            if items:
                md.append(f"**{hd}:**")
                md += [f"- {x}" for x in items]
                md.append("")
        lessons = r.get("lessons") or []
        if lessons:
            md.append("**◆ Lessons designed from this window:**")
            for L in lessons:
                md.append(f"- _{L.get('type', '')}_ — **{L.get('lesson', '')}**")
                md.append(f"    - apply: {L.get('how_to_apply', '')}")
                md.append(f"    - evidence: \"{(L.get('evidence', '') or '')[:220]}\"")
            md.append("")
    return "\n".join(md) + "\n"


# ------------------------------------------------------------------ daily
def run_daily(dry=False, since=None):
    with runlog.Run("review", dry=dry) as run:
        ck = _load_ckpt()
        window_end = _now_iso()   # ckpt value on success — stamped at run START so activity that
                                  # lands DURING a long run is re-covered next night, never skipped
        since_iso = since or ck.get("daily") or \
            (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=1)).replace(tzinfo=None).isoformat()
        since_epoch = _epoch(since_iso)
        dmap = sessions.descriptor_map()
        metas = sessions.enumerate_sessions(run.excluded)
        units = _units_since(metas, dmap, since_epoch, since_iso)
        run.count("units_due", len(units))
        run.note(f"activity since {since_iso}: {len(units)} workspace(s): "
                 + ", ".join(sorted(units)))
        reviews, completed = [], []
        for name in sorted(units):
            u = units[name]
            try:
                r = _review_workspace(name, u["root"], u["paths"], since_epoch, since_iso)
            except agentcall.Capped as e:
                run.cap(e)        # STOP; remaining workspaces roll to the next run
                break
            except agentcall.AgentFailure as e:
                run.error(name, e)
                continue
            completed.append(name)
            run.count("units_done")
            if r:
                reviews.append(r)
        skipped = [r["workspace"] for r in reviews if _empty(r)]
        reviews = [r for r in reviews if not _empty(r)]
        if skipped:
            run.note(f"skipped {len(skipped)} no-activity: {', '.join(skipped)}")
        run.count("outputs", len(reviews))
        n_lessons = sum(len(r.get("lessons") or []) for r in reviews)
        if n_lessons:
            run.count("lessons", n_lessons)
        today = datetime.date.today().isoformat()
        if dry:
            if reviews:
                run.note("[dry] digest:\n" + _digest_md(reviews, today, since_iso)[:2000])
            return run
        if reviews:
            os.makedirs(DAILY, exist_ok=True)
            with open(f"{DAILY}/{today}.md", "w") as fh:
                fh.write(_digest_md(reviews, today, since_iso))
            with open(f"{DAILY}/{today}.json", "w") as fh:   # structured → regenerate digest w/o re-running
                json.dump(reviews, fh, indent=2)
            if n_lessons:   # stage provenance-grounded lessons for consult to promote (v1 schema)
                os.makedirs(LESSONS, exist_ok=True)
                allL = [{**L, "workspace": r["workspace"], "source": "review"}
                        for r in reviews for L in (r.get("lessons") or [])]
                # MERGE into today's file (dedup by workspace+lesson) — a plain overwrite would
                # clobber same-day staging and resurrect items consult already consumed
                path = f"{LESSONS}/{today}.json"
                try:
                    with open(path) as fh:
                        cur = json.load(fh)
                except Exception:
                    cur = []
                seen = {(c.get("workspace"), transcripts.norm(c.get("lesson", ""))) for c in cur}
                for L in allL:
                    k = (L.get("workspace"), transcripts.norm(L.get("lesson", "")))
                    if k not in seen:
                        seen.add(k)
                        cur.append(L)
                tmp = path + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump(cur, fh, indent=2)
                os.replace(tmp, path)
        # checkpoint: advance ONLY when the full unit list completed (see module docstring)
        if run.capped is None and not run.errors:
            ck["daily"] = window_end
            _save_ckpt(ck)
        else:
            run.note("checkpoint NOT advanced — window will be redone; completed this run: "
                     + (", ".join(completed) or "(none)"))
        if reviews:
            head = "; ".join(r.get("headline", "") for r in reviews[:4])
            runlog.notify_main(f"[brain review {today}] {len(reviews)} workspaces active, "
                               f"{n_lessons} lessons designed. {head}")
        return run


# ------------------------------------------------------------------ weekly
def run_weekly(dry=False):
    with runlog.Run("weekly", dry=dry) as run:
        dailies = sorted(glob.glob(f"{DAILY}/*.md"))[-7:]
        run.count("units_due", 1)
        if not dailies:
            run.note("no dailies yet")
            return run
        blob = "\n\n".join(f"=== {os.path.basename(d)} ===\n{open(d).read()}" for d in dailies)
        try:
            out = agentcall.call(WEEKLY_PROMPT + blob[:120000])
        except agentcall.Capped as e:
            run.cap(e)            # resumable: next run re-reads the same dailies
            return run
        run.count("units_done")
        run.count("outputs")
        wk = datetime.date.today().isoformat()
        text = f"# Fleet weekly synthesis — week ending {wk}\n\n{out.strip()}\n"
        if dry:
            run.note("[dry] weekly:\n" + text[:1500])
            return run
        os.makedirs(WEEKLY, exist_ok=True)
        with open(f"{WEEKLY}/{wk}.md", "w") as fh:
            fh.write(text)
        ck = _load_ckpt()
        ck["weekly"] = _now_iso()
        _save_ckpt(ck)
        runlog.notify_main(f"[brain WEEKLY {wk}] synthesis ready → brain/reviews/weekly/{wk}.md")
        return run
