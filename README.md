# agent-fleet

Run a **fleet of persistent AI coding agents** on one machine — each in its own workspace, each
remembering its work forever, all steerable from your phone.

This is the extracted, genericized version of a system that has run a real 15-agent fleet in
production for months: agents that survive reboots, message each other, get isolated from each
other when they must, rotate across accounts when one hits a rate limit, and mine their own
transcripts nightly into durable memory.

```bash
git clone https://github.com/fl-sean03/agent-fleet.git ~/agent-fleet && cd ~/agent-fleet
./install.sh
agentctl new demo --root ~/work/demo --up
agentctl send demo "read the repo and tell me what it does"
```

---

## What you actually get

**Persistent workspaces.** A workspace is a tmux session + a ~10-line descriptor. It has ONE
conversation, pinned to a fixed session id, that resumes forever — across restarts, reboots, and
account switches. Close your laptop, come back tomorrow, the agent still knows everything.

**Three isolation tiers.** *Project* (full host access) · **Confined** (a bubblewrap namespace where
the host filesystem is **not mounted** — genuinely unreadable, not merely denied; its own
credentials, its own session store) · *Remote* (the agent runs on another box over ssh).
Confinement is not a nicety: an unsandboxed agent asked for a fact it was never told **will grep
your other agents' transcripts to find it.**

**Workspace profiles.** `~/.agents/` is the single source of truth; `~/.claude/` (and `~/.codex/`, `~/.grok/`) are projections of it. Categorize a workspace by **profile** — `agentctl new acme --profile client` (confined, restricted toolkit, client instructions) vs `--profile lab` (unconfined, full toolkit) — and its config is composed from `base < profile < descriptor`, while still sharing one session store and rotating across accounts (credentials stay in the account — the config dir never holds them). See [docs/PROFILES.md](docs/PROFILES.md).

**Agent-agnostic.** The descriptor's `AGENTS=` field picks the launcher — `claude`, `codex`,
`opencode`, or your own `bin/run-<name>`. Everything else (workspaces, messaging, holds, idle-down,
session protection) works the same regardless of which CLI is inside.

**Inter-agent messaging.** `agentctl send <who> "..."` delivers text **into another agent's live
conversation** as a real turn — not an inbox it has to remember to poll. Messages to a stopped agent
queue and flush on resume. Everything is logged to a durable SQLite store with conversation links.

**Steer from your phone.** Remote Control bridges every workspace into the Claude mobile app. One
session per workspace, enforced.

**A second brain (optional).** A nightly pipeline reads your agents' own transcripts and distills
them into per-workspace memory: durable facts, operating rules, and lessons from their own failure
arcs. Every claim is gated by a **verbatim-quote check** against the source (fabrications cannot be
promoted), proposals are written by one model call and approved by an independent second one, and
every write is archived with before/after so it can be reversed.

**Multi-account rotation (optional).** If you run several accounts, a watchdog polls usage and
migrates the whole fleet before you hit a wall — ordered, verified, and aware of per-model caps.
Opt-in; see [docs/ACCOUNTS.md](docs/ACCOUNTS.md).

**446 tests** (392 shell + 54 Python). Every subsystem is tested against sandboxed fixtures — no
live credentials, no network. `./run-tests.sh` (the Python suite needs `pip install pytest`).

---

## Docs

| Read this | For |
|---|---|
| [QUICKSTART](docs/QUICKSTART.md) | Zero → a working fleet you're talking to, in ~15 minutes |
| **[OPERATING](docs/OPERATING.md)** | **How to actually live with this**: the coordinator pattern, holds, checkpoints, day-to-day UX |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | How the pieces fit; what each tool owns |
| [ISOLATION](docs/ISOLATION.md) | The three tiers, the confinement wall, and the leak it prevents |
| [PROFILES](docs/PROFILES.md) | One source of truth (`~/.agents`); per-workspace **profiles** (client/lab/…) that compose different config |
| [MESSAGING](docs/MESSAGING.md) | Direct-into-conversation delivery, queueing, the durable log |
| [ACCOUNTS](docs/ACCOUNTS.md) | Multi-account rotation, failover, per-model caps |
| [BRAIN](docs/BRAIN.md) | The second brain: pipelines, the anti-fabrication gate, what it writes |

## Requirements

`tmux` · `git` · `python3` — plus at least one agent CLI (`claude`, `codex`, `opencode`).
`bubblewrap` for confined workspaces. Linux with systemd for the background services (the core
works without them). Everything else is bash + the Python standard library.

## Status & honesty

- The workspace/messaging/isolation/hold layers are **genuinely agent-agnostic**.
- The **account-rotation** layer and the **brain's model call** are Claude-shaped today — they sit
  behind narrow interfaces (one launcher per agent; one `agentcall` choke point), so porting them is
  a contained job, but they are not yet ported. Called out rather than papered over.
- This was extracted from a working private fleet. Names, hosts, and workspaces are genericized; the
  scars in the comments (dated incidents, "this is why the guard exists") are real and kept on
  purpose — they are the most valuable documentation in the repo.

## License

MIT — see [LICENSE](LICENSE).
