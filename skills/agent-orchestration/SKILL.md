---
name: agent-orchestration
description: "Recursive principal-subordinate orchestration for multi-agent / multi-PR work. Load when delegating substantive code, audit, or research work to a subordinate agent — OR when you ARE a subordinate that may spawn its own — OR when coordinating 2+ concurrent subordinates. Load BEFORE writing the brief, not after the subordinate comes back broken."
allowed-tools: Bash, Read, Write, Edit, Agent, ToolSearch, TaskCreate, TaskUpdate, TaskList, TaskGet, TaskOutput, SendMessage, Monitor
user-invocable: true
version: 3.0.0
---

# agent-orchestration

Drive long-running work by spawning subordinates — at any depth — without half-baked returns, file
collisions, or authority overreach. Distilled from real multi-PR agent campaigns.

## Architecture (one paragraph)

There is ONE role: **agent**, seen from two sides. Every agent has a **principal** (whoever spawned it;
the user at the root) and may become a principal by spawning subordinates. **Work flows down as briefs;
deliverables flow up as return contracts; authority flows down from the brief — default-deny:**

> Whatever your principal's brief grants, you have. Whatever it doesn't, you don't.
> No implicit inheritance, no role-derived authority, no self-promotion. A subordinate cannot grant
> authority it doesn't hold. Need more mid-task? Return and ask; principal grants explicitly via
> SendMessage (scoped, time-bounded, acknowledged, revocable).

Root-owned by default (delegatable only by explicit scoped grant): merge to `main` · production deploys ·
live-DB writes (single-writer) · cross-PR conflict resolution · reporting to the user · durable task state.

## How to spawn

| Job shape | agent type | isolation | bg | returns |
|---|---|---|---|---|
| End-to-end PR (default) | general-purpose | worktree | yes | PR URL + CI-green + contract |
| Audit / research / design doc | general-purpose | worktree | yes | doc path + headline + rec |
| Quick code search | Explore | — | no | inline |
| Plan only | Plan | — | no | plan text |

**Don't spawn** for: single-file edits you have context for, 3-line fixes, anything where the brief takes
longer than the work, quick searches, or work needing the principal's durable state. **Do spawn** for: a
PR's worth of self-contained work, parallelizable work, or long tasks that would idle you.

## The brief (every section, briefly)

1. **Role + verbatim framing** — quote your principal's ask under a header; paraphrase strips intent.
2. **Prior hypothesis** (if known) — "validate or disprove early", never "assume".
3. **Required reading FIRST** — exact paths to prior PRs/diagnosis docs/invariants. Highest-leverage section. "Build on; don't re-litigate."
4. **Diagnostic plan** — reproduce → baseline → diagnose → commit a DIAGNOSIS.md in the same PR.
5. **Acceptance criteria** — testable bullets, each citing its source spec/PR. Not vibes.
6. **Validation** — name the evidence: exact commands, screenshot paths, measurement format. ("Sharper edges" = vibes; "side-by-side at 3 zooms under xvfb+swiftshader saved at `<path>`" = testable.)
7. **MUST-NOT block** — explicit revocations (see snippet below).
8. **Authority grant** — explicit affirmative permissions. Not in §7 or §8 ⇒ forbidden (default-deny).
9. **Coordination heads-up** — when concurrent subordinates share a file area: name the other branches, assign lanes, "rebase before push; first-to-land wins."
10. **Numbered workflow** — ends with: *poll CI to terminal state; do NOT return with "CI pending".* The single most-violated line.
11. **Return contract** — PR URL · one-line root cause · one-line fix · evidence paths · invariants check. <200 words.

### Paste-ready MUST-NOT block
```
- Do NOT touch <files locked by prior PRs / concurrent subordinates>.
- Do NOT write to any production DB (ephemeral tmp DBs only).
- Do NOT run deploy.sh / vercel deploy / any production command.
- Do NOT merge PRs unless §8 grants scoped merge authority.
- Do NOT add WebGL specs to default CI (use the testIgnore carve-out pattern).
- Do NOT add dependencies without justification in the PR body.
- Do NOT use the Anthropic API — all LLM work is `claude -p` (Max sub, $0).
- Do NOT return prematurely with "CI pending — will notify."
- Do NOT spawn subordinates, or grant them authority, beyond what §8 grants you.
```

