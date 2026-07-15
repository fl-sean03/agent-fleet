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
  .claude.json       = seeded from the account (re-seeded when the account changes)
  projects/          → the shared session store
```

Note what is **not** there: `.credentials.json`.

```
CLAUDE_CONFIG_DIR              = ~/.agents/cfg/<ws>          # per-workspace, composed
CLAUDE_SECURESTORAGE_CONFIG_DIR = ~/.agents/accounts/<label>  # per-account, shared credential store
```

Three consequences worth understanding:

1. **Different workspaces get genuinely different config** — a `client` and a `lab` workspace read
   different skills, instructions, and settings, even though they share one machine.
2. **You still get account rotation, and credentials never fork.** The CLI resolves its credential
   store from `CLAUDE_SECURESTORAGE_CONFIG_DIR` independently of `CLAUDE_CONFIG_DIR`, so every
   workspace on an account reads and refreshes the **one** credential file — exactly as it did before
   profiles existed. A swap changes that env var; the config dir path never moves, which also keeps a
   workspace's Remote-Control identity stable.
3. **Do not "simplify" this by symlinking credentials into the composed dir.** It looks equivalent and
   is not. Claude Code writes credentials through an atomic helper — write `"$path.tmp.<rand>"`, then
   **rename** onto `$path` — and a rename *replaces a symlink with a regular file*. The first OAuth
   refresh would silently convert the link into a private copy, leaving the account holding a **stale**
   refresh token; refresh tokens rotate, so the account's copy then dies and every other workspace on
   it goes down with it. This is not hypothetical — it is the shape of a real dud-credential outage.

Because the composed `settings.json` **replaces** the account dir's settings, `profiles/base/settings.json`
carries the unattended-operation contract (`skipDangerousModePermissionPrompt`, the permissions default,
the transcript-retention floor). Drop those and every agent boots into a modal it cannot answer.
`model` deliberately stays out of settings — the descriptor's `MODEL=` pin owns that.

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

## Confined workspaces

A confined workspace composes **into its own isolated config** (`~/.agents/confined-cfg/<ws>`) — the
dir its bwrap namespace already mounts at `/config`. Composition touches instructions, settings and
skills and **nothing else**: its credentials stay its own isolated stash (never an account's) and its
session store stays private. Profiles do not cross the confinement wall.

Two consequences specific to confinement:

- **Skills are copied, not symlinked.** The host filesystem is not mounted, so a link into
  `~/.agents/skills` would dangle inside the sandbox. That also means a granted skill's *text* is
  disclosed to whoever that workspace belongs to — keep the profile's `SKILLS` narrow and free of
  host/fleet detail. (A fleet-operations skill is useless in there anyway: no `agentctl` to run.)
- **Pre-existing settings are preserved.** A confined config dir predates profiles and may hold
  hand-tuned settings. The first compose snapshots them to `.settings.pre-profile.json` and treats
  that as the **lowest** layer, so profiles override what they declare and nothing else is lost. (A
  project's `cfg/<ws>` is fully managed by compose and is never seeded this way — otherwise a key
  removed from a profile would be resurrected from the previous run forever.)

## The boundary (what is *not* canonicalized)

Only the **shareable config surface** is projected: instructions, skills, subagents, settings. The
following stay per-harness because they are runtime or harness-specific, not portable config:

- the **session store** (`~/.claude/projects/` — transcripts) — shared, symlinked, never moved;
- **credentials** — per-account, via `CLAUDE_SECURESTORAGE_CONFIG_DIR` (see above);
- **caches, daemon, history, tasks** — Claude Code runtime.

A Claude `settings.json` is also not a Codex or Grok config — settings are composed *per harness*.
The canonical thing is the *intent* (a profile), projected into each harness's format.
