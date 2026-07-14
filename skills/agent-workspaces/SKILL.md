---
name: agent-workspaces
description: Operate the agent fleet — create, start, stop, and message persistent agent workspaces; choose the right isolation tier; pause work safely; keep sessions resumable. Use when asked to spin up an agent, message another agent, pause or resume the fleet, set up a confined workspace, or when a workspace is stuck, missing, or has lost its context.
---

# Agent workspaces

A **workspace** is a persistent agent: a tmux session (`agent-<name>`) + a descriptor
(`~/.agents/projects/<name>.env`) + **one conversation that resumes forever** via a fixed
`SESSION_ID`. Restarts, reboots, and account switches all resume that same conversation.

Full docs ship with this kit: `docs/OPERATING.md` (day-to-day), `docs/ISOLATION.md` (the tiers),
`docs/MESSAGING.md`, `docs/ACCOUNTS.md`, `docs/ARCHITECTURE.md`.

## Commands

```bash
agentctl status                       # who exists, up/down, account, model
agentctl new <n> --root PATH --up     # create a project workspace and start it
agentctl new <n> --confined "Name"    # create a CONFINED workspace (bwrap namespace)
agentctl login <n>                    # one-time OAuth for a confined workspace
agentctl up|stop <n>                  # start / transient stop (descriptor stays listed)
agentctl down <n>                     # retire: stop + archive descriptor (up again = same convo)
agentctl attach <n>                   # drive it yourself (Ctrl-b d leaves it running)
agentctl send <to> "text"             # deliver INTO its conversation (to: name | a,b | @all | @human)
agentctl read <n> [lines]             # read its pane
agentctl msglog [N] [--from|--to]     # the durable message log
agentctl flush <n> | kick <n>         # redeliver queued messages | submit stranded input
```

## Choosing an isolation tier

| Work | Tier | How |
|---|---|---|
| Yours; cross-agent visibility is fine | **project** | `agentctl new x --root ~/work/x --up` |
| Someone else's, regulated, or must not leak | **confined** | `agentctl new x --confined "Name" --up` |
| Must run on other hardware | **remote** | `AGENTS="claude-remote"` + `SSH="user@host"` |

**Confinement is a namespace, not a promise.** An unconfined agent asked for a fact it was never told
will grep the filesystem — including other agents' transcripts — because that is the cheapest way to
answer. If work must not leak, a prompt instruction will not save you; a bwrap namespace will.

## Rules that keep a fleet healthy

- **Never hand-edit a live descriptor's `SESSION_ID`.** It is the conversation's identity; losing it
  loses the context, which is the most expensive thing the workspace owns.
- **Checkpoint to disk, not to context.** An agent can be bounced at any moment (reboot, swap, crash).
  Keep a `STATE.md` in the workspace: objective · what's done · **what to do next**. Write it before
  long operations; read it first on resume. An agent that keeps its plan only in its head loses it.
- **Pause work with `fleet-hold`; quiet the watchdog with `watch-freeze`.** Different switches on
  purpose: a hold pauses *work* (post-swap nudges say "stay idle") and never disarms rotation or
  failover; `watch-freeze <duration>` stops the watchdog *visibly* and books its own auto-thaw, so it
  cannot be left off by accident.
  ```bash
  fleet-hold on --for 6h "deploying"    fleet-hold status    fleet-hold off
  watch-freeze 45m "maintenance"        watch-freeze thaw
  ```
- **Before auto-resuming work on session start, check the hold:** `fleet-hold --active`
  (rc 0 = a hold is on → checkpoint and stay idle).
- **One Remote-Control session per workspace.** Account moves orphan the old bridge server-side; the
  fleet reports the list for manual cleanup after a swap.

## When something is wrong

| Symptom | Do |
|---|---|
| Agent idle, never picked up a message | `agentctl kick <n>` — input stranded, unsubmitted |
| Agent came back but isn't working | It idles at the prompt until spoken to — `agentctl send` it |
| Messages "sent" but no effect | `agentctl msglog` is the record; don't debug by eyeballing panes |
| Workspace missing after reboot | Needs `AUTOSTART="yes"`; `agentctl up <n>` |
| Lost context / "what was I doing?" | Its `STATE.md` — and enable `session-guard.timer` |
