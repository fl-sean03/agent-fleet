# Config model: one source of truth, per-workspace profiles

The design principle: **`~/.agents/` is the single source of truth, and every harness's config dir
(`~/.claude/`, `~/.codex/`, `~/.grok/`) is a projection of it.** `.agents` is agent-agnostic, so it
comes first; `.claude` and friends just mirror the shareable surface. A workspace is then
*categorized by a profile*, and its effective config is composed from layers.

## The layers (most-specific wins)

```
base   <   <profile>   <   workspace descriptor
```

- **base** (`~/.agents/profiles/base/`) — applies to every workspace.
- **profile** (`~/.agents/profiles/<name>/`) — a named *type* of workspace: `client`, `lab`, … .
- **descriptor** (`~/.agents/projects/<ws>.env`, plus optional `<ws>.d/`) — the specific workspace.

Each layer can set **instructions** (`AGENTS.md`), **settings** (`settings.json`), a **skill set**
(`SKILLS=`), and **policy** (`KIND`, `SANDBOX`, `MODEL`, `REMOTE_CONTROL`). A later layer overrides
an earlier one; for `SKILLS`, the most-specific declaration wins outright (a narrow profile *narrows*
— it does not inherit base's `all`).

## Why profiles

You have many client engagements that should all behave one way — confined, a restricted toolkit,
confidentiality instructions — and your own lab work that should behave another — unconfined, full
toolkit, your choice of model. You don't want to configure each workspace by hand.

```bash
agentctl new acme  --profile client          # confined + client instructions + narrow skills
agentctl new rig   --profile lab --root ~/work/rig   # unconfined + full toolkit
```

The profile's policy is adopted at creation (a `client` profile whose `KIND=confined` provisions a
confined workspace automatically), and its instructions/skills/settings are **composed at launch**.
Change a profile once and every workspace of that type picks it up on next start.

## What "composed" means

At launch, `agent-profile compose <ws>` builds a **per-workspace config directory**
(`~/.agents/cfg/<ws>/`) and points the workspace's `CLAUDE_CONFIG_DIR` at it:

```
~/.agents/cfg/acme/
  AGENTS.md          = base + profile + descriptor instructions (concatenated)
  CLAUDE.md          → AGENTS.md            (harness-compat mirror)
  settings.json      = deep-merge(base, profile, descriptor)
  skills/<x>         → ~/.agents/skills/<x> (only the resolved set)
  agents/            → ~/.agents/subagents  (canonical, shared)
  .credentials.json  → ~/.agents/accounts/<active>/.credentials.json
  .claude.json       → ~/.agents/accounts/<active>/.claude.json
  projects/          → the shared session store
```

Two consequences worth understanding:

1. **Different workspaces get genuinely different config** — a `client` and a `lab` workspace read
   different skills, instructions, and settings, even though they share one machine.
2. **You still get account rotation.** Credentials are *symlinked* from the active account, not baked
   in. A swap **retargets that symlink** and leaves the config dir path stable — so `CLAUDE_CONFIG_DIR`
   never changes for a workspace, which also keeps its Remote-Control identity stable. (This is
   strictly better than coupling config to the account dir, where per-workspace settings are
   impossible.)

## Making your own profile

```bash
cp -r ~/.agents/profiles/lab ~/.agents/profiles/research
$EDITOR ~/.agents/profiles/research/profile.conf   # KIND / SANDBOX / MODEL / SKILLS
$EDITOR ~/.agents/profiles/research/AGENTS.md       # the instructions overlay
$EDITOR ~/.agents/profiles/research/settings.json   # settings overlay (deep-merged)
agentctl new thing --profile research --root ~/work/thing
```

`profile.conf` keys:

| key | meaning |
|---|---|
| `KIND` | `project` or `confined` (confined ⇒ bwrap namespace + own scaffold) |
| `SANDBOX` | empty, or `bwrap` |
| `MODEL` | the descriptor model pin for new workspaces of this type |
| `REMOTE_CONTROL` | `on` / off |
| `SKILLS` | `all`, or a space-separated subset of skill names |

`agent-profile list`, `agent-profile show <profile>`, and `agent-profile policy <ws> <KEY>` inspect
the resolution.

## The boundary (what is *not* canonicalized)

Only the **shareable config surface** is projected: instructions, skills, subagents, settings. The
following stay per-harness because they are runtime or harness-specific, not portable config:

- the **session store** (`~/.claude/projects/` — transcripts) — shared, symlinked, never moved;
- **credentials** (`.credentials.json` / `.claude.json`) — per-account;
- **caches, daemon, history, tasks** — Claude Code runtime.

A Claude `settings.json` is also not a Codex or Grok config — settings are composed *per harness*.
The canonical thing is the *intent* (a profile), projected into each harness's format.
