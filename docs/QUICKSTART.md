# Quickstart — zero to a working fleet

~15 minutes. Assumes Linux, `tmux`, `python3`, and at least one agent CLI (`claude`, `codex`, or
`opencode`) already installed and logged in.

## 1. Install

```bash
git clone https://github.com/fl-sean03/agent-fleet.git ~/agent-fleet
cd ~/agent-fleet
./install.sh
```

This creates `~/.agents/` (descriptors, tools, message store), writes `~/.agents/fleet.conf`, links
the tools onto your `PATH`, and installs the systemd units **without enabling them**. It never logs
you in, creates workspaces, or starts anything.

Make sure `~/.local/bin` is on your `PATH` (the installer warns if it isn't).

## 2. Your first agent

```bash
agentctl new demo --root ~/work/demo --up
agentctl status
```

You now have a tmux session `agent-demo` with a live agent in it, a descriptor at
`~/.agents/projects/demo.env`, and a **fixed session id** — this conversation will resume forever.

Talk to it without attaching:

```bash
agentctl send demo "look around this directory and tell me what you'd build first"
agentctl read demo 40
```

Or attach and drive it directly (`Ctrl-b d` to detach and leave it running):

```bash
agentctl attach demo
```

Prove persistence — stop it, start it, and note it still remembers:

```bash
agentctl stop demo && agentctl up demo
agentctl send demo "what did I just ask you?"
```

## 3. The coordinator

The habit that makes a fleet manageable: one agent that talks to the others for you.

```bash
agentctl new main --root ~ --rc "Main" --up
agentctl send main "introduce yourself; you are the fleet coordinator"
```

Background services alert `main` by default, and agents that need a decision relay through it.
See [OPERATING.md](OPERATING.md#the-coordinator-pattern-main).

## 4. A confined agent (when the work must not see your other work)

Requires `bubblewrap` (`apt install bubblewrap`).

```bash
agentctl new acme --confined "Acme Corp" --up
agentctl login acme          # follow the URL; finish with: agentctl login acme --code <CODE>
```

This agent runs in a namespace where **your home directory is not mounted**. It cannot read your
other agents' transcripts even if it tries — and an unconfined agent absolutely will try, because
grepping a sibling transcript is often the cheapest way to answer a question. See
[ISOLATION.md](ISOLATION.md).

## 5. Turn on the background services (optional, recommended in this order)

```bash
systemctl --user enable --now session-guard.timer   # protects transcripts from loss — turn this on
systemctl --user enable --now idle-down.timer       # spins down workspaces you haven't touched
systemctl --user enable --now brain-nightly.timer   # the second brain (see docs/BRAIN.md)
systemctl --user enable --now account-watch.timer   # ONLY if you run multiple accounts — read docs/ACCOUNTS.md first
```

`account-watch` does nothing useful (and should stay off) unless you've configured a rotation
allow-list. It is the one opt-in with real caveats.

## 6. Phone control (optional)

Set `REMOTE_CONTROL="on"` and `RC_NAME="Demo"` in `~/.agents/projects/demo.env`, then
`agentctl stop demo && agentctl up demo`. The workspace appears in the Claude mobile app.

## 7. Verify

```bash
cd ~/agent-fleet && ./run-tests.sh
```

249 tests, all against sandboxed fixtures — no credentials, no network.

## Where things live

| Path | What |
|---|---|
| `~/.agents/projects/*.env` | Workspace descriptors — the source of truth |
| `~/.agents/bin/` | The tools (symlinks into your checkout) |
| `~/.agents/fleet.conf` | Config: kit root, confined root, brain model |
| `~/.agents/messages/` | Durable inter-agent message log + SQLite |
| `~/.agents/accounts/` | Account profiles (only if you use rotation) |
| `~/confined/` | Confined workspace roots |
| `<kit>/memory/` | Per-workspace knowledge stores (written by the brain) |

## Next

**[OPERATING.md](OPERATING.md)** — the day-to-day: coordinator, holds, checkpoint discipline, phone
control, anti-patterns. That's the one that makes this stick.
