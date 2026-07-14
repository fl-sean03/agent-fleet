"""Meta-harness PROPOSE loop (Phase 1) — the brain's missing ACT arm, for the HARNESS layer.

Fills audit gap #1 (docs/BRAIN.md): the brain detects but only ever writes
memories. This turns a detected harness problem into a concrete, verified CHANGE PROPOSAL — and stops
at a human gate. Phase 1 is dossier-only: it never applies anything to the live surface and never
touches a live agent. See docs/BRAIN.md.

Per issue: DIAGNOSE+PROPOSE (maker) -> VERIFY-IN-ISOLATION (apply-ability + per-file syntax check)
-> INDEPENDENT VERIFIER (never sees the maker's reasoning) -> DOSSIER -> fleet-message to main.
the operator applies by hand. No auto-apply (that is Phase 2).

Discipline mirrored from consult.py: structurally-separate maker/verifier, an engine gate that runs
regardless of verdict (here: every edit's old_string must be a verbatim, UNIQUE substring of the real
file — a fabricated or ambiguous edit cannot pass), and an append-only reversible record. Bounded:
PROPOSE_CAP proposals per run; a fingerprint already in proposals.jsonl is never re-proposed (the operator
clears it to reopen). Tier 0 targets (auth/credentials/confinement/systemd/MODEL pin/this loop's own
gate) are NEVER auto-editable — the maker may flag one but it is delivered as high-scrutiny only.
"""
import datetime
import glob
import hashlib
import json
import os
import re
import subprocess
import tempfile

from engine import PROJ, STAGED, STATE, setting
from engine import agentcall, runlog, telemetry

HARNESS_STATE = f"{STATE}/harness"
PROPOSALS_LOG = f"{HARNESS_STATE}/proposals.jsonl"
DOSSIER_DIR = f"{HARNESS_STATE}/proposals"
LESS_DIR = f"{STAGED}/lessons"
# Lessons from the workspace that owns the fleet itself (set FLEET_INFRA_WS to your infra
# workspace name; empty = consider lessons from every workspace).
INFRA_WS = setting("FLEET_INFRA_WS", "")

PROPOSE_CAP = 3            # proposals built per run (bounds Opus spend; overflow waits for next run)
MAX_SRC_CHARS = 60_000     # cap on source text handed to the maker per issue

# Tier-0 files: never auto-editable. A maker edit targeting one of these is downgraded to
# "flag only" — surfaced for high-scrutiny human review, never presented as ready-to-apply.
TIER0_RE = re.compile(r"(^|/)(account-refresh|account-usage|account-profile|account-status|"
                      r"swap-fleet|swap-account|run-claude-confined|confined-cfg-sync|agentctl)$|"
                      r"\.service$|\.timer$|agentcall\.py$|/harness\.py$|/telemetry\.py$")

# staged-lesson → candidate harness source files, by keyword. A lesson with no match is context-only
# (no file anchor → the maker has nothing concrete to edit), so it is not turned into a proposal.
KW_SOURCES = [
    (re.compile(r"account|rotation|401|usage|eligib|failover|capp?ed", re.I),
     ["bin/account-watch"]),
    (re.compile(r"swap[- ]?fleet|swap[- ]?account|\bswap\b", re.I),
     ["bin/swap-fleet", "bin/swap-account"]),
    (re.compile(r"resume|bounce|run-claude|/compact|resume gate", re.I),
     ["bin/run-claude"]),
    (re.compile(r"session[- ]?guard|stuck|STUCK-RISK|context %|high context", re.I),
     ["bin/session-guard"]),
    (re.compile(r"messag|fleet-msg|agentctl send|inbox", re.I),
     ["bin/fleet-msg", "bin/agentctl"]),
]

