# Loop patterns — primitives, archetypes, reflect recipes

## Contents
- Choosing the primitive
- Archetype: gen-verify (the Karpathy loop)
- Archetype: drain-until-dry
- Archetype: watch-react
- Archetype: scheduled pipeline
- Archetype: bilevel (outer loop over an inner loop)
- Verifier menu
- Stop-condition menu
- Cost control

## Choosing the primitive

| You need | Primitive | Trigger | Stops when |
|---|---|---|---|
| One bounded task now | turn-based (just work) | user/agent prompt | you judge done — cheapest, default |
| Iterate to a measurable target | goal loop (`/goal`, or a while-loop in your prompt) | one kickoff | metric reached OR max tries |
| Recurring/polling work | interval (`/loop Nm`, `/schedule`, cron/systemd timer, ScheduleWakeup) | clock | cancelled or work exhausted |
| React to a state change | event-driven (Monitor on a log/socket, harness notification, inbox message) | the event itself | watcher closed |

Rules of thumb: **react to events rather than time** when an event source exists; pick intervals
from how fast the watched thing actually changes; a goal loop needs a *verifiable* exit criterion
or it is just a turn-based task wearing a costume.

## Archetype: gen-verify (the Karpathy `autoresearch` loop)

For anything with an objective metric (perf, test coverage, benchmark score, size, latency):

1. Freeze the eval: a `never-touch` verifier script + held-out data.
2. Loop pass: read loop-log → form ONE hypothesis → apply the smallest change that tests it →
   run verifier → append `{attempt, diff-summary, metric, verdict, next}` to the log.
3. Keep only verified improvements; revert failures immediately (never stack unverified changes).
4. Expect a low hit-rate by design (autoresearch: 700 experiments → 20 improvements — that ratio
   is HEALTHY; the loop's job is to make attempts cheap, not to be right every time).

## Archetype: drain-until-dry

For queue/backlog shapes (fix all X, migrate all Y, process the staged pool):

- State = the queue itself + a processed-ledger (done/empty/error per unit — error retries, with
  a poison cap so one bad unit can't burn every pass forever).
- Stop = queue empty OR K consecutive passes with zero new completions ("dry" rule).
- Batch small; checkpoint the ledger after every unit, not every pass — caps must resume, not redo.

## Archetype: watch-react

For monitoring (logs, CI, an external service, another agent):

- Prefer a blocking watcher (tail/inotify/Monitor/webhook) over polling; if polling, interval =
  the watched thing's real change-rate.
- The react arm must cover EVERY terminal state, not just the happy path — silence must be
  distinguishable from "still fine" (emit heartbeats or judge staleness).
- Debounce before acting (2 consecutive observations) and put a dwell time after each action —
  undebounced reactors flap.

## Archetype: scheduled pipeline

For nightly/weekly digestion (harvest, review, report):

- Stages isolated: one stage crashing marks itself red and the rest still run.
- Every run writes a run-record (status, counters, errors) — silence structurally impossible.
- A capped/interrupted run checkpoints and RESUMES; it never silently skips its window.

## Archetype: bilevel (the evolvable part)

The outer loop's only job is improving the inner loop. Run it every N inner passes or on a
dry-stop:

1. Read the whole loop-log (not the last pass — the *pattern* of passes).
2. Diagnose: wasted-effort classes? repeated hypothesis families that never pay? a missing tool
   or mechanism? a cadence/batch-size mismatch? verifier too slow (dominating pass time)?
3. Apply ONE structural change to the inner loop (prompt, ordering, cadence, tooling, split into
   writer/reviewer, add a pre-filter) — smallest change that addresses the diagnosis.
4. A/B honestly: run the tuned inner loop; if the metric-per-pass or cost-per-improvement did not
   improve, REVERT the tune (the outer loop is itself gen-verify — its metric is inner-loop
   efficiency).
5. Hard limits: outer loop may never touch the verifier, never widen its own authority, never
   remove stop conditions, never cross an owner gate. Log every tune with before/after.

## Verifier menu (strongest first)

test suite green · numeric metric from a frozen script · build/compile success · lint/typecheck ·
screenshot diff at fixed viewport · external state probe (endpoint says done) · independent
second-agent review (never sees your reasoning) · checklist verified line-by-line against
artifacts (weakest — use only when nothing mechanical exists, and quote evidence verbatim).

## Stop-condition menu (compose ≥2)

metric target reached · max passes N · max wall-clock / max tokens · K consecutive no-gain passes ·
queue empty · error-rate ceiling (>20% of passes erroring = stop and escalate) · human interrupt
marker (a hold file the loop checks every pass).

## Cost control

Write specific prompts (vague prompts buy extra turns). Batch same-shape units per pass. Route
mechanical passes to a cheaper model, judgment passes to the strong one. Cache what each pass
re-derives (put it in the loop-log header). Pilot 3 passes before authorizing 300. Review spend
per improvement, not spend per pass — a cheap loop that never lands anything is the expensive one.
