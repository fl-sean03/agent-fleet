#!/usr/bin/env bash
# test-fleet-lib.sh — bin/fleet-lib.sh, the single implementation of the harness's shared
# primitives (descriptor parsing, pane/busy checks, dormancy gate, envelope send, markers).
# Everything runs in a sandbox: fake $HOME/$A, PATH-stubbed tmux/fleet-msg, no live fleet contact.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; BIN="$HERE/../bin"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-fleetlib.XXXXXX"); trap 'rm -rf "$TMP"' EXIT
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  - $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL- $1"; }
check(){ local d="$1"; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }
check_not(){ local d="$1"; shift; if "$@"; then bad "$d"; else ok "$d"; fi; }
eq(){ [ "$1" = "$2" ] || { echo "       got: '$1'  want: '$2'"; return 1; }; }

export HOME="$TMP"
export A="$TMP/.agents" ACCTS="$TMP/.agents/accounts"
export FLEET_CONFINED_ROOT="$TMP/confined"
mkdir -p "$A/projects" "$A/bin" "$A/confined-cfg/exampleco/projects/-work" "$ACCTS" \
         "$TMP/.claude/projects" "$TMP/stubbin" "$TMP/confined/exampleco" "$TMP/work/alpha"
export PATH="$TMP/stubbin:$PATH"

. "$BIN/fleet-lib.sh"

echo "== ws_get: the one descriptor parser =="
cat > "$A/projects/alpha.env" <<EOF
# project workspace
ROOT="\$HOME/work/alpha"
AGENTS="claude"
MODEL="claude-fable-5"   # pinned
RC_NAME="Alpha Workspace"
REMOTE_CONTROL="on"
UNQUOTED=bare-value   # trailing comment
SESSION_ID="aaaaaaaa-1111-2222-3333-444444444444"
EOF
check "quoted value"                       eq "$(ws_get alpha MODEL)" "claude-fable-5"
check "quoted value keeps inner spaces"    eq "$(ws_get alpha RC_NAME)" "Alpha Workspace"
check "unquoted value strips comment+ws"   eq "$(ws_get alpha UNQUOTED)" "bare-value"
check_not "missing key rc=1"               ws_get alpha NOPE
check "ws_root expands \$HOME"             eq "$(ws_root alpha)" "$TMP/work/alpha"
check "ws_model reads the pin"             eq "$(ws_model alpha)" "claude-fable-5"
check "ws_rc_on on"                        ws_rc_on alpha
check "ws_agents defaults to claude"       eq "$(ws_agents alpha)" "claude"
check "ws_kind derives project"            eq "$(ws_kind alpha)" "project"

echo "== the retired-comment regression: a commented #RESUME-retired line must never match =="
cat > "$A/projects/exampleco.env" <<EOF
ROOT="\$HOME/confined/exampleco"
AGENTS="claude"
#RESUME-retired-2026-07-03="bbbbbbbb-5555-6666-7777-888888888888"
RESUME="cccccccc-9999-aaaa-bbbb-cccccccccccc"
EOF
check "RESUME fallback returns the REAL id"   eq "$(ws_session_id exampleco)" "cccccccc-9999-aaaa-bbbb-cccccccccccc"
check "SESSION_ID preferred when present"     eq "$(ws_session_id alpha)" "aaaaaaaa-1111-2222-3333-444444444444"
check "confined kind derived from confined root" eq "$(ws_kind exampleco)" "confined"

echo "== ws_transcript: project store vs confined isolated store =="
enc=$(fl_encode_cwd "$TMP/work/alpha")
check "project transcript path"  eq "$(ws_transcript alpha)" "$TMP/.claude/projects/$enc/aaaaaaaa-1111-2222-3333-444444444444.jsonl"
check "confined transcript path" eq "$(ws_transcript exampleco)" "$A/confined-cfg/exampleco/projects/-work/cccccccc-9999-aaaa-bbbb-cccccccccccc.jsonl"