MAKER_PROMPT = """You are a harness engineer in the fleet's META-HARNESS loop. The fleet is a set of \
Claude Code agents driven by shell/python "harness" scripts (launchers, an account-rotation watchdog, \
a swap tool, a session guard). Below is a recurring PROBLEM detected in the fleet's own operational \
logs, plus the CURRENT SOURCE of the harness file(s) most likely responsible.

Decide whether a concrete, MINIMAL, surgical code/config change would fix or durably mitigate the \
problem. Prefer the smallest change that addresses the ROOT CAUSE, not the symptom.

Return ONLY this JSON object:
{
  "actionable": true|false,
  "root_cause": "one or two sentences",
  "edits": [{"file": "<repo-relative path from the sources list>", "old_string": "<VERBATIM unique substring of that file>", "new_string": "<replacement>", "reason": "<why>"}],
  "rationale": "why this change fixes the root cause",
  "tier": 1|2,
  "blast_radius": "what this change could affect if wrong",
  "verification_recipe": "how a human should FUNCTIONALLY test this (a concrete command/observation), since automated checks here only cover syntax",
  "confidence": "low"|"medium"|"high"
}

HARD RULES:
- old_string MUST be copied VERBATIM from the provided source and be UNIQUE in that file (include \
enough surrounding context to be unique). If you cannot anchor an edit exactly, set actionable=false.
- Keep edits minimal and reversible. Do not reformat or touch unrelated lines.
- NEVER propose changes to credentials/auth, the confined workspace-confinement sandbox, systemd units, or the \
model pin — if the only real fix is one of those, set actionable=true but explain it in root_cause \
and leave edits=[] (a human must handle it).
- If the problem is already handled by the current source, is operational/human (not a code bug), or \
no minimal code change is warranted, set actionable=false with a one-line root_cause explaining why.
- Be conservative: an INDEPENDENT reviewer will judge your diff without seeing this reasoning.

PROBLEM SIGNAL:
{signal}

CURRENT SOURCE:
{sources}
"""

VERIFIER_PROMPT = """You are an INDEPENDENT reviewer in the fleet's meta-harness loop. You are given a \
detected operational problem, a PROPOSED DIFF, and the automated verification results. You do NOT see \
the proposer's reasoning. Judge the diff on its own merits.

Return ONLY this JSON object:
{
  "addresses_root_cause": true|false,
  "safe": true|false,
  "minimal": true|false,
  "concerns": ["..."],
  "recommend": "present"|"hold",
  "verification_confidence": "low"|"medium"|"high"
}
"present" = worth sending to the owner to review/apply. "hold" = do not send (wrong, unsafe, or the \
diff does not match the problem). verification_confidence reflects how sure you are the change is \
correct GIVEN that automated checks here cover only syntax, not runtime behavior.

DETECTED PROBLEM:
{signal}

PROPOSED DIFF (unified):
{diff}

AUTOMATED VERIFICATION:
{verify}
"""


def _now():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _read_source(rel):
    p = os.path.join(PROJ, rel)
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _lesson_signals(window_days):
    """Harness-relevant failures-lessons from the infra workspace, as signals with a file anchor.
    Only lessons whose text maps to a known harness file (KW_SOURCES) become proposable."""
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=window_days)).date().isoformat()
    out = []
    for f in sorted(glob.glob(f"{LESS_DIR}/*-failures.json")):
        date = os.path.basename(f)[:10]
        if date < cutoff:
            continue
        try:
            items = json.load(open(f))
        except Exception:
            continue
        for it in items:
            if (INFRA_WS and it.get("workspace") != INFRA_WS) or it.get("confidence") == "low":
                continue
            text = f"{it.get('lesson','')} {it.get('evidence','')}"
            sources = next((s for rx, s in KW_SOURCES if rx.search(text)), None)
            if not sources:
                continue
            fp = "lesson:" + hashlib.sha1(it.get("lesson", "").encode()).hexdigest()[:12]
            out.append({"signal_id": fp, "fingerprint": fp, "pattern_id": "lesson",
                        "component": "lesson", "severity": 2, "count": 1,
                        "first_ts": None, "last_ts": date + "T00:00:00Z",
                        "title": (it.get("lesson", "")[:140]), "log": os.path.basename(f),
                        "sources": sources,
                        "evidence": [it.get("evidence", "")[:300],
                                     it.get("how_to_apply", "")[:300]]})
    return out


def _gather(window_days):
    sigs = telemetry.scan(window_days=window_days) + _lesson_signals(window_days)
    # highest severity, then most recurrent, first
    return sorted(sigs, key=lambda s: (s["severity"], s["count"]), reverse=True)


def _seen_fingerprints():
    seen = set()
    if os.path.exists(PROPOSALS_LOG):
        for line in open(PROPOSALS_LOG):
            try:
                seen.add(json.loads(line)["fingerprint"])
            except Exception:
                pass
    return seen


def _syntax_check(rel, content):
    """Per-file syntax check of proposed content. Returns (ok, detail)."""
    suffix = ".py" if rel.endswith(".py") else ".sh"
    is_py = rel.endswith(".py")
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as tf:
        tf.write(content)
        tmp = tf.name
    try:
        if is_py:
            cmd = ["python3", "-m", "py_compile", tmp]
        else:
            cmd = ["bash", "-n", tmp]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return (r.returncode == 0, (r.stderr or r.stdout).strip()[:300])
    except Exception as e:
        return (False, f"syntax check could not run: {e}")
    finally:
        os.unlink(tmp)


