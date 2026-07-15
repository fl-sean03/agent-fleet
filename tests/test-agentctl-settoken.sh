#!/usr/bin/env bash
# test-agentctl-settoken.sh — `agentctl set-token`, folded in from the retired clientctl (2026-07-15).
# set-token is a DOWNGRADE path (Claude API mode: Sonnet only, no Remote Control), so it must be
# hard to trigger by accident and must never be silently applied to a project workspace.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-settoken.XXXXXX"); trap 'rm -rf "$TMP"' EXIT
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  - $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL- $1"; }
eq(){ [ "$1" = "$2" ] || { echo "       got: '$1'  want: '$2'"; return 1; }; }
check(){ local d="$1"; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }
check_not(){ local d="$1"; shift; if "$@"; then bad "$d"; else ok "$d"; fi; }

export HOME="$TMP"; A="$TMP/.agents"
mkdir -p "$A/projects" "$TMP/clients/acme" "$TMP/work/rig"
ln -sfn "$REPO/bin" "$A/bin"
printf 'ROOT="$HOME/clients/acme"\nKIND="confined"\nSANDBOX="bwrap"\n' > "$A/projects/acme.env"
printf 'ROOT="$HOME/work/rig"\n' > "$A/projects/rig.env"
AC="$REPO/bin/agentctl"

echo "== set-token: the headless auth mode =="
check "agentctl has set-token"  bash -c "'$AC' set-token 2>&1 | grep -qv 'unknown'"
"$AC" set-token acme sk-ant-oat-TESTTOKEN >/dev/null 2>&1
eq "$(cat "$A/confined-cfg/acme/oauth-token")" "sk-ant-oat-TESTTOKEN" && ok "token written verbatim (no trailing newline)" || bad "token content"
eq "$(stat -c %a "$A/confined-cfg/acme/oauth-token")" "600" && ok "token file is 0600"      || bad "token perms"
eq "$(stat -c %a "$A/confined-cfg/acme")" "700"             && ok "confined cfg dir is 0700"  || bad "cfg dir perms"
check "warns it is Sonnet-only / no RC (a downgrade)" bash -c "'$AC' set-token acme sk-x 2>&1 | grep -qi 'sonnet'"
check "points at agentctl login for Opus+RC"          bash -c "'$AC' set-token acme sk-x 2>&1 | grep -q 'agentctl login acme'"

echo "== it is fail-closed =="
check_not "refuses a PROJECT workspace"   bash -c "'$AC' set-token rig sk-x >/dev/null 2>&1"
check_not "refuses an unknown workspace"  bash -c "'$AC' set-token nosuch sk-x >/dev/null 2>&1"
check_not "refuses a missing token arg"   bash -c "'$AC' set-token acme >/dev/null 2>&1"

echo "== the retired entrypoint is really gone =="
check_not "no separate client tool in the repo"  test -e "$REPO/bin/clientctl"
check_not "no clientctl referenced by any live tool" bash -c "grep -rl 'clientctl' '$REPO/bin/' 2>/dev/null | grep -qv agentctl"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
