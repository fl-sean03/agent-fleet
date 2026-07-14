#!/usr/bin/env bash
# Regression tests for the individuals-only account convention.  Everything runs against a temp HOME;
# no tmux, network, or real credential is touched.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
BIN="$ROOT/bin"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-account-convention.XXXXXX")
trap 'rm -rf "$TMP"' EXIT
export HOME="$TMP/home"
A="$HOME/.agents"
mkdir -p "$A/accounts" "$A/bin" "$A/confined-cfg/example-confined"
ln -s "$BIN/account-profile" "$A/bin/account-profile"

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  - $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL- $1"; }
check(){ local d="$1"; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }
check_not(){ local d="$1"; shift; if "$@"; then bad "$d"; else ok "$d"; fi; }
eq(){ [ "$1" = "$2" ] || { echo "       got: '$1'  want: '$2'"; return 1; }; }

labels=(account-a account-b account-c)
emails=(account-a@example.com account-b@example.com account-c@example.com)
for i in "${!labels[@]}"; do
  label="${labels[$i]}"; email="${emails[$i]}"
  mkdir -p "$A/accounts/$label"
  printf '{"claudeAiOauth":{"accessToken":"fake"}}\n' > "$A/accounts/$label/.credentials.json"
  printf '{"oauthAccount":{"emailAddress":"%s"}}\n' "$email" > "$A/accounts/$label/.claude.json"
done
printf '%s\n' "${labels[@]}" > "$A/accounts/.rotation"
printf '%s\n' account-a > "$A/accounts/.active"

echo "== profile resolver =="
r=$("$BIN/account-profile" --active)
check "active profile resolves" eq "$r" "$A/accounts/account-a"
check_not "host is rejected" "$BIN/account-profile" host
check_not "unknown labels are rejected" "$BIN/account-profile" unknown

echo "== launchers fail closed =="
printf '%s\n' host > "$A/accounts/.active"
check_not "run-oneshot rejects host active pointer" "$BIN/run-oneshot" ping
check_not "run-claude rejects host descriptor account" env ACCOUNT=host "$BIN/run-claude"
check_not "swap-fleet rejects host target" "$BIN/swap-fleet" host --dry
check_not "usage command rejects host target" "$BIN/account-usage" host --mock /dev/null
printf '%s\n' account-a > "$A/accounts/.active"

echo "== confined workspace OAuth mapping =="
printf '{"claudeAiOauth":{"accessToken":"fake-confined workspace"}}\n' > "$A/confined-cfg/example-confined/.credentials.json"
printf '{"oauthAccount":{"emailAddress":"account-a@example.com"}}\n' > "$A/confined-cfg/example-confined/.claude.json"
AGENTCTL_TEST=1 source "$BIN/agentctl"
r=$(confined_login_label example-confined)
check "interactive confined workspace email maps to named profile" eq "$r" account-a
record_confined_login example-confined >/dev/null
r=$(cat "$A/confined-cfg/example-confined/.account")
check "login records the correct marker" eq "$r" account-a
check "login refreshes the named credential stash" cmp -s "$A/confined-cfg/example-confined/.credentials.json" "$A/confined-cfg/example-confined/.credentials.account-a.json"

echo "== brain routing =="
r=$(PYTHONPATH="$ROOT" python3 -c 'from brain.engine.agentcall import _cfg_dir; print(_cfg_dir())')
check "brain uses fleet-active named profile" eq "$r" "$A/accounts/account-a"
printf '%s\n' host > "$A/accounts/.active"
check_not "brain rejects a host active pointer" bash -c 'PYTHONPATH="$1" python3 -c "from brain.engine.agentcall import _cfg_dir; _cfg_dir()" >/dev/null 2>&1' _ "$ROOT"

echo "== RC single-session ordering + no-hold conventions (greps pin the structure) =="
# the operator hard rule 2026-07-13: one RC session per workspace -> every account-changing path must STOP
# the old claude before UP on the new account. These greps pin the ordering so a refactor that
# reorders the phases fails a test, not the fleet.
first_line(){ grep -n "$2" "$1" | head -1 | cut -d: -f1; }
sa_stop=$(first_line "$ROOT/bin/swap-account" 'stop ')
sa_up=$(first_line "$ROOT/bin/swap-account" '" up ')
check "swap-account: stop precedes up" bash -c "[ -n '$sa_stop' ] && [ -n '$sa_up' ] && [ '$sa_stop' -lt '$sa_up' ]"
sf_stop=$(first_line "$ROOT/bin/swap-fleet" 'AGENTCTL" stop')
sf_up=$(first_line "$ROOT/bin/swap-fleet" 'AGENTCTL" up')
check "swap-fleet confined workspace loop: stop precedes up" bash -c "[ -n '$sf_stop' ] && [ -n '$sf_up' ] && [ '$sf_stop' -lt '$sf_up' ]"
check "swap-fleet per-model gate runs BEFORE the confined workspace stop" bash -c "g=\$(grep -n 'model_gate_skip \"\$c\"' '$ROOT/bin/swap-fleet' | head -1 | cut -d: -f1); [ -n \"\$g\" ] && [ \"\$g\" -lt '$sf_stop' ]"
check "agentctl do_up refuses when already up (no second RC)" grep -q 'already up' "$ROOT/bin/agentctl"
# the removed .hold must stay removed: ignored loudly, never honored, never written by any tool
check "account-watch loudly ignores a stale .hold" grep -q 'stale accounts/.hold' "$ROOT/bin/account-watch"
check "agentctl help no longer advertises the removed .hold" bash -c "! grep -q 'touch ~/.agents/accounts/.hold' '$ROOT/bin/agentctl'"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
