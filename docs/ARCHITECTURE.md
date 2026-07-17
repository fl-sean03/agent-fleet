# Architecture

## The shape

```
      you ──► main (coordinator agent) ──► the other agents
                          │
   ┌──────────────────────┼───────────────────────────────┐
   │                      │                               │
 agentctl            fleet-msg                    background services
 (control)      (agent↔agent delivery)     watchdog · guard · brain · idle-down
   │                                                      │
   ▼                                                      ▼
 tmux sessions + descriptors                    ~/.agents state + <kit>/memory
 (one persistent conversation each)
```

Everything is bash + Python stdlib over **tmux** and **files**. There is no daemon, no database
server, no message broker. If you can read a file, you can debug the fleet.

## The unit of work: a workspace

A workspace is **a descriptor + a tmux session**.

`~/.agents/projects/<name>.env`:

```bash
ROOT="$HOME/work/api"        # where it lives and works
AGENTS="claude"              # which harness → runs bin/run-claude
REMOTE_CONTROL="on"          # mobile bridge
RC_NAME="API"
AUTOSTART="yes"              # returns after reboot
SESSION_ID="a1b2…"           # assigned ONCE — the conversation, forever
MODEL="claude-opus-4-8"      # survives every relaunch
# ACCOUNT="account-a"        # omit → fleet-active account
```

The tmux session `agent-<name>` has two panes: a control shell and the agent itself.

**The fixed `SESSION_ID` is the core design decision.** Every launch resumes *that exact
conversation* — not "the most recent," not a heuristic. Same workspace ⇒ same conversation across
restarts, reboots, account switches, and machine moves. It also gives every other tool a stable
handle: `swap-account` verifies the live process by session id, `session-guard` watches it, the brain
attributes memories to it.

## Who owns what

| Tool | Owns |
|---|---|
| `agentctl` | The control surface: create, up/down/stop, attach, send, status, login |
| `run-<agent>` | Launching one harness (claude / confined / remote / codex / opencode). **The agent-agnostic seam.** |
| `fleet-msg` | Delivering a message *into* a live conversation; queueing; the durable log |
| `session-guard` | Defending transcripts from loss; flagging high-context sessions before a bounce |
| `idle-down` | Spinning down workspaces nobody has touched |
| `fleet-hold` | Work-tempo pause (nudges + agent tempo). **Never** touches infrastructure |
| `watch-freeze` | Quieting the watchdog for maintenance — with a guaranteed auto-thaw |
| `account-watch` | *When* and *where* to rotate accounts (usage, caps, debounce, backoff) |
| `swap-fleet` / `swap-account` | *How* to move: ordered, verified, per-model-gated |
| `fleet-lib.sh` | The ONE implementation of every shared primitive (sourced, never executed) |
| `brain` | Turning transcripts into memory (see [BRAIN.md](BRAIN.md)) |

Two of those are deliberately split: **`account-watch` decides, `swap-fleet` executes.** Deciding and
doing have different failure modes and different tests.

## The shared library (consolidation, 2026-07-17)

Descriptor parsing, pane addressing, busy detection, process-tree liveness, RC re-registration, the
fable-cap marker read, the log format and the system-message envelope each used to exist as 3–6
slightly different copies across `bin/`. The differences were never design — they were drift, and
drift bites: one unanchored descriptor grep matched a *commented-out* `#RESUME-retired…` line and fed
a garbage session id into message provenance and idle detection for a confined workspace.

Now there is **one implementation**: `bin/fleet-lib.sh`, sourced by the eleven scripts that used to
carry private copies (`swap-fleet`, `account-watch`, `session-guard`, `idle-down`, `backup-watch`,
`agentctl`, `account-status`, `swap-account`, `fleet-hold`, `watch-freeze`, `provision-profile`).
What lives there:

- **Descriptor access** — `ws_get` and friends: anchored (a commented line can never match),
  quote-aware, inline-comment-safe. One parser, one set of bugs to fix.