echo "== ws_active_within: the 6h automated-wake dormancy gate (operator policy 2026-07-17) =="
mkdir -p "$TMP/.claude/projects/$enc"
tj="$TMP/.claude/projects/$enc/aaaaaaaa-1111-2222-3333-444444444444.jsonl"
touch "$tj"
check "fresh transcript → active"                     ws_active_within alpha
touch -d "-7 hours" "$tj"
check_not "7h-old transcript → NOT active (default 6h)" ws_active_within alpha
check "explicit wider window (8h) → active"           ws_active_within alpha 8
check "FL_WAKE_ACTIVE_HOURS env widens the default"   env FL_WAKE_ACTIVE_HOURS=8 bash -c ". '$BIN/fleet-lib.sh'; ws_active_within alpha"
rm -f "$tj"
check_not "missing transcript → NOT active"           ws_active_within alpha
h=$(touch -d "-7 hours" "$tj"; fl_idle_hours alpha)
check "fl_idle_hours reports ~7.0"                    bash -c "case '$h' in 6.9|7.0|7.1) exit 0;; *) exit 1;; esac"

echo "== fl_fable_marker_fresh: fresh honored, expired/garbled removed =="
now=$(date +%s)
echo $((now + 3600)) > "$ACCTS/.fable-cap.m1"
check "fresh marker → rc 0"            fl_fable_marker_fresh m1
echo $((now - 10)) > "$ACCTS/.fable-cap.m2"
check_not "expired marker → rc 1"      fl_fable_marker_fresh m2
check "expired marker removed on read" test ! -f "$ACCTS/.fable-cap.m2"
echo "garbage" > "$ACCTS/.fable-cap.m3"
check_not "garbled marker → rc 1"      fl_fable_marker_fresh m3
check "garbled marker removed on read" test ! -f "$ACCTS/.fable-cap.m3"

echo "== fl_send: the system-envelope standard =="
cat > "$A/bin/fleet-msg" <<'EOS'
#!/usr/bin/env bash
echo "fleet-msg $*" >> "$HOME/calls.txt"
EOS
chmod +x "$A/bin/fleet-msg"
rm -f "$HOME/calls.txt"; FL_SCRIPT=test-script fl_send main "hello there" >/dev/null 2>&1
check "sender = system:<FL_SCRIPT>"    bash -c "grep -q -- '--from system:test-script' '$HOME/calls.txt'"
check "recipient + body pass through"  bash -c "grep -q -- '--to main hello there' '$HOME/calls.txt' || grep -q 'hello there' '$HOME/calls.txt'"
rm -f "$HOME/calls.txt"; FLEET_MSG_FROM=system:override FL_SCRIPT=test-script fl_send main "x" >/dev/null 2>&1
check "FLEET_MSG_FROM overrides"       bash -c "grep -q -- '--from system:override' '$HOME/calls.txt'"

echo "== pane primitives (tmux stubbed) =="
cat > "$TMP/stubbin/tmux" <<'EOS'
#!/usr/bin/env bash
case "$*" in
  *list-panes*agent-two*)      printf '%%1\n%%2\n' ;;
  *list-panes*agent-one*)      printf '%%7\n' ;;
  *"capture-pane -p -t agent-busy1:main.2"*) printf 'working\n  esc to interrupt\n❯ \n' ;;
  *"capture-pane -p -t agent-idle1:main.2"*) printf '❯ \n' ;;
  *has-session*agent-up*)      exit 0 ;;
  *has-session*)               exit 1 ;;
esac
exit 0
EOS
chmod +x "$TMP/stubbin/tmux"
check "fl_agent_pane picks the SECOND pane"  eq "$(fl_agent_pane two)" "%2"
check "single-pane session falls back"       eq "$(fl_agent_pane one)" "%7"
check "ws_busy sees a live turn"             ws_busy busy1
check_not "idle pane is not busy"            ws_busy idle1
check "fl_have up"                           fl_have up
check_not "fl_have down"                     fl_have down

echo "== fl_dur_to_secs =="
check "45m" eq "$(fl_dur_to_secs 45m)" 2700
check "6h"  eq "$(fl_dur_to_secs 6h)"  21600
check "2d"  eq "$(fl_dur_to_secs 2d)"  172800
check "30s" eq "$(fl_dur_to_secs 30s)" 30
check_not "garbage rejected" fl_dur_to_secs bogus

echo "== fl_log format =="
LOG="$TMP/l.log" fl_log "hello world"
check "one standard line format" bash -c "grep -qE '^\[20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z\] hello world$' '$TMP/l.log'"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
