#!/usr/bin/env bash
# test-fleet-msg.sh — unit tests for the messaging engine (bin/fleet-msg). Delivery is direct-into-
# conversation (paste+submit into the recipient's claude pane); the durable provenance store is a
# queryable messages.db + append-only log.jsonl.
#
# SAFETY: tmux is NOT sandboxed by FLEET_MSG_ROOT, so tests use FAKE recipient names with no live
# session (zmain/zgpu/…) and --no-submit on send, so no real agent pane is ever pasted into. Live
# pane submission is proven separately against a throwaway agent.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MSG="$HERE/../bin/fleet-msg"
export FLEET_MSG_ROOT
FLEET_MSG_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/test-fmsg.XXXXXX")"
trap 'rm -rf "$FLEET_MSG_ROOT"' EXIT

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  - $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL- $1"; }
check(){ local d="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi; }
send(){ WS_NAME="$1" python3 "$MSG" send --to "$2" --no-submit "$3"; }

echo "== delivery status (down recipient → queued, never silently dropped) =="
ID=$(send zinfra zmain "look at the ws-gpu queue" | grep -oE 'msg-\S+' | head -1)
check "send returns an id"                          test -n "$ID"
check "down recipient is QUEUED (reported)"         bash -c "python3 '$MSG' log --json | grep -q '\"status\": \"queued\"'"
check "queued message written to pending/"          test -f "$FLEET_MSG_ROOT/pending/zmain.jsonl"

echo "== durable provenance store (append-only log + queryable DB) =="
check "append-only log.jsonl exists"                test -f "$FLEET_MSG_ROOT/log.jsonl"
check "queryable messages.db exists"                test -f "$FLEET_MSG_ROOT/messages.db"
j=$(python3 "$MSG" log --json)
check "record: from"                                bash -c "echo '$j' | grep -q '\"sender\": \"zinfra\"'"
check "record: to"                                  bash -c "echo '$j' | grep -q '\"recipient\": \"zmain\"'"
check "record: full body"                           bash -c "echo '$j' | grep -q 'ws-gpu queue'"
check "record: msg_id (correlation key)"            bash -c "echo '$j' | grep -q '$ID'"
lj=$(python3 -c "import json;print([json.loads(l) for l in open('$FLEET_MSG_ROOT/log.jsonl')][0])")
check "provenance: sender_session field present"    bash -c "echo \"$lj\" | grep -q 'sender_session'"
check "provenance: recipient session field present"  bash -c "echo \"$lj\" | grep -q \"'session'\""

echo "== msg_id embedded in the delivered turn (grep-able in recipient transcript) =="
w=$(python3 -c "import importlib.machinery as m; fm=m.SourceFileLoader('fm','$MSG').load_module(); print(fm._wire({'from':'a','id':'msg-XYZ','body':'hi'}))")
check "wired text carries the msg_id"               bash -c "echo '$w' | grep -q 'msg-XYZ'"
check "wired text carries sender attribution"       bash -c "echo '$w' | grep -q 'message from a'"

echo "== queryable filters =="
send zfields zmain "second msg" >/dev/null
send zfields zgpu  "third msg"  >/dev/null
check "filter --from"                               bash -c "python3 '$MSG' log --from zfields --json | grep -q 'second msg' && ! python3 '$MSG' log --from zfields --json | grep -q 'ws-gpu queue'"
check "filter --to zgpu"                            bash -c "python3 '$MSG' log --to zgpu --json | grep -q 'third msg'"

echo "== addressing (routing) =="
send zmain za,zb "both" >/dev/null
check "multi-recipient za"                          bash -c "python3 '$MSG' log --to za --json | grep -q 'both'"
check "multi-recipient zb"                          bash -c "python3 '$MSG' log --to zb --json | grep -q 'both'"

echo "== flush leaves messages queued while recipient still down (fake target, no live pane) =="
out=$(python3 "$MSG" flush zmain)
check "flush reports still-down (no pane)"           bash -c "echo '$out' | grep -qi 'still down'"
check "pending preserved when flush can't deliver"   test -f "$FLEET_MSG_ROOT/pending/zmain.jsonl"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
