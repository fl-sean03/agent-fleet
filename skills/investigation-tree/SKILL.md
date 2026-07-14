---
name: investigation-tree
description: Methodology + living tracker for systematic investigation, debugging, and validation work where a result contradicts expectation and you must descend into confounds, remediate, and climb back to the goal. Use when a result is surprising/wrong, when debugging a multi-layer problem, when reproducing/calibrating against a reference, or any time the work keeps spawning new sub-investigations that risk losing the thread. Keeps a route doc with a branch tree, findings log, and scorecard so progress is never lost across sessions.
argument-hint: [action] (start|branch|close|status) — optional; describe the problem if starting fresh
allowed-tools: [Write, Read, Edit, Glob, Grep, Bash]
---

# Investigation Tree

A disciplined way to chase a contradiction to its root and back without losing the thread. Most hard debugging/validation work is a **tree**: the goal is the root, each suspected cause is a branch you descend into, isolate, and resolve — then you **climb back up** to the goal. The failure mode is getting lost mid-tree: forgetting which branches are open, concluding from weak evidence, or fixing the wrong thing because you assumed causality.

This skill is two things: (1) a **methodology** for navigating the tree rigorously, and (2) a **tracking format** so the tree survives context loss and hand-offs.

## When to use

- A result contradicts expectation, a reference, or a prior run.
- You're reproducing/calibrating against a published or external number.
- Debugging spans multiple layers (build → config → physics/logic → environment) and keeps spawning sub-questions.
- Any time you think "wait, I need to check X first" more than twice — that's a tree forming.

---

## Part 1 — Methodology (the 6 disciplines)

### D1. Frame the contradiction precisely before touching anything
Write one line: **expected E (with its source), observed O (with how measured), gap G.** Vague framing ("it's wrong") breeds flailing. "Reference says F slips at ~30 MPa (paper Fig 6d); we see no slip through 125 MPa (dump COM displacement <0.4 Å)" is a frame you can act on.

### D2. Get ground truth before concluding — distrust derived/aggregate metrics
The first metric you check is often the wrong one. A "no slip" verdict from a last-timestep velocity can be wrong when the real signal is cumulative displacement. **Before declaring a cause, confirm the symptom with the most direct measurement available** (raw positions, not smoothed averages; the actual file, not a summary). Independently re-measure anything a subagent or prior step reported.

### D3. Don't assume the direction of causality
The most expensive error: "fix" the thing that was already correct. Verify which side is the anomaly. (Real example: a build's params looked "wrong" vs a reference, but the *reference* was the older version and the build was the intended-new one — the "fix" was a silent regression.) Check provenance: which is canonical, which is older, what does the source-of-truth (the .frc, the spec, the upstream) actually specify? Convert/normalize both sides to the same representation and compare.

### D4. Isolate one variable against a known-good reference
To attribute a difference, change exactly one thing and re-run against something that *does* work. Swap our-structure into their-protocol; run their-input through our-pipeline; hold geometry fixed and vary only the FF. If two suspects co-vary, you can't attribute — separate them. Keep a "known-good" anchor (a passing case) to compare against.

### D5. Every change is reversible and verified
Back up before overwriting (`*.bak`, labeled variants — never an ambiguous name like `.fixed`). Make the change. **Verify it actually took** (re-read the value), and verify it didn't break an invariant (charge, counts, geometry). One change per step so a regression is attributable.

### D6. Know the escalation boundary
Separate **engineering/infra** decisions (sizing, routing, file layout, which queue) — make these autonomously — from **scientific/product-direction** decisions (is this model appropriate for this observable? does this change the claim?) — surface these to the human with the evidence, don't decide unilaterally. When a confound turns out to be a genuine choice rather than a bug, that's usually a D6 escalation.

---

## Part 2 — The tracking format (the route doc)

Maintain ONE living doc (e.g. `PLAN.md` / `ROUTE.md` / `INVESTIGATION.md`). It has six sections. Update it as work proceeds — it is the source of truth, not your memory.

### 2.1 Goal — the root
The objective + the reference/acceptance criterion, stated once. Everything else exists to reach this.

### 2.2 The route tree — where we've been and are
A diagram of branches with status. Each branch: **symptom → fix → result.** Mark exactly one **◀── WE ARE HERE**.

```
GOAL: <objective + acceptance criterion>
│
├─ [CLOSED]  Confound A: <symptom>
│     fix: <what was done>   result: <outcome + evidence> ✓
├─ [CLOSED]  Confound B: ...
├─ [ACTIVE]  Confound C: <symptom>            ◀── WE ARE HERE
│     diagnosis: <root cause, with verification>
│     remediation: <chosen path>
│        ├─ [RUNNING] <step>  (handle/PID/job id)
│        ├─ [NEXT]    <step>
│        └─ [THEN]    climb back up →
└─ [PENDING] climb back to GOAL: <remaining work>
```

Branch states: `RUNNING` (in flight, with a handle), `NEXT`/`THEN` (queued), `ACTIVE` (current focus), `CLOSED` (resolved, with result), `PARKED` (deliberately deferred — say why), `PENDING` (not started). **A branch never just disappears** — it closes with a result or is parked explicitly.

### 2.3 Headline finding(s)
The non-obvious things learned, with evidence and consequence. The reader who skims only this should understand what changed.

### 2.4 Inventory
Artifacts the work produced/depends on, and their roles — especially when multiple variants exist (canonical vs reference vs superseded). Name variants unambiguously; mark the canonical one. Note backups and "do not use" files.

### 2.5 Scorecard
The goal restated as a checklist/grid with current status per cell. This is the "are we there yet" view.

### 2.6 Next steps — the climb back up, in order
Numbered, ordered, each with a gate/acceptance test. Ends at the goal. This is what you (or the next agent) does next.

### 2.7 Findings log
Newest-first dated entries. Each remediation/discovery gets a line so the journey is reconstructable.

---

## Discipline for running/long work

- Every in-flight job records its **handle** (PID, job id, agent id) in the route doc, and has a **completion monitor** armed so you're notified rather than polling.
- When a job finishes, **update the tree** (branch RUNNING→CLOSED/NEXT) before starting the next thing.
- Independently verify long-running or delegated outputs against the acceptance criterion — don't promote on a summary alone (D2).

## The core loop

```
1. Frame the contradiction (D1)               → add branch to tree (2.2)
2. Ground-truth the symptom (D2)
3. Hypothesize cause; check provenance/direction (D3)
4. Isolate one variable vs known-good (D4)     → run; arm monitor
5. Remediate reversibly; verify (D5)           → close branch with result (2.2, 2.7)
6. Engineering → decide; scientific → escalate (D6)
7. Climb back up: re-check the goal/scorecard  → next branch or DONE
```

## Anti-patterns this prevents

- "Fixing" the already-correct side (D3) — verify causality direction first.
- Concluding from a smoothed/aggregate metric (D2) — get raw ground truth.
- Losing open branches across context compaction — the tree doc holds them.
- Co-varying two changes and being unable to attribute the effect (D4).
- Silently overwriting a good artifact (D5) — back up + label.
- Unilaterally making a product/scientific call that should be the human's (D6).