- **Pane + liveness** — `fl_agent_pane` / `ws_busy` (one busy signature: `esc to interrupt`) /
  `fl_proctree` + `fl_agent_alive` (full recursive walk — tree depth varies by isolation tier).
- **Envelope send** — `fl_send` routes through `fleet-msg` as `system:<script>` (see
  [MESSAGING.md](MESSAGING.md)).
- **The dormancy gate** — `ws_active_within <ws> [hours]`: automated *wake* messages (post-swap
  resume nudges) go only to agents whose transcript was written in the last
  `FL_WAKE_ACTIVE_HOURS` (default 6). A dormant agent is swapped but not woken; the skip is
  logged with its idle age. Exempt by design: busy-at-bounce workspaces (observed mid-turn =
  in use), queued-message flush (delivery-of-record), and alert pages (the alarm channel).
- **Post-swap recovery** — `verify_submitted` (detect-and-log only: stranded text is LEFT VISIBLE,
  never auto-fired) and `verify_rc_retry` (re-trigger `/remote-control` after the transient
  first-registration failure) — extracted so nothing has to source `swap-fleet` past its swap lock.

Rules that keep it safe to source: no side effects at source time, everything reads `$A`/`$ACCTS` at
call time (test sandboxes always win), and the `fleet-lib-sane` invariant pages within one
`fleet-invariants` sweep if the lib ever fails to parse — it is load-bearing for eleven scripts, so
a syntax error there must be loud, not mysterious.

Retired in the same pass: the `input-watchdog` (automated composer submission — unsafe by design
because the Remote Control bridge can replay unsent drafts into composers; see MESSAGING.md) and
`run-claude`'s dead `RESUME=` legacy branch. Retired tools keep their history under
`attic/retired-tools/`.

## The agent-agnostic seam

`AGENTS="codex"` runs `bin/run-codex`. That's the whole mechanism. A launcher's job:

1. resolve the account/config for this workspace,
2. resume the fixed session (or create it with that id on first run),
3. apply the descriptor's model pin and Remote-Control name,
4. `exec` the CLI.

Everything above the launcher — workspaces, messaging, holds, status, idle-down, session protection —
is harness-independent. Shipped launchers: `claude`, `claude-confined`, `claude-remote`, `codex`,
`opencode`. Adding one is ~40 lines.

What is *not* yet agnostic, stated plainly: account rotation speaks the Claude usage endpoint, and
the brain's model call shells out to `claude -p`. Both sit behind one narrow interface each
(`bin/account-usage`, `brain/engine/agentcall.py`), so porting is contained — but it isn't done.

## State on disk

| Path | What | Survives? |
|---|---|---|
| `~/.agents/projects/` | Descriptors | The source of truth — back this up |
| `~/.claude/projects/` | The actual conversations (jsonl) | The real asset. Back this up. |
| `~/.agents/messages/` | Message log + SQLite | Durable provenance |
| `~/.agents/accounts/` | Account profiles, rotation state, logs | Only if you rotate |
| `~/.agents/session-archive/` | Scrollback + retired sessions | Recovery net |
| `<kit>/memory/<ws>/` | Distilled knowledge per workspace | Written by the brain |
| `~/.agents/fleet.conf` | Config (kit root, confined root, model) | Portability |

## Design rules the code actually follows

- **Fail loud, never silent.** A failed pipeline run writes a red run-record and exits nonzero. An
  ignored `.hold` file logs a warning every tick. Silence is treated as a bug.
- **Verify, don't trust "up".** A swap confirms the live process is running under the target config
  by reading `/proc/<pid>/environ` — not by assuming the command succeeded.
- **One writer per session file.** Two processes appending one transcript is how you lose history.
- **Debounce anything that triggers an action.** Two consecutive observations before acting, plus a
  dwell time after. Undebounced reactors flap.
- **Everything mechanical is tested.** 479 harness tests plus the brain's pytest suite, all against
  fixtures — no live credentials, no network.
