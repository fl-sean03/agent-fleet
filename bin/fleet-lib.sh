# fleet-lib.sh — the ONE implementation of the fleet harness's shared primitives.
#
# WHY (consolidation 2026-07-17): descriptor parsing, pane addressing, busy detection, process-tree
# walks, RC re-registration, the fable-cap marker read, the log-line format and the system-message
# envelope each existed as 3–6 slightly different copies across bin/. The differences were never
# design — they were drift, and drift bites: one ad-hoc grep handled quotes differently and a rename
# sweep missed it; verify_rc_retry had to be re-implemented inline because sourcing swap-fleet for it
# trips the swap lock. One copy, sourced everywhere.
#
# USAGE — source it via your own RESOLVED path (works from every deploy point: ~/.agents/bin entries
# are symlinks into the kit checkout, so readlink -f lands next to this file):
#
#     FL_SELF="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
#     . "$FL_SELF/fleet-lib.sh"
#
# RULES:
#   * No side effects at source time (safe under every *_TEST=1 sourcing seam). No `set` changes.
#   * Everything reads $A / $ACCTS at CALL time, so test sandboxes (export A=... before or after
#     sourcing) always win. Defaults below only fill in what the caller didn't set.
#   * Functions named ws_* / verify_* / fable_blocked keep their historical names — tests and
#     swap-fleet source them by those names. New primitives are fl_*.
#   * Scripts set FL_SCRIPT=<name> (their envelope identity) and LOG (their log file) before use.

[ -n "${FLEET_LIB_LOADED:-}" ] && return 0
FLEET_LIB_LOADED=1

A="${A:-$HOME/.agents}"
ACCTS="${ACCTS:-$A/accounts}"

# ---------------------------------------------------------------------------------------------
# logging — one line format everywhere: "[YYYY-mm-ddTHH:MM:SSZ] msg" appended to $LOG.
# FL_LOG_TEE=1 additionally echoes to stdout (swap-fleet's historical behavior).
# ---------------------------------------------------------------------------------------------
fl_log(){
  local line; line="[$(date -u +%FT%TZ)] $*"
  if [ "${FL_LOG_TEE:-0}" = 1 ]; then printf '%s\n' "$line" | tee -a "${LOG:?fl_log: LOG unset}"
  else printf '%s\n' "$line" >> "${LOG:?fl_log: LOG unset}"; fi
}

# ---------------------------------------------------------------------------------------------
# descriptor access — ~/.agents/projects/<ws>.env is the single source of workspace truth.
# ws_get is THE parser: anchored (a commented '#RESUME-retired…' line can never match — the exact
# bug that fed fleet-msg/idle-down a garbage session id for a confined workspace), quote-aware
# (values may contain spaces: RC_NAME="Team Workspace"), and inline-comment-safe for unquoted values.
# ---------------------------------------------------------------------------------------------
fl_desc(){ printf '%s\n' "$A/projects/$1.env"; }

