#!/usr/bin/env bash
# test-grok-ready.sh — unit tests for bin/grok-ready (grok's ~6h OAuth token does not self-refresh
# in headless mode; this helper is the pre-flight any automated grok run should call).
# Static-path tests only (fake auth.json via GROK_AUTH_F); no login flows, no model spend.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/../bin"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-grok-ready.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  - $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL- $1"; }
check_rc(){ # <desc> <want-rc> <cmd...>
  local d="$1" want="$2"; shift 2
  local rc=0; "$@" >/dev/null 2>&1 || rc=$?
  if [ "$rc" = "$want" ]; then ok "$d"; else bad "$d (rc=$rc want=$want)"; fi
}

command -v grok >/dev/null 2>&1 || { echo "SKIP: no grok CLI on this machine"; echo "PASS=0 FAIL=0"; exit 0; }

future=$(python3 -c "import datetime;print((datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=3)).isoformat().replace('+00:00','Z'))")
past=$(python3 -c "import datetime;print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=1)).isoformat().replace('+00:00','Z'))")
soon=$(python3 -c "import datetime;print((datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(minutes=10)).isoformat().replace('+00:00','Z'))")

mk(){ printf '{"acct":{"expires_at":"%s"}}' "$1" > "$TMP/auth.json"; }

echo "== grok-ready static auth checks =="
mk "$future"
check_rc "unexpired credential → rc 0 (ready)"        0 env GROK_AUTH_F="$TMP/auth.json" "$BIN/grok-ready"
mk "$past"
check_rc "expired credential → rc 1 (not ready)"      1 env GROK_AUTH_F="$TMP/auth.json" "$BIN/grok-ready"
check_rc "missing auth file → rc 1"                   1 env GROK_AUTH_F="$TMP/nope.json" "$BIN/grok-ready"
printf 'not json' > "$TMP/auth.json"
check_rc "garbled auth file → rc 1 (no crash)"        1 env GROK_AUTH_F="$TMP/auth.json" "$BIN/grok-ready"
printf '{"acct":{"no_expiry_here":true}}' > "$TMP/auth.json"
check_rc "auth without expires_at → rc 1"             1 env GROK_AUTH_F="$TMP/auth.json" "$BIN/grok-ready"
mk "$soon"
out=$(GROK_AUTH_F="$TMP/auth.json" "$BIN/grok-ready" 2>&1)
if echo "$out" | grep -q "WARNING <30m"; then ok "near-expiry warns about long agentic runs"; else bad "near-expiry warns about long agentic runs"; fi
mk "$past"
out=$(GROK_AUTH_F="$TMP/auth.json" "$BIN/grok-ready" 2>&1)
if echo "$out" | grep -q "does not self-refresh"; then ok "failure message names the root cause"; else bad "failure message names the root cause"; fi

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