def _verify_edits(edits):
    """Engine gate + isolation check. For each edit: old_string must be a VERBATIM, UNIQUE substring
    of the real file (fabrication/ambiguity fails here regardless of any verdict); the edited file
    must still pass its syntax check. Returns (verify_dict, unified_diff_text)."""
    v = {"apply_ok": True, "syntax_ok": True, "details": [], "tier0_touched": []}
    diffs = []
    for e in edits:
        rel, old, new = e.get("file", ""), e.get("old_string", ""), e.get("new_string", "")
        if TIER0_RE.search(rel):
            v["tier0_touched"].append(rel)
        src = _read_source(rel)
        if src is None:
            v["apply_ok"] = False
            v["details"].append(f"{rel}: file not found")
            continue
        n = src.count(old)
        if n == 0:
            v["apply_ok"] = False
            v["details"].append(f"{rel}: old_string not found (fabricated/misquoted)")
            continue
        if n > 1:
            v["apply_ok"] = False
            v["details"].append(f"{rel}: old_string not unique ({n} matches) — ambiguous")
            continue
        patched = src.replace(old, new, 1)
        ok, detail = _syntax_check(rel, patched)
        if not ok:
            v["syntax_ok"] = False
            v["details"].append(f"{rel}: SYNTAX FAIL — {detail}")
        else:
            v["details"].append(f"{rel}: applies cleanly, syntax OK")
        diffs.append(_unified(rel, src, patched))   # unified diff for the dossier
    return v, "\n".join(d for d in diffs if d)


def _unified(rel, a, b):
    import difflib
    return "".join(difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True),
                                        fromfile=f"a/{rel}", tofile=f"b/{rel}"))


def _dossier(pid, sig, maker, verify, diff, verdict):
    lines = [f"# Meta-harness proposal {pid}", "", f"_generated {_now()} — Phase 1 (dossier only; "
             "not applied)_", "", "## Signal",
             f"- **{sig['title']}**",
             f"- fingerprint `{sig['fingerprint']}` · severity {sig['severity']} · "
             f"{sig['count']}× · {sig.get('first_ts')}→{sig.get('last_ts')} · log `{sig['log']}`",
             "- evidence:"]
    for ev in sig["evidence"]:
        if ev:
            lines.append(f"  - `{ev}`")
    lines += ["", "## Diagnosis", f"- **root cause:** {maker.get('root_cause','')}",
              f"- **rationale:** {maker.get('rationale','')}",
              f"- **tier:** {maker.get('tier','?')} · **confidence:** {maker.get('confidence','?')}",
              f"- **blast radius:** {maker.get('blast_radius','')}"]
    if verify.get("tier0_touched"):
        lines.append(f"- ⚠ **TIER-0 files touched (high-scrutiny, do NOT auto-apply):** "
                     f"{', '.join(verify['tier0_touched'])}")
    lines += ["", "## Proposed diff", "```diff", diff.rstrip() or "(no edits — flagged for human)",
              "```", "", "## Automated verification (syntax + apply-ability only)",
              f"- apply cleanly: {'YES' if verify['apply_ok'] else 'NO'}",
              f"- syntax OK: {'YES' if verify['syntax_ok'] else 'NO'}"]
    for d in verify["details"]:
        lines.append(f"  - {d}")
    lines += ["", "## Independent verifier",
              f"- addresses root cause: {verdict.get('addresses_root_cause')}",
              f"- safe: {verdict.get('safe')} · minimal: {verdict.get('minimal')}",
              f"- recommend: **{verdict.get('recommend')}** · verification confidence: "
              f"{verdict.get('verification_confidence')}",
              f"- concerns: {'; '.join(verdict.get('concerns', []) or ['none'])}",
              "", "## Functional test for the human (run before applying)",
              maker.get("verification_recipe", "(none provided)"),
              "", "---", "_Phase 1: apply by hand after review. No auto-apply._"]
    return "\n".join(lines)


