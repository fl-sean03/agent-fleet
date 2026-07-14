---
name: loop-engineering
description: Design a hyper-tailored, EVOLVABLE work loop for yourself — pick the right loop primitive, build an ungameable verifier, persistent state, and hard stop conditions, then add a bilevel reflect pass so the loop improves itself run over run. Use when setting up recurring/autonomous work (a nightly job, a watch-react monitor, a drain-until-done queue, an experiment loop), when asked to "keep doing X until Y", or when an existing loop stalls, overruns budget, or repeats mistakes.
---

# Loop Engineering

You are the engine only until the loop exists. A loop = **a goal pursued until arrival**: plan →
act → **verify** → repeat, with the objective defined once and iteration delegated. Karpathy's rule:
*if you have an objective metric, you should not be the one running the experiments — you are the
bottleneck.* This skill is how you design that loop **for yourself**, tailored to your workspace,
and make it **evolve** instead of merely repeat.

## The three load-bearing components (no loop ships without all three)

1. **Verifier — objective and ungameable.** Something that grades each pass WITHOUT trusting your
   self-assessment: tests pass, a metric file moves, a build succeeds, a screenshot diff, an
   external state check. **Structural rule (the autoresearch separation):** the loop must not be
   able to edit its own verifier — separate "what the loop may modify" from "what judges it"
   (agent edits `train.py`, never `prepare.py`). If the verifier is your own opinion, you will
   drift into self-deception by pass three.
2. **State — a persistent loop-log, resume not restart.** One file (e.g. `LOOP.md` or
   `loop-log.jsonl` in your workspace) recording: objective, per-pass attempt → result → verdict →
   next hypothesis. A loop with no memory file starts from zero every pass — the #1 failure mode
   in the wild. Your STATE.md resume block points at it.
3. **Stop condition — mandatory, mechanical.** Goal metric reached, OR max passes, OR budget
   ceiling, OR N consecutive no-improvement passes (dry-run rule). "Runs forever and bills
   forever" is the #2 failure mode. Write the stop condition into the loop prompt itself, not
   your intentions.

## Design worksheet (fill this before the first pass)

```
Loop Design:
- [ ] Objective (one sentence) + METRIC that proves it (numeric/boolean, externally checkable)
- [ ] May-modify set vs never-touch set (verifier + its data live in never-touch)
- [ ] Primitive: turn-based | goal-loop | interval | event-driven  (see references/patterns.md)
- [ ] Cadence matched to how fast the watched thing actually changes (not to your anxiety)
- [ ] Budget: max passes ___ · max tokens/$ ___ · dry-stop after ___ no-gain passes
- [ ] State file path: ___   Stop conditions written into the prompt: yes/no
- [ ] Escalation: what gets a human (owner gate, external action, security wall) vs what loops
- [ ] Fleet check: does this loop respect fleet-hold + account budget? (references/fleet.md)
```

## Making it EVOLVABLE — the bilevel pass

A fixed loop repeats; an engineered loop **improves itself**. The Bilevel Autoresearch result: an
outer loop that watches the inner loop's behavior and rewrites its *mechanisms* when it stalls
beat the identical single loop 5×, same model. Your version:

- Every N passes (or on a dry-stop), run a **REFLECT pass** instead of a work pass: read the
  loop-log, ask "where did passes waste effort? which hypothesis class never pays? what mechanism
  is missing?" — then **tune the loop itself**: reorder its steps, tighten its prompt, change
  cadence, add a tool, split in two, or kill it.
- Record every self-tune in the loop-log with before/after (reversible). The loop's own
  parameters are in its may-modify set; **the verifier and the authority gates are not.**
- Improvements land at the HARNESS and CONTEXT layers (your loop script/prompt, your memory and
  skills) — never "the model". That's where continual learning actually lives in production.
- Escalation stays sacred: a reflect pass may propose but NEVER self-grant wider authority,
  touch its verifier, or cross an owner gate (operating-surface changes need owner sign-off).

## Failure modes (each one observed in the wild — check yours against all six)

1. No memory file → every pass starts from zero.
2. No stop condition → runs forever, bills forever.
3. Gameable verifier → loop optimizes the test, not the goal (made the eval easier ≠ improved).
4. One agent does everything → no writer/reviewer split, no fresh-eyes verification.
5. Cadence mismatch → 60s polls on a thing that changes hourly (burn), or hourly polls on a
   deploy that finishes in minutes (stall).
6. Loop fights the fleet → auto-resumes during a hold, burns a capped account, or duplicates
   what a fleet service (rotation, brain, session-guard) already does.

## Go deeper

- **Primitive selection + loop archetypes + reflect-pass recipes**: [references/patterns.md](references/patterns.md)
- **Fleet bindings — MUST-follow invariants** (fleet-hold, budgets, STATE.md, escalation paths,
  live examples from this fleet's own production loops): [references/fleet.md](references/fleet.md)
