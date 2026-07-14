#!/usr/bin/env bash
# test-agentctl-workspaces.sh — workspace lifecycle in a throwaway HOME. No tmux agents are started,
# no credentials, no network: this exercises descriptor creation, kind resolution, and listing.
#
# Regression it pins (found 2026-07-14): agentctl's load() ended with `[ "$KIND" = project ] && case…`.
# Under `set -e` that returns 1 for ANY descriptor already declaring KIND="confined", which killed
# every caller — `agentctl status` printed its header and died the moment a confined workspace existed,
# and `agentctl new` exited 1 despite succeeding.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT="$(cd "$HERE/.." && pwd)"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-ws.XXXXXX")
trap 'rm -rf "$TMP"' EXIT
export HOME="$TMP"

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  - $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL- $1"; }
eq(){ [ "$1" = "$2" ] || { echo "       got: '$1'  want: '$2'"; return 1; }; }
check(){ local d="$1"; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }
rc0(){ local d="$1"; shift; "$@" >/dev/null 2>&1; local r=$?; [ $r = 0 ] && ok "$d" || bad "$d (exit $r)"; }

bash "$KIT/install.sh" >/dev/null 2>&1
AC="$KIT/bin/agentctl"

echo "== create =="
rc0 "new project exits 0"            bash "$AC" new demo --root "$TMP/work/demo"
rc0 "new confined exits 0"           bash "$AC" new acme --confined "Acme Corp"
check "project descriptor written"   test -f "$TMP/.agents/projects/demo.env"
check "confined descriptor written"  test -f "$TMP/.agents/projects/acme.env"
check "confined scaffold created"    test -f "$TMP/confined/acme/AGENTS.md"
check "display name templated in"    grep -q "Acme Corp" "$TMP/confined/acme/WORKSPACE.md"
check "fixed SESSION_ID assigned"    grep -qE '^SESSION_ID="[0-9a-f-]{36}"' "$TMP/.agents/projects/demo.env"

echo "== list (the regression) =="
rc0 "status exits 0 with a confined ws present" bash "$AC" status
out=$(bash "$AC" status 2>/dev/null)
check "confined workspace is VISIBLE in status" bash -c "grep -q 'acme' <<<'$out'"
check "project workspace is visible"            bash -c "grep -q 'demo' <<<'$out'"
check "confined ws reports kind=confined"       bash -c "grep -E 'acme +confined' <<<'$out' >/dev/null"
j=$(bash "$AC" status --json 2>/dev/null)
check "status --json is valid JSON"             bash -c "echo '$j' | python3 -m json.tool >/dev/null"
k=$(echo "$j" | python3 -c "import json,sys; print(next(w['kind'] for w in json.load(sys.stdin) if w['name']=='acme'))" 2>/dev/null)
check "json kind for a confined ws"             eq "$k" "confined"

echo "== kind auto-derivation (descriptor with no KIND= line) =="
printf 'ROOT="%s/confined/legacy"\n' "$TMP" > "$TMP/.agents/projects/legacy.env"
j=$(bash "$AC" status --json 2>/dev/null)
k=$(echo "$j" | python3 -c "import json,sys; print(next(w['kind'] for w in json.load(sys.stdin) if w['name']=='legacy'))" 2>/dev/null)
check "a KIND-less descriptor under the confined root derives confined" eq "$k" "confined"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
