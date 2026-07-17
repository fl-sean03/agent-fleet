#!/usr/bin/env bash
# install.sh — bootstrap the fleet on this machine. Idempotent; safe to re-run after `git pull`.
#
#   ./install.sh                 install (tools + config + dirs; timers NOT enabled)
#   ./install.sh --with-timers   also enable the background services (watchdog, guard, brain, idle)
#   ./install.sh --uninstall     remove symlinks + units (never touches your workspaces or memory)
#
# What it does NOT do: log you in, create workspaces, or start agents — see docs/QUICKSTART.md.
set -uo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
A="${FLEET_AGENTS_DIR:-$HOME/.agents}"
CONFINED_ROOT="${FLEET_CONFINED_ROOT:-$HOME/confined}"
UNITS="$HOME/.config/systemd/user"
LOCAL_BIN="$HOME/.local/bin"
WITH_TIMERS=0; UNINSTALL=0
for a in "$@"; do case "$a" in
  --with-timers) WITH_TIMERS=1 ;;
  --uninstall) UNINSTALL=1 ;;
  -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
  *) echo "unknown flag: $a" >&2; exit 2 ;;
esac; done

say(){ printf '  %s\n' "$*"; }

if [ "$UNINSTALL" = 1 ]; then
  echo "uninstalling (workspaces, memory, and accounts are LEFT ALONE)…"
  for u in account-watch session-guard brain-nightly idle-down input-watchdog backup-watch; do
    systemctl --user disable --now "$u.timer" 2>/dev/null
    rm -f "$UNITS/$u.timer" "$UNITS/$u.service"
  done
  rm -f "$UNITS/brain-alert.service"
  systemctl --user daemon-reload 2>/dev/null
  find "$A/bin" -maxdepth 1 -type l -exec rm -f {} + 2>/dev/null
  rm -f "$LOCAL_BIN/agentctl" "$LOCAL_BIN/brain"
  say "done. Your ~/.agents data and workspaces were not touched."
  exit 0
fi

echo "installing the fleet from: $KIT"

# --- 1. dependency check -------------------------------------------------------------------
missing=()
for c in tmux git python3; do command -v "$c" >/dev/null 2>&1 || missing+=("$c"); done
[ "${#missing[@]}" -gt 0 ] && { echo "MISSING required: ${missing[*]}" >&2; exit 1; }
command -v bwrap >/dev/null 2>&1 || say "NOTE: bubblewrap (bwrap) not found — CONFINED workspaces will be unavailable (projects still work). Install: apt install bubblewrap"
command -v claude >/dev/null 2>&1 || say "NOTE: no 'claude' CLI on PATH — install your agent CLI(s); the fleet also supports codex/opencode via AGENTS=."
command -v sqlite3 >/dev/null 2>&1 || say "NOTE: sqlite3 CLI absent (python's sqlite3 module is what the brain actually uses — fine)."

# --- 2. directories ------------------------------------------------------------------------
mkdir -p "$A"/{projects,bin,accounts,confined-cfg,messages,session-archive,attic/retired-descriptors,tokens}
mkdir -p "$CONFINED_ROOT" "$LOCAL_BIN" "$UNITS"
# canonical config surface: profiles + subagents (user-editable copies; not overwritten on reinstall)
[ -d "$A/profiles" ] || cp -a "$KIT/profiles" "$A/profiles"
[ -d "$A/subagents" ] || cp -a "$KIT/subagents" "$A/subagents"
# scaffold used by `agentctl new <n> --confined` (yours to customize; not overwritten on re-install)
[ -d "$CONFINED_ROOT/_template" ] || cp -a "$KIT/templates/confined-template" "$CONFINED_ROOT/_template"
chmod 700 "$A/accounts" "$A/tokens" 2>/dev/null
say "dirs ready: $A, $CONFINED_ROOT"

# --- 3. config ------------------------------------------------------------------------------
# Every path the tools use resolves: env > this file > default. This is what makes the kit portable.
CONF="$A/fleet.conf"
if [ ! -f "$CONF" ]; then
  cat > "$CONF" <<EOF
# fleet.conf — written by install.sh. env vars override these.
FLEET_KIT_ROOT=$KIT
FLEET_CONFINED_ROOT=$CONFINED_ROOT
# BRAIN_MODEL=claude-opus-4-8      # the one model choke point for the brain's own calls
EOF
  say "wrote $CONF"
