# Multi-account rotation

Optional module — **off by default**.

If you run several accounts (e.g. separate plans for separate purposes), a watchdog can migrate the
whole fleet off an account that's about to hit a usage wall — before your agents die mid-task.
If you run one account, skip this entirely: leave `account-watch.timer` disabled and everything else
in the kit works.

## Setup

```bash
# one isolated config dir per account
mkdir -p ~/.agents/accounts/{account-a,account-b}
CLAUDE_CONFIG_DIR=~/.agents/accounts/account-a claude   # /login once, then quit
CLAUDE_CONFIG_DIR=~/.agents/accounts/account-b claude

printf 'account-a\naccount-b\n' > ~/.agents/accounts/.rotation   # the allow-list
printf 'account-a\n'            > ~/.agents/accounts/.active     # who's live now

agentctl accounts            # who is on what, from the live process — not a guess
systemctl --user enable --now account-watch.timer
```

Every account is a **named profile** with its own config directory. Labels not in `.rotation` are
rejected before an agent starts — there is no ambient "default account" to drift onto.

## What the watchdog does

Every 5 minutes it reads each account's usage (5-hour window, 7-day window, and **per-model** caps)
and decides:

- **Trigger**: active account ≥90% on the 5h window or ≥95% on the 7d window — confirmed over **two
  consecutive ticks** (never act on one sample), or a hard cap detected by probe.
- **Eligible target**: has credentials, is under both ceilings, and is **meaningfully better on the
  axis that triggered** — a 7d-triggered rotation compares 7d headroom, a 5h-triggered one compares
  5h. (Comparing the wrong axis makes the trigger silently unsatisfiable; that bug stranded a fleet.)
- **Choose**: the account whose window resets soonest — burn the one that comes back first.
- **Confirm**: probe the target with a real call *before* moving. The usage endpoint saying "fine"
  is not proof.
- **If nothing is eligible**: hold in place, alert `main`, and back off polling until the earliest
  known reset. It never bounces the fleet onto another capped account.

Then `swap-fleet` executes: **ordered** (other workspaces → coordinator last → confined workspaces
with their own credentials), **verified** (each agent's live process is confirmed running under the
target config by reading `/proc/<pid>/environ`), and **resumable** (the tail runs detached, so it
completes even though swapping the coordinator tears down the shell that launched it).

## Things it knows that cost us to learn

- **Per-model caps are invisible to the usual numbers.** An account can read healthy on 5h/7d and
  still refuse one specific model. If a workspace pins that model, moving it onto such an account
  strands it. The fleet reads per-model caps from the usage endpoint *and* records event-driven
  markers when a real call hits the wall, and **refuses to move a model-pinned workspace onto an
  account that can't serve its model** — leaving it running where it is, and telling you.
- **Idle accounts go blind.** An account nobody uses has its access token expire (~8h) with nothing
  renewing it; usage reads 401 and the account silently drops out of the failover set. Each tick now
  refreshes any 401'd account (exercising its long-lived refresh token) *before* deciding
  eligibility. Never re-mint a setup-token for this — that authenticates in a degraded mode
  (weaker model, no Remote Control).
- **Rotation and holds are different switches.** A `fleet-hold` pauses *work*; it must never disarm
  the watchdog. A freeze marker that silently disarmed rotation caused two outages, so there is no
  freeze file — maintenance stops the timer *visibly* via `watch-freeze`, which books its own thaw.

## Files

| Path | What |
|---|---|
| `accounts/.rotation` | The allow-list (order = chain) |
| `accounts/.active` | Fleet-active account |
| `accounts/.fleet-model` | The model the fleet runs (gates per-model cap logic) |
| `accounts/watch.log` | Every tick, trigger, and decision |
| `accounts/swap-fleet.log` | Every phase of every swap |
| `accounts/.watch-backoff` | Deadline-aware backoff while holding |

## Before you enable this

1. **It's Claude-shaped.** `bin/account-usage` speaks the Claude OAuth usage endpoint. Everything
   above it (deciding, swapping, verifying) is generic — porting means reimplementing one script.
2. **It moves your agents.** A swap bounces every workspace (they resume their same conversation, but
   they *are* interrupted). If that's not acceptable, leave it off and swap by hand:
   `swap-fleet <account>`.
3. **Remote-Control bridges orphan on every account move** — see [OPERATING.md](OPERATING.md).

It is off by default. The rest of the kit does not depend on it.