### CI poll loop (give to every PR subordinate)
```bash
while :; do
  r=$(timeout 40 gh pr view <N> --json statusCheckRollup \
      --jq '[.statusCheckRollup[]?|.status]|join(",")' 2>/dev/null || echo ERR)
  case "$r" in
    *IN_PROGRESS*|*QUEUED*|*PENDING*|ERR|"") sleep 30 ;;
    *) gh pr view <N> --json statusCheckRollup \
       --jq '.statusCheckRollup[]?|"\(.name): \(.conclusion)"'; break ;;
  esac
done
```

## Composing multiple subordinates — feature-branch integration

Phase needs N sub-PRs → principal opens feature branch; subordinates PR against IT, not main; principal
merges each as CI greens, validates the *integrated* result, then ships ONE PR upward. Atomic rollback,
one PR per phase in the audit trail. Typical scoped grant: "merge sub-PRs into `feat/<phase>` only."

## Failure modes → prevention (all real; all recur at every layer)

| # | Failure | Prevention |
|---|---|---|
| 1 | **Premature return** ("Monitor will notify") — the most-cited failure (PRs #16/#20/#22/#25) | §10 poll-to-terminal-state line + the poll loop |
| 2 | WebGL spec blows default CI (no GPU) | Required-reading: playwright testIgnore carve-out pattern |
| 3 | Writes prod DB / deploys / merges unauthorized | §7 + §8; default-deny |
| 4 | Wrong tool paths / test config | Hand over literal commands in the brief |
| 5 | Re-derives prior context | §3 required reading with explicit prior-doc paths |
| 6 | File-lane collision between concurrent subordinates | §9 lanes; first-to-land wins, second rebases |
| 7 | Assumes Anthropic API (adds billing infra) | Lock invocation: `claude -p` via Max, no API key |
| 8 | Skips validation because "obvious" | §6 names the evidence files that must exist |
| 9 | Stale worktrees accumulate | Principal cleanup pass after the batch merges (`git worktree unlock` + `remove --force`; never an active one) |
| 10 | `vercel deploy` project collision from wrong cwd | cat `.vercel/project.json` first; `--scope` explicit |
| 11 | **Subordinate dies mid-task** (socket/timeout) | Principal completes the tail: inspect worktree → validate → commit/push/PR yourself, or respawn with "continue from <path>" |
| 12 | Exceeds granted authority | Default-deny; if observed, verify the work manually + name the violation in the next brief |

## After a subordinate returns

1. Read the return contract. 2. Verify CI actually green (`gh pr view N --json statusCheckRollup`) —
if not, it returned prematurely; SendMessage it back. 3. Check mergeability; rebase/resolve trivial
conflicts yourself. 4. Merge into YOUR layer's target (main at root; feature branch intermediate).
8. Worktree cleanup when the batch is done.

Blocker instead of a PR? Resume via SendMessage with more context, respawn with a sharper brief, or
escalate to your principal. Don't silently back-fill their work — except failure mode #11.

## "Full authority" grants (user → root only)

"You have full authority / full autonomy / use best judgment / setting you off overnight" = maximal scoped
delegation: auto-cross human-gated phase boundaries as proxy reviewer (using the documented acceptance
criteria — not relaxing them), decide novel questions (prefer most-reversible), spawn freely, don't
round-trip "should I?".

**Still locked even then:** destructive git ops on main · data loss / single-writer DB violations ·
Anthropic API when `claude -p` is available · skill self-modification beyond changelog · deploys that fail
the safety protocol · anything the user previously named off-limits.

**Required practice:** echo the grant back verbatim; keep a durable session log (grant + decisions D-1…D-n
with rationale/reversibility + event stream); halt-and-surface on any locked-convention breach or failed
acceptance criterion; morning summary at window end. User's next message ends the window.

## Changelog
- 3.0.0 (2026-07-08): condensed 633→~140 lines; same rules, all narrative/duplication removed.
- 2.0.x (2026-05-30): recursive principal-subordinate model, default-deny, scoped delegation, full-authority protocol.
- 1.0.0 (2026-05-29): initial fixed three-role version.