else
  sed -i "s|^FLEET_KIT_ROOT=.*|FLEET_KIT_ROOT=$KIT|" "$CONF"
  say "updated FLEET_KIT_ROOT in $CONF (kept your other settings)"
fi

# --- 4. tools on PATH ------------------------------------------------------------------------
for f in "$KIT"/bin/*; do ln -sfn "$f" "$A/bin/$(basename "$f")"; done
ln -sfn "$A/bin/agentctl" "$LOCAL_BIN/agentctl"
ln -sfn "$KIT/brain/bin/brain" "$LOCAL_BIN/brain" 2>/dev/null
say "linked $(ls "$KIT"/bin | wc -l) tools into $A/bin; agentctl + brain on \$PATH"
case ":$PATH:" in *":$LOCAL_BIN:"*) ;; *) say "WARNING: $LOCAL_BIN is not on your PATH — add it to your shell rc";; esac

# --- 5. skills (optional, if you use Claude Code) ---------------------------------------------
# ~/.claude is a PROJECTION of the canonical ~/.agents surface (skills, base instructions, subagents),
# so ambient (non-fleet) Claude Code use sees the same source of truth. Per-workspace config is composed
# separately at launch (see docs/PROFILES.md); this is only the base mirror.
ln -sfn "$KIT/skills" "$A/skills"   # canonical skills live under ~/.agents (backed by the checkout)
mkdir -p "$HOME/.claude/skills"
for s in "$A"/skills/*/; do [ -d "$s" ] && ln -sfn "$s" "$HOME/.claude/skills/$(basename "$s")"; done
ln -sfn "$A/profiles/base/AGENTS.md" "$HOME/.claude/CLAUDE.md"
[ -d "$A/subagents" ] && ln -sfn "$A/subagents" "$HOME/.claude/agents"
say "projected canonical surface into ~/.claude (skills, CLAUDE.md→base AGENTS.md, agents)"

# --- 6. systemd units (installed; enabled only with --with-timers) -----------------------------
for u in "$KIT"/systemd/*; do
  [ -f "$u" ] || continue    # skip subdirs (environment.d/ has its own installer)
  sed "s|{{FLEET_KIT_ROOT}}|$KIT|g" "$u" > "$UNITS/$(basename "$u")"
done
# environment.d: PATH for ALL --user units, including the transient ones Claude Code spawns for the
# Remote-Control bridge / Stop hooks (without it those run under systemd's bare default PATH and
# fail with "node: not found"). Idempotent; takes effect for units started after the next login or
# `systemctl --user daemon-reexec`.
[ -x "$KIT/systemd/environment.d/install.sh" ] && "$KIT/systemd/environment.d/install.sh" >/dev/null 2>&1 \
  && say "installed environment.d PATH drop-in (transient --user units get ~/.local/bin)"
systemctl --user daemon-reload 2>/dev/null
say "installed $(ls "$KIT"/systemd | wc -l) systemd units"
if [ "$WITH_TIMERS" = 1 ]; then
  for t in account-watch session-guard idle-down brain-nightly input-watchdog; do
    systemctl --user enable --now "$t.timer" 2>/dev/null && say "enabled $t.timer"
  done
  say "background services running. 'systemctl --user list-timers' to see them."
else
  say "timers NOT enabled (safe default). Enable when ready:"
  say "    systemctl --user enable --now session-guard.timer   # transcript protection (recommended)"
  say "    systemctl --user enable --now input-watchdog.timer  # auto-submit stranded composer input"
  say "    systemctl --user enable --now account-watch.timer   # multi-account rotation (opt-in, see docs/ACCOUNTS.md)"
  say "    systemctl --user enable --now brain-nightly.timer   # the second brain"
  say "    systemctl --user enable --now idle-down.timer       # spin down idle workspaces"
  say "    systemctl --user enable --now backup-watch.timer    # backup staleness alerts (needs ~/.agents/backup-watch.conf)"
fi

cat <<EOF

installed.

  next:  agentctl new demo --root ~/work/demo --up     # create + start your first workspace
         agentctl attach demo                           # watch it
         agentctl send demo "what are you working on?"  # talk to it from anywhere

  read:  docs/QUICKSTART.md   zero → a working fleet
         docs/OPERATING.md    how to actually run this day to day
EOF