def _deliver_main(pid, sig, maker, verify, verdict, dossier_path):
    msg = (f"[meta-harness] PROPOSAL {pid} — {sig['title']}\n"
           f"Signal {sig['fingerprint']} ×{sig['count']} ({sig.get('first_ts')}→{sig.get('last_ts')}), "
           f"log {sig['log']}.\n"
           f"Root cause: {maker.get('root_cause','')[:240]}\n"
           f"Proposed: {len(maker.get('edits', []))} edit(s) to "
           f"{', '.join(sorted({e.get('file','?') for e in maker.get('edits', [])})) or '(none)'} | "
           f"tier {maker.get('tier','?')} | confidence {maker.get('confidence','?')}\n"
           f"Auto-verify: apply {'OK' if verify['apply_ok'] else 'FAIL'}, "
           f"syntax {'OK' if verify['syntax_ok'] else 'FAIL'}. "
           f"Independent verifier: {verdict.get('recommend')} "
           f"({verdict.get('verification_confidence')} confidence).\n"
           + (f"⚠ TIER-0 touched: {', '.join(verify['tier0_touched'])} — high-scrutiny only.\n"
              if verify.get("tier0_touched") else "")
           + f"Dossier: {dossier_path}\n"
           f"ACTION (Phase 1, no auto-apply): review + apply by hand, then relay to the operator for sign-off.")
    runlog.notify_main(msg)


def run(dry=False, cap=PROPOSE_CAP, window_days=7):
    """Detect harness problems, propose+verify a fix for each, deliver a dossier to main. Dossier
    only — nothing is applied. `dry` also skips delivery + the proposals-ledger write."""
    os.makedirs(DOSSIER_DIR, exist_ok=True)
    with runlog.Run("harness", dry=dry) as run:
        seen = _seen_fingerprints()
        signals = _gather(window_days)
        run.count("signals_detected", len(signals))
        fresh = [s for s in signals if s["fingerprint"] not in seen]
        run.count("signals_fresh", len(fresh))
        run.count("units_due", min(len(fresh), cap))
        built = 0
        for sig in fresh:
            if built >= cap:
                run.note(f"cap {cap} reached — {len(fresh) - built} fresh signals wait for next run")
                break
            try:
                srcs = []
                total = 0
                for rel in sig["sources"]:
                    txt = _read_source(rel)
                    if txt is None:
                        continue
                    txt = txt[:MAX_SRC_CHARS - total]
                    total += len(txt)
                    srcs.append(f"=== {rel} ===\n{txt}")
                    if total >= MAX_SRC_CHARS:
                        break
                sig_json = json.dumps({k: sig[k] for k in
                                       ("title", "component", "count", "first_ts", "last_ts",
                                        "evidence", "log")}, indent=1)
                maker = agentcall.call_json(
                    MAKER_PROMPT.replace("{signal}", sig_json).replace("{sources}", "\n\n".join(srcs)),
                    shape=dict, stage="harness-maker")
                if not maker.get("actionable"):
                    run.count("not_actionable")
                    run.note(f"{sig['fingerprint']}: not actionable — {maker.get('root_cause','')[:120]}")
                    continue
                edits = maker.get("edits") or []
                verify, diff = _verify_edits(edits) if edits else (
                    {"apply_ok": True, "syntax_ok": True,
                     "details": ["no edits — flagged for human (likely tier-0)"], "tier0_touched": []}, "")
                verdict = agentcall.call_json(
                    VERIFIER_PROMPT.replace("{signal}", sig_json)
                    .replace("{diff}", diff or "(no diff — flagged for human review)")
                    .replace("{verify}", json.dumps(verify, indent=1)),
                    shape=dict, stage="harness-verifier")
                pid = ("hp-" + datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
                       + "-" + sig["fingerprint"].split(":")[-1][:8])
                dossier = _dossier(pid, sig, maker, verify, diff, verdict)
                built += 1
                run.count("outputs")
                if dry:
                    print(f"\n===== DRY proposal {pid} =====\n{dossier}\n")
                    continue
                path = f"{DOSSIER_DIR}/{pid}.md"
                with open(path, "w") as fh:
                    fh.write(dossier)
                rec = {"id": pid, "fingerprint": sig["fingerprint"], "ts": _now(), "status": "open",
                       "title": sig["title"], "tier": maker.get("tier"),
                       "confidence": maker.get("confidence"), "recommend": verdict.get("recommend"),
                       "apply_ok": verify["apply_ok"], "syntax_ok": verify["syntax_ok"],
                       "tier0_touched": verify.get("tier0_touched", []), "dossier": path}
                with open(PROPOSALS_LOG, "a") as fh:
                    fh.write(json.dumps(rec) + "\n")
                _deliver_main(pid, sig, maker, verify, verdict, path)
                run.count("delivered")
            except agentcall.Capped as e:
                run.cap(e)
                break
            except Exception as e:
                run.error(sig["fingerprint"], e)
        return run