ws_get(){ # <ws> <KEY> → value on stdout; rc 1 when the key line is absent
  local f="$A/projects/$1.env" line v
  [ -f "$f" ] || return 1
  line=$(grep -m1 -E "^$2=" "$f" 2>/dev/null) || return 1
  v="${line#*=}"
  case "$v" in
    \"*) v="${v#\"}"; v="${v%%\"*}" ;;                      # quoted: to the closing quote
    *)   v="${v%%#*}"; v="${v%"${v##*[![:space:]]}"}" ;;    # unquoted: strip comment + trailing ws
  esac
  printf '%s\n' "$v"
}

ws_root(){ # <ws> → ROOT with $HOME / ~ expanded
  local r; r=$(ws_get "$1" ROOT) || return 1
  r="${r/#\$HOME/$HOME}"; r="${r/#\~/$HOME}"
  printf '%s\n' "$r"
}

ws_session_id(){ ws_get "$1" SESSION_ID || ws_get "$1" RESUME; }   # RESUME= = legacy confined pin
ws_model(){ ws_get "$1" MODEL; }
ws_agents(){ local v; v=$(ws_get "$1" AGENTS) || true; printf '%s\n' "${v:-claude}"; }
ws_rc_on(){ [ "$(ws_get "$1" REMOTE_CONTROL 2>/dev/null)" = on ]; }

ws_kind(){ # <ws> → explicit KIND=, else confined iff ROOT is under $CONFINED_ROOT (agentctl's derivation)
  local k r; k=$(ws_get "$1" KIND 2>/dev/null) || true
  if [ -z "$k" ]; then
    r=$(ws_root "$1" 2>/dev/null) || true
    case "$r" in "${CONFINED_ROOT:-${FLEET_CONFINED_ROOT:-$HOME/confined}}"/*) k=confined ;; *) k=project ;; esac
  fi
  printf '%s\n' "$k"
}

fl_encode_cwd(){ printf '%s' "$1" | sed 's#[^a-zA-Z0-9]#-#g'; }   # the CLI's session-dir encoding

ws_transcript(){ # <ws> → path of the pinned conversation jsonl (confined isolated store vs shared host store)
  local ws="$1" sid; sid=$(ws_session_id "$ws") || return 1
  if [ "$(ws_kind "$ws")" = confined ]; then
    printf '%s\n' "$A/confined-cfg/$ws/projects/-work/$sid.jsonl"  # cwd in the sandbox is /work
  else
    local root; root=$(ws_root "$ws") || return 1
    printf '%s\n' "$HOME/.claude/projects/$(fl_encode_cwd "$root")/$sid.jsonl"
  fi
}

# ---------------------------------------------------------------------------------------------
# dormancy — operator policy 2026-07-17: automated wake/nudge messages are only for agents that
# were actually IN USE recently ("we should not be sending swap messages if the agent has not been
# in use/process for the last 6 hours"). Signal = the pinned transcript's mtime. Operator-initiated
# and explicit fleet-msg sends are never gated — this is for AUTOMATED wake-ups only.
# ---------------------------------------------------------------------------------------------
ws_active_within(){ # <ws> [hours=FL_WAKE_ACTIVE_HOURS(6)] → rc 0 if the transcript was written within N hours
  local ws="$1" hours="${2:-${FL_WAKE_ACTIVE_HOURS:-6}}" f m
  f=$(ws_transcript "$ws" 2>/dev/null) || return 1
  [ -f "$f" ] || return 1                                   # no transcript = no evidence of use
  m=$(stat -c %Y "$f" 2>/dev/null) || return 1
  [ $(( $(date +%s) - m )) -lt $(( hours * 3600 )) ]
}

fl_idle_hours(){ # <ws> → idle hours (1 decimal) for log lines; '?' when unknowable
  local f m
  f=$(ws_transcript "$1" 2>/dev/null) && [ -f "$f" ] && m=$(stat -c %Y "$f" 2>/dev/null) \
    && awk -v s="$(( $(date +%s) - m ))" 'BEGIN{printf "%.1f", s/3600}' || printf '?'
}

# ---------------------------------------------------------------------------------------------
# tmux / pane — pane model: each agent-<ws> session has pane 1 = control shell, pane 2 = the agent.
# ---------------------------------------------------------------------------------------------
fl_have(){ tmux has-session -t "agent-$1" 2>/dev/null; }

fl_agent_pane(){ # <ws> → the agent pane's %id (second pane; sole pane if only one exists)
  local p; p=$(tmux list-panes -t "agent-$1:main" -F '#{pane_id}' 2>/dev/null) || return 1
  [ -n "$p" ] || return 1
  if [ "$(printf '%s\n' "$p" | wc -l)" -ge 2 ]; then printf '%s\n' "$p" | sed -n 2p
  else printf '%s\n' "$p" | head -1; fi
}

fl_pane_text(){ # <ws> → visible text of the agent pane (wrapped lines joined)
  local p; p=$(fl_agent_pane "$1") || return 1
  tmux capture-pane -p -J -t "$p" 2>/dev/null
}

ws_busy(){ # <ws> → rc 0 while a live turn owns the pane ('esc to interrupt' = the only reliable
  # busy footer; spinner glyphs persist in idle panes and false-positive)
  tmux capture-pane -p -t "agent-$1:main.2" 2>/dev/null | grep -q "esc to interrupt"
}

# ---------------------------------------------------------------------------------------------
# process liveness — pane_current_command reports `bash` for a healthy agent (the agent is a CHILD
# of the launcher), and the tree depth varies by isolation tier (confined = 4 levels via bwrap),
# so liveness is a FULL recursive walk matched against known agent process names.
# ---------------------------------------------------------------------------------------------
fl_proctree(){ local p="$1" c; echo "$p"; for c in $(pgrep -P "$p" 2>/dev/null); do fl_proctree "$c"; done; }

fl_agent_alive(){ # <ws> → rc 0 if any live agent-ish process exists under the session's panes
  local pane pid comm
  for pane in $(tmux list-panes -t "agent-$1:main" -F '#{pane_pid}' 2>/dev/null); do
    for pid in $(fl_proctree "$pane"); do
      comm=$(cat "/proc/$pid/comm" 2>/dev/null) || continue
      case "$comm" in claude|claude-bin|node|bun|codex|opencode|ssh|bwrap) return 0 ;; esac
    done
  done
  return 1
}

# ---------------------------------------------------------------------------------------------
# messaging — the system-envelope standard (2026-07-17): every machine-mediated message names the
# SCRIPT it came from ("system:<script>"), so a machine-sent turn is never indistinguishable from a
# human one. fl_send routes through fleet-msg (delivery + durable provenance store).
# ---------------------------------------------------------------------------------------------
fl_send(){ # <to> <text...> → send as system:$FL_SCRIPT (FLEET_MSG_FROM overrides)
  local to="$1"; shift
  local from="${FLEET_MSG_FROM:-system:${FL_SCRIPT:-$(basename -- "${0:-fleet}")}}"
  "$A/bin/fleet-msg" send --to "$to" --from "$from" "$*"
}

# ---------------------------------------------------------------------------------------------
# accounts
# ---------------------------------------------------------------------------------------------
fl_cfg_of(){ "$A/bin/account-profile" "$1"; }   # label → validated profile dir (allow-list enforced)

fl_email_of(){ # <cfg-dir> → login email from its .claude.json ('' on any failure)
  python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('oauthAccount',{}).get('emailAddress',''))" \
    "$1/.claude.json" 2>/dev/null || true
}

fl_fable_marker_fresh(){ # <label> → rc 0 while a FRESH .fable-cap marker exists (content = expiry
  # epoch, written by fable-cap-mark). Expired/garbled markers are removed on read.
  local f="$ACCTS/.fable-cap.$1" exp
  [ -f "$f" ] || return 1
  exp=$(head -1 "$f" 2>/dev/null | tr -cd '0-9')
  if [ -z "$exp" ] || [ "$(date +%s)" -ge "$exp" ]; then rm -f "$f"; return 1; fi
  return 0
}

fable_blocked(){ # <acct> → rc 0 when <acct> cannot serve Fable NOW: fresh marker OR the usage
  # endpoint's per-model fable utilization >= FABLE_BLOCK_PCT (default 99). Endpoint errors → not
  # blocked (fail open; the marker path still catches walls seen by real calls).
  fl_fable_marker_fresh "$1" && return 0
  local u
  u=$("${AUSAGE:-$A/bin/account-usage}" --json "$1" 2>/dev/null | python3 -c '
import json,sys
try:
    r = json.loads(sys.stdin.readline() or "{}")
    fb = r.get("fable") or {}
    u = fb.get("utilization")
    print("" if u is None else u)
except Exception:
    print("")')
  [ -n "$u" ] && awk -v u="$u" -v p="${FABLE_BLOCK_PCT:-99}" 'BEGIN{exit !(u>=p)}'
}

# ---------------------------------------------------------------------------------------------
# post-swap verification / recovery (extracted from swap-fleet so nothing has to source a script
# whose top half takes the swap lock)
# ---------------------------------------------------------------------------------------------
verify_submitted(){ # <ws> — a nudge is paste+Enter; if the Enter was swallowed the text SITS in the
  # input box (observed live, twice in one day). Verify the box cleared and LOG if not — NO
  # automated kick (the operator removed auto-submit 2026-07-17: the RC bridge can REPLAY drafts
  # into a composer, so pressing Enter on whatever is sitting there is unsafe by design; the same
  # replayed draft was auto-fired into an agent three times in one afternoon). Stranded text stays
  # VISIBLE for the operator; the only submit path is a manual, envelope-tagged `fleet-msg kick <ws>`.
  local ws="$1"; sleep 2
  local line; line=$(tmux capture-pane -p -t "agent-$ws:main.2" 2>/dev/null | grep "❯" | tail -1)
  if printf '%s' "$line" | grep -qE "❯ .+"; then
    fl_log "nudge for $ws did not submit — LEFT VISIBLE in its composer (auto-submit removed 2026-07-17; manual: fleet-msg kick $ws)"
  fi
}

verify_rc_retry(){ # <ws> — after a swap relaunch the FIRST RC registration against the freshly-
  # swapped account fails transiently and the CLI never retries (observed live: 2 of 13 workspaces
  # kept RC after one swap). The agent idles right after the bounce, so re-triggering
  # /remote-control registers cleanly under the correct name (run-claude passes -n). Best-effort;
  # rc 0 = registered/busy-skip, 1 = not seen.
  local w="$1" pane attempt txt
  ws_rc_on "$w" || return 0
  pane=$(fl_agent_pane "$w") || return 1
  for attempt in 1 2 3; do
    txt=$(tmux capture-pane -p -t "$pane" -S -20 2>/dev/null || true)
    printf '%s' "$txt" | grep -qiE 'remote-control is active|Continue here, on your phone' && return 0
    printf '%s' "$txt" | grep -q 'esc to interrupt' && return 0   # busy (a live turn owns the box) — leave it
    tmux send-keys -t "$pane" C-u 2>/dev/null || true; sleep 1
    tmux send-keys -t "$pane" "/remote-control" 2>/dev/null || true; sleep 3
    tmux send-keys -t "$pane" Enter 2>/dev/null || true; sleep 5
  done
  txt=$(tmux capture-pane -p -t "$pane" -S -20 2>/dev/null || true)
  printf '%s' "$txt" | grep -qiE 'remote-control is active|Continue here' && return 0
  return 1
}

# ---------------------------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------------------------
fl_dur_to_secs(){ # 45m / 6h / 2d / 30s → seconds; rc 1 on garbage
  local d="$1" n unit
  n="${d%[smhd]}"; unit="${d##*[0-9]}"
  [[ "$n" =~ ^[0-9]+$ ]] || return 1
  case "$unit" in
    s) echo "$n" ;; m) echo $((n*60)) ;; h) echo $((n*3600)) ;; d) echo $((n*86400)) ;;
    *) return 1 ;;
  esac
}
