# Fleet bindings — invariants every loop in THIS fleet must honor

## Contents
- Hard invariants (MUST)
- Checkpoint discipline
- Escalation map
- Production loops already running (reuse, don't duplicate)
- Worked example: tailoring a loop to your workspace

## Hard invariants (MUST)

1. **Respect the fleet hold.** Before any auto-resume or scheduled pass:
   `~/.agents/bin/fleet-hold --active` → rc 0 means a work-tempo hold is on: checkpoint and idle;
   do NOT start the pass. (Post-swap nudges are already hold-aware; your self-triggered passes
   must be too. Holds gate TEMPO only — never rotation/failover.)
2. **Respect the account budget.** Long/heavy loops check usage before big passes:
   `~/.agents/bin/account-usage --table` (5h/7d/Fable columns). Don't schedule a token-heavy pass
   into an account at 90%+ — the rotation watchdog will bounce you mid-pass; your loop must
   survive that bounce via its state file, not fight it.
3. **Stop conditions are not optional** — a fleet agent's runaway loop bills every workspace on
   the shared account. Compose ≥2 from the menu in patterns.md.
4. **Owner gates are structural.** A loop (especially a reflect/bilevel pass) may PROPOSE changes
   to operating surfaces — AGENTS.md, descriptors, bin/ tools, systemd units, credentials,
   confinement walls — but applying them requires owner sign-off, relayed via main. No loop
   self-grants authority. External actions (posting, emailing, publishing, confined workspace deliverables)
   are always owner-gated.
5. **One writer per session file** — never spawn a second claude into your own session as part of
   a loop mechanism. Subagents/one-shots: `claude -p --no-session-persistence` so loop passes
   don't litter the shared session store.

## Checkpoint discipline

- Loop state lives IN YOUR WORKSPACE (e.g. `LOOP.md` / `loop-log.jsonl`), and your memory
  STATE.md resume block points at it — an account swap relaunches you with `--resume`, and the
  post-swap nudge says "continue from your on-disk notes": the loop-log IS those notes.
- Checkpoint after every unit of work, not every pass. A capped pass must resume, never redo
  (ledger semantics: done / empty / error-retries / poison-cap).

## Escalation map

| Situation | Route |
|---|---|
| Loop needs an owner decision (policy, money, external action) | fleet-message main → the operator |
| Loop found something another workspace owns | `agentctl send <ws> "..."` directly |
| Loop's own error-rate ceiling hit | stop + message main with the loop-log tail |
| Infra/harness bug blocking the loop | message infrastructure |

Messages land IN the recipient's conversation (durable log: `agentctl msglog`). Don't build a
custom notification channel; don't spam heartbeats — message on state changes a human would act on.

## Production loops already running (reuse, don't duplicate)

| Loop | Shape | Owner |
|---|---|---|
| account-watch (5-min timer) | watch-react: usage/cap → debounced rotation | infrastructure |
| session-guard | watch-react: context% → stuck-risk alerts | infrastructure |
| brain nightly | scheduled pipeline: harvest→review→failures→consult→state→usage | infrastructure |
| idle-down (daily) | drain: spin down workspaces idle ≥N days | infrastructure |
| example-confined-nightly | scheduled job w/ cap-aware retry | example-confined |

If your loop wants to watch usage, detect stuck panes, or mine transcripts — these already do.
Extend or consume them (their logs/ledgers are readable) instead of building a twin.

## Worked example: tailoring a loop to your workspace

Say your workspace goal is "benchmark score ≥ X on suite S" (a ws-bench shape):

- **Verifier**: the frozen suite S runner + held-out answer key (never-touch; keys stay held-out —
  standing fleet rule). Metric: score file it emits.
- **May-modify**: your harness code, prompts, configs. Never: the suite, the keys, the scorer.
- **Primitive**: goal loop, max 25 passes, dry-stop after 4 no-gain passes.
- **State**: `loop-log.jsonl` — one line per pass: hypothesis, diff summary, score, verdict, next.
- **Fleet checks**: `fleet-hold --active` before each pass; usage check before the expensive eval;
  checkpoint per pass so a mid-pass account bounce resumes cleanly.
- **Reflect pass** (every 8 passes or on dry-stop): read the whole log; kill hypothesis families
  that never paid; consider a writer/reviewer split if self-review keeps passing bad diffs; tune
  batch size; log the tune; A/B it; revert if pass-efficiency didn't improve.
- **Escalation**: score regression you can't explain in 2 passes → message main with the log tail
  rather than thrashing.
