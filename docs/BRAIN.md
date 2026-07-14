# The second brain

Your agents generate a huge amount of hard-won knowledge — and then it dies in a transcript nobody
will ever read again. The brain is a nightly pipeline that reads your fleet's own conversations and
distills them into **durable, per-workspace memory** that future sessions actually load.

The premise: **self-improvement is a property of the system, not the model.** You don't retrain
anything. You improve what the agent *reads* (its memory, its rules, its skills) and what it *runs*
(its harness). This kit does the first, and prototypes the second.

```bash
brain nightly            # the whole chain
brain status             # health, ledgers, staged-pool depth
brain harvest --dry      # see what it would extract, without writing
systemctl --user enable --now brain-nightly.timer
```

## The chain

| Stage | Reads | Writes |
|---|---|---|
| **harvest** | every in-scope transcript (new/changed since last run) | staged candidate facts |
| **review** | each workspace's recent conversation + git log + logs | a daily digest + staged lessons |
| **failures** | the agent's own error→diagnosis→fix arcs | staged lessons (`failure-fix`) |
| **consult** | the staged pool | **promotes** into memory / rules / skills |
| **state** | the daily review | each workspace's resume pointer + open failures |
| **usage** | per-message token counts | a spend ledger (SQLite, priced at query time) |

Output lands in `<kit>/memory/<workspace>/`:

- `MEMORY.md` — the index (one line per memory; this is what loads into context)
- `STATE.md` — curated: verified facts, operating rules, open failures, **where to resume**
- `auto_*.md` — one durable fact each, loaded on relevance, each carrying a `## Provenance` section
  with the session id, timestamp, and a **verbatim quote**

## Why you can trust what it writes

This is the part that matters. An unsupervised memory-writer that hallucinates is worse than no
memory at all — and it degrades every future session.

1. **Anti-fabrication gate.** Every promoted claim must quote its source **verbatim**. The quote is
   checked as a literal substring of the actual transcript. If the model paraphrases, invents, or
   drifts, the item is **rejected by code** — not by a judge that can be talked around.
2. **Maker / independent verifier.** One call proposes what to write and where. A **second, separate**
   call — which never sees the first one's reasoning — approves or rejects it against the same
   evidence. Structural separation, not a politeness convention.
3. **The engine re-gates regardless of verdict.** Even if both model calls agree, the evidence check
   runs again in code before anything is written.
4. **Everything is reversible.** Every promotion is archived to `staged/promoted.jsonl` with
   before/after. Nothing is destroyed; a bad write can be walked back.
5. **Volume caps.** A bounded number of promotions per night. Knowledge accretes slowly and on
   purpose.

There is a real research finding behind this paranoia: *self-generated* agent skills have been
measured as **worse than nothing** (−1.3pp) while human-curated ones help (+16.2pp). Unsupervised
self-authoring is a known failure mode. Hence: gates, an independent verifier, caps, and a human in
the loop for anything structural.

## Confinement

The brain **never reads confined workspaces.** Their content cannot enter the shared knowledge base —
enforced structurally, with every exclusion counted rather than silent. See
[ISOLATION.md](ISOLATION.md#the-second-wall-the-knowledge-base) for why the naive version of this
check leaks, and how the wall actually works.

## Failing loud

Every run writes a record (`brain/state/runs/`) with status, counters, and errors, and updates
`brain/state/health.json`. A red run exits nonzero, systemd's `OnFailure` fires, and the coordinator
gets a message. A run interrupted by a rate limit **checkpoints and resumes** — it never silently
skips its window. Ledgers distinguish *done* from *empty* from *errored* (conflating "errored" with
"done with 0 results" is how a previous version silently lost data).

## Configuration

`~/.agents/fleet.conf`:

```
FLEET_KIT_ROOT=/home/you/agent-fleet     # memory/ and brain state live under here
BRAIN_MODEL=claude-opus-4-8              # the ONE model choke point (engine/agentcall.py)
```

Cost is a real consideration: the brain makes model calls proportional to how much your fleet talked.
Start with `brain harvest --dry` and `--limit`, watch `brain status`, then let it run nightly.

## The frontier: the meta-harness

`brain propose` is a prototype of the missing half — a loop that reads the fleet's **operational
logs** (not conversations), detects recurring harness problems, diagnoses them against the source,
writes a **concrete diff**, verifies it in isolation, has an independent verifier review it, and
delivers a dossier to the coordinator.

It **never auto-applies.** The autonomy is in the labor — detect, diagnose, write, verify — and the
authority stays with you. That is a deliberate line, and it's the line to keep: a loop that can edit
the machinery that runs it, without a human gate, is a loop that can quietly grant itself anything.

It is shipped as a working prototype, not a finished product. Read
`brain/pipelines/harness.py` before you turn it loose on anything.
