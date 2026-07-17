# Operating the fleet

How to actually live with this day to day. Read [QUICKSTART](QUICKSTART.md) first if you haven't
installed yet.

## Contents
- The mental model
- The coordinator pattern (`main`)
- Talking to agents
- Creating workspaces
- Holds: pausing the fleet without breaking it
- Checkpoint discipline (the thing that makes agents resumable)
- Working from your phone
- Maintenance
- Anti-patterns

## The mental model

An agent is not a chat window you visit. It is a **long-lived worker with one continuous
conversation**, a home directory, and a memory. You do not "start a session" — the session already
exists and has existed since the day you created the workspace. You *send it work* and *read its
output*, and it keeps its context across reboots.

Three consequences worth internalizing:

1. **Context is the asset.** A workspace that has been running for a month knows your project. Never
   casually destroy one (`agentctl down` archives its descriptor, but the conversation is the value).
2. **Agents idle at the prompt.** After a restart or a swap, an agent sits waiting until *spoken to*.
   If you want autonomy, you send it a nudge — that is what `AUTOCONTINUE` and the coordinator do.
3. **Everything is a file.** Descriptors, memory, logs, messages. If you can't explain where the
   state lives, you don't understand the system yet.

## The coordinator pattern (`main`)

The single highest-leverage habit: **create one workspace called `main` and treat it as your chief
of staff.** You talk to `main`; `main` talks to the fleet.

```bash
agentctl new main --root ~ --rc "Main" --up
```

What `main` does for you:
- **Dispatch**: "ask the ws-gpu agent to profile that kernel and report back" → it messages the agent,
  the agent works, it relays the answer.
- **Triage**: background services (rotation, brain, session guard) alert `main`, not you. It decides
  what's worth your attention and what's noise.
- **Relay**: agents that need an owner decision message `main`, which brings it to you with context.

Why this matters: with 15 agents, you cannot hold every thread. With one coordinator, you hold one.
The fleet's alerting is already wired to `main` by default (see `bin/*` — the alert paths all call
`agentctl send main`).

**A coordinator is not a manager.** It has no authority you didn't give it. It relays and dispatches;
you decide.

## Talking to agents

```bash
agentctl send ws-gpu "profile the matmul kernel and report the top 3 hotspots"
agentctl send a,b,c "standup: one line on what you're working on"
agentctl send @all "we're pausing for a deploy — checkpoint and idle"
agentctl read ws-gpu 60          # last 60 lines of its pane
agentctl status               # who's up, on what account, which model
```

The message lands **inside** the recipient's conversation as a real turn — it processes it like
anything you typed. A stopped agent's messages queue and flush when it comes back. Every message is
in a durable log (`agentctl msglog`). See [MESSAGING](MESSAGING.md).

Agents message each other the same way. That is how a fleet coordinates without you in the loop.

## Creating workspaces

```bash
# a normal project agent (full host access, mobile RC on)
agentctl new api --root ~/work/api --rc "API" --up

# a CONFINED agent — bwrap namespace, sees only its own dir + its own config
agentctl new exampleco --confined "ExampleCo Corp" --up
agentctl login exampleco            # one interactive login; its credentials never touch your others

# a non-Claude agent
printf 'ROOT="$HOME/work/x"\nAGENTS="codex"\n' > ~/.agents/projects/x.env && agentctl up x
```

Pick **confined** whenever the work must not see your other work: client engagements, regulated data,
anything you'd be embarrassed to have an agent grep. It is a real namespace boundary, not a prompt
instruction. See [ISOLATION](ISOLATION.md).

## Holds: pausing the fleet without breaking it

```bash
fleet-hold on --for 6h "deploying; agents stay idle"
fleet-hold status
fleet-hold off
```

A hold is a **work-tempo** signal. Under a hold:
- post-swap nudges tell agents to *stay idle* instead of "keep going";
- agents whose tempo rules would auto-resume are supposed to check it and stand down.

A hold deliberately does **not** stop account rotation, failover, or swaps — infrastructure keeps
protecting you while the *work* pauses. (This distinction was learned the hard way: a hold that
silently disarmed the rotation watchdog caused two outages. Pausing work and pausing infrastructure
are different things and get different switches.)

To quiet the **watchdog** for maintenance, use the other switch — which cannot forget to turn itself
back on:

```bash
watch-freeze 45m "swapping disks"    # stops the timer AND books a guaranteed auto-thaw first
watch-freeze thaw                    # end early
```

## Checkpoint discipline

An agent can be bounced at any moment — a reboot, an account swap, a crash. What survives is **what
it wrote to disk**. Teach your agents (in their `AGENTS.md`) to:

- keep a `STATE.md` in their workspace: current objective, what's done, **what to do next**;
- write it *before* long operations, not after;
- on resume, read it first and continue from the resume pointer.

The fleet does its part: the session is resumed by id (never a fresh chat), queued messages are
flushed, and — if enabled — the brain writes each workspace's own resume pointer into its memory.
But an agent that keeps its plan only in its head will lose it. This is the single most common
source of "my agent forgot what it was doing."

## Working from your phone

Set `REMOTE_CONTROL="on"` and `RC_NAME="Nice Name"` in a descriptor and the workspace appears in the
Claude mobile app; you can read and steer it from anywhere. Two rules:

1. **One RC session per workspace.** The fleet enforces stop-before-start on every path that could
   double it.
2. **Account moves orphan bridges.** Bridges register server-side per account+device and there is no
   CLI to deregister them; when a workspace changes account it re-registers under the new one and the
   old entry becomes a stale ghost. The fleet messages `main` a cleanup list after any swap that
   moved RC workspaces — delete those entries in the app, or they read as broken agents.

## Maintenance

| Want to | Do |
|---|---|
| See everything | `agentctl status` |
| Stop an agent for now (keep it listed) | `agentctl stop <n>` |
| Retire an agent (archive its descriptor) | `agentctl down <n>` — bring back with `agentctl up <n>`, same conversation |
| Free up idle workspaces automatically | enable `idle-down.timer` |
| Protect transcripts | enable `session-guard.timer` (recommended for everyone) |
| Watch what the fleet is doing | `journalctl --user -u account-watch -f`, `~/.agents/accounts/watch.log` |

## Anti-patterns

- **Talking to 12 agents directly.** Use `main`. You are the bottleneck otherwise.
- **Doing sensitive work in an unconfined workspace.** The agent can read every other agent's
  transcripts. It *will*, if that's the cheapest way to answer you.
- **Letting an agent hold its plan only in context.** One bounce and it's gone. `STATE.md` or it
  didn't happen.
- **Using a hold to stop the watchdog.** Different switch (`watch-freeze`) — and it books its own
  thaw so you can't strand yourself.
- **Destroying a long-lived workspace to "start clean."** You are deleting the most expensive thing
  you own. Compact or branch the work instead.
