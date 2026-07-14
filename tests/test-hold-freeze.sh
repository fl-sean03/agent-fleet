#!/usr/bin/env bash
# test-hold-freeze.sh — unit tests for the two formalized maintenance primitives (2026-07-13,
# replacing the ad-hoc foldback-drain.sh / aw-reenable scar tissue):
#   fleet-hold   — work-tempo hold: gates swap nudges + agent tempo, NEVER rotation (marker file)
#   watch-freeze — visible account-watch.timer stop with a GUARANTEED scheduled auto-thaw (dry-run)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/../bin"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-hold.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  - $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL- $1"; }
check(){ local d="$1"; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }
check_not(){ local d="$1"; shift; if "$@"; then bad "$d"; else ok "$d"; fi; }

export A="$TMP/agents" FLEET_HOLD_F="$TMP/agents/.fleet-hold"
mkdir -p "$A/accounts"
FH="$BIN/fleet-hold"

echo "== fleet-hold lifecycle =="
check_not "no marker → not active"                        "$FH" --active
check_not "status rc=1 when inactive"                     bash -c "'$FH' status >/dev/null"
"$FH" on drain window for account-c reset >/dev/null
check "on (indefinite) → active"                          "$FH" --active
check "status rc=0 + prints reason" bash -c "'$FH' status | grep -q 'drain window for account-c reset'"
"$FH" off >/dev/null
check_not "off → inactive"                                "$FH" --active

echo "== timed hold + auto-expiry on read =="
"$FH" on --for 1h weekly reset consolidation >/dev/null
check "timed hold active within window"                   "$FH" --active
check "status shows an until timestamp" bash -c "'$FH' status | grep -qE 'until 20[0-9][0-9]-'"
# hand-expire the marker: a past epoch must be treated as no-hold AND the marker removed on read
printf '%s\nexpired-test\n' "$(( $(date -u +%s) - 60 ))" > "$FLEET_HOLD_F"
check_not "expired hold reads as inactive"                "$FH" --active
check "expired marker removed on read"                    test ! -f "$FLEET_HOLD_F"

echo "== bad input =="
check_not "bad --for duration rejected"                   "$FH" on --for banana 2>/dev/null
check_not "bad --until date rejected"                     "$FH" on --until "not a date" 2>/dev/null
check_not "unknown verb rejected"                         "$FH" frobnicate 2>/dev/null

echo "== watch-freeze (dry-run: command plan only, no systemd) =="
export WATCH_FREEZE_DRY=1 LOG="$TMP/watch.log"
WF="$BIN/watch-freeze"
out=$("$WF" 45m disk maintenance 2>&1)
check "freeze plans the auto-thaw BEFORE stopping the timer" bash -c "echo '$out' | grep -n 'systemd-run' | cut -d: -f1 | head -1 | xargs -I{} test {} -lt \"\$(echo '$out' | grep -n 'stop account-watch.timer' | cut -d: -f1 | tail -1)\""
check "freeze schedules with the requested duration"      bash -c "echo '$out' | grep -q -- '--on-active=2700s'"
check "freeze logged to watch.log"                        bash -c "grep -q 'FROZEN for 45m (disk maintenance)' '$LOG'"
check "bare/empty arg is the harmless status verb (by design)" bash -c "'$WF' '' 2>/dev/null | grep -q 'account-watch.timer:'"
check_not "garbage duration refused"                      "$WF" banana 2>/dev/null
check_not "freeze > 24h refused (outage, not maintenance)" "$WF" 2d 2>/dev/null
check_not "freeze < 60s refused (pointless)"              "$WF" 30s 2>/dev/null
out=$("$WF" thaw 2>&1)
check "thaw starts the timer"                             bash -c "echo '$out' | grep -q 'start account-watch.timer'"
check "thaw cancels the pending auto-thaw"                bash -c "echo '$out' | grep -q 'stop watch-thaw.timer'"
check "thaw logged"                                       bash -c "grep -q 'THAWED' '$LOG'"

echo "== swap-fleet consults fleet-hold (convention greps) =="
check "swap-fleet calls fleet-hold --active"              grep -q 'fleet-hold" --active' "$BIN/swap-fleet"
check "hold nudge says stay idle, not keep going"         bash -c "grep -q 'do NOT resume work' '$BIN/swap-fleet'"
check "fleet-hold never referenced by account-watch (rotation ungated)" bash -c "! grep -q 'fleet-hold' '$BIN/account-watch'"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
