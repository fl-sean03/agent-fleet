#!/usr/bin/env bash
# test-swap-client.sh — swap-account's CONFINED WORKSPACE case (bug found in production, 2026-07-15:
# swap-account only rewrote ACCOUNT= in the descriptor and moved no credential at all, leaving the
# client authenticating as the OLD account while the descriptor claimed the new one — a silent lie).
#
# The invariants under test, in order of how badly they burned us:
#   1. the client's OWN per-account stash is deployed — NEVER a copy of a host profile's credential
#      (refresh tokens are single-use and rotate; sharing one kills the host account);
#   2. a missing stash FAILS LOUDLY instead of silently leaving the old account live;
#   3. a dud stash (no refresh token) is refused (a real dud-credential incident);
#   4. the departing account's live credential is preserved into its stash, but never from an
#      empty/zeroed live file (that is how a real empty-stash dud was minted);
#   5. a pin is policy: moving state out from under it requires --repin.
# Fully sandboxed: fake HOME, stubbed agentctl. No tmux, no network, no real credentials.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-swapclient.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  - $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL- $1"; }
eq(){ [ "$1" = "$2" ] || { echo "       got: '$1'  want: '$2'"; return 1; }; }
check(){ local d="$1"; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }
check_not(){ local d="$1"; shift; if "$@"; then bad "$d"; else ok "$d"; fi; }

export HOME="$TMP"
A="$TMP/.agents"; CC="$A/confined-cfg/acme"
mkdir -p "$A"/{projects,bin,accounts/acct-a,accounts/acct-b} "$CC/backups" "$TMP/.local/bin" "$TMP/clients/acme"
ln -sfn "$REPO/bin/account-profile" "$A/bin/account-profile" 2>/dev/null
# account-profile validates labels against the rotation allow-list — the project path uses it
printf 'acct-a\nacct-b\n' > "$A/accounts/.rotation"
printf 'acct-a\n' > "$A/accounts/.active"
# stub agentctl: record stop/up instead of driving tmux
cat > "$TMP/.local/bin/agentctl" <<'EOF'
#!/usr/bin/env bash
printf '%s %s\n' "${1:-}" "${2:-}" >> "$HOME/agentctl.calls"; exit 0
EOF
chmod +x "$TMP/.local/bin/agentctl"
printf 'ROOT="$HOME/clients/acme"\nKIND="confined"\nACCOUNT="acct-a"\nSESSION_ID="aaaa"\n' > "$A/projects/acme.env"
# HOST profile credentials — the thing that must NEVER be copied into a confined workspace
printf '{"claudeAiOauth":{"refreshToken":"sk-HOST-ACCT-B-SECRET"}}\n' > "$A/accounts/acct-b/.credentials.json"
printf '{"claudeAiOauth":{"refreshToken":"sk-HOST-ACCT-A-SECRET"}}\n' > "$A/accounts/acct-a/.credentials.json"
# account-profile requires BOTH a credential and a .claude.json before it will resolve a profile
for _a in acct-a acct-b; do printf '{"oauthAccount":{"emailAddress":"%s@example.com"}}\n' "$_a" > "$A/accounts/$_a/.claude.json"; done

SA="$REPO/bin/swap-account"
reset_client(){
  rm -f "$CC"/.credentials* "$CC/.account" "$CC/.pinned-account" "$TMP/agentctl.calls"
  printf '{"claudeAiOauth":{"refreshToken":"sk-CLIENT-OWN-A"}}\n' > "$CC/.credentials.json"        # live = acct-a
  printf '{"claudeAiOauth":{"refreshToken":"sk-CLIENT-OWN-A"}}\n' > "$CC/.credentials.acct-a.json"
  printf 'acct-a\n' > "$CC/.account"
  sed -i '/^ACCOUNT=/d' "$A/projects/acme.env"; printf 'ACCOUNT="acct-a"\n' >> "$A/projects/acme.env"
}
live_token(){ grep -oP '"refreshToken":"\K[^"]+' "$CC/.credentials.json" 2>/dev/null; }

echo "== a missing stash FAILS LOUDLY and changes nothing =="
reset_client
out=$("$SA" acme acct-b 2>&1); rc=$?
check "exits non-zero"                       test "$rc" -ne 0
check "says the stash is missing"            bash -c "printf '%s' \"\$1\" | grep -q 'NO credential stash'" _ "$out"
check "points at the ONE correct remedy"     bash -c "printf '%s' \"\$1\" | grep -q 'agentctl login acme'" _ "$out"
eq "$(live_token)" "sk-CLIENT-OWN-A"      && ok "live credential untouched"        || bad "live credential changed"
eq "$(cat "$CC/.account")" "acct-a"       && ok ".account still says acct-a"       || bad ".account moved"
check_not "the agent was NOT bounced"        test -s "$TMP/agentctl.calls"
# THE ORIGINAL BUG: the descriptor must not claim an account the client is not using
eq "$(grep -oP '^ACCOUNT="\K[^"]+' "$A/projects/acme.env")" "acct-a" && ok "descriptor NOT rewritten to a lie" || bad "descriptor lies about the account"

echo "== a host profile's credential is NEVER deployed to a confined workspace =="
# the stash for acct-b is absent, but the HOST has an acct-b credential sitting right there
check_not "no host token leaked into the client's live file" grep -q 'sk-HOST' "$CC/.credentials.json"
check_not "no host token leaked into any client stash"       bash -c "grep -rq 'sk-HOST' '$CC'/.credentials.*.json 2>/dev/null"

echo "== a dud stash (no refresh token) is refused =="
reset_client
printf '{"claudeAiOauth":{"accessToken":"sk-ant-oat-x","refreshToken":""}}\n' > "$CC/.credentials.acct-b.json"
out=$("$SA" acme acct-b 2>&1); rc=$?
check "exits non-zero"                    test "$rc" -ne 0
check "names it a dud"                    bash -c "printf '%s' \"\$1\" | grep -qi 'dud\|NO refresh token'" _ "$out"
eq "$(live_token)" "sk-CLIENT-OWN-A"   && ok "live credential untouched by a dud"  || bad "dud was deployed"

echo "== the happy path: the client's OWN stash is deployed =="
reset_client
printf '{"claudeAiOauth":{"refreshToken":"sk-CLIENT-OWN-B"}}\n' > "$CC/.credentials.acct-b.json"
out=$("$SA" acme acct-b 2>&1); rc=$?
check "exits 0"                                    test "$rc" -eq 0
eq "$(live_token)" "sk-CLIENT-OWN-B"            && ok "live credential is now the client's OWN acct-b login" || bad "wrong credential deployed"
check_not "and is NOT the host's acct-b token"     grep -q 'sk-HOST' "$CC/.credentials.json"
eq "$(cat "$CC/.account")" "acct-b"             && ok ".account records the move"      || bad ".account not updated"
eq "$(grep -oP '^ACCOUNT="\K[^"]+' "$A/projects/acme.env")" "acct-b" && ok "descriptor agrees with reality" || bad "descriptor/state divergence"
check "the workspace was stopped and restarted"       bash -c "grep -q '^stop acme' '$TMP/agentctl.calls' && grep -q '^up acme' '$TMP/agentctl.calls'"
check "a backup of the previous live credential exists" bash -c "ls -A '$CC/backups/' | grep -q credentials"

echo "== the DEPARTING account's rotation is preserved into its stash =="
reset_client
printf '{"claudeAiOauth":{"refreshToken":"sk-CLIENT-OWN-A-ROTATED"}}\n' > "$CC/.credentials.json"   # live rotated since stash
printf '{"claudeAiOauth":{"refreshToken":"sk-CLIENT-OWN-B"}}\n' > "$CC/.credentials.acct-b.json"
"$SA" acme acct-b >/dev/null 2>&1
check "acct-a stash refreshed from the live file (latest rotation)" grep -q 'sk-CLIENT-OWN-A-ROTATED' "$CC/.credentials.acct-a.json"

echo "== but NEVER from an empty/zeroed live file (how a real empty-stash dud was minted) =="
reset_client
printf '{"claudeAiOauth":{"refreshToken":""}}\n' > "$CC/.credentials.json"                          # CLI zeroed it
printf '{"claudeAiOauth":{"refreshToken":"sk-CLIENT-OWN-B"}}\n' > "$CC/.credentials.acct-b.json"
out=$("$SA" acme acct-b 2>&1)
check "the good acct-a stash is NOT overwritten by the zeroed live file" grep -q 'sk-CLIENT-OWN-A' "$CC/.credentials.acct-a.json"
check "and it says so"                    bash -c "printf '%s' \"\$1\" | grep -qi 'EMPTY/zeroed'" _ "$out"
check "the switch still proceeds"         grep -q 'sk-CLIENT-OWN-B' "$CC/.credentials.json"

echo "== a pin is POLICY: state may not move out from under it silently =="
reset_client
printf '{"claudeAiOauth":{"refreshToken":"sk-CLIENT-OWN-B"}}\n' > "$CC/.credentials.acct-b.json"
printf 'acct-a\n' > "$CC/.pinned-account"
out=$("$SA" acme acct-b 2>&1); rc=$?
check "refuses to move a pinned workspace"        test "$rc" -ne 0
check "explains --repin"                       bash -c "printf '%s' \"\$1\" | grep -q -- '--repin'" _ "$out"
eq "$(live_token)" "sk-CLIENT-OWN-A"        && ok "credential untouched while pinned" || bad "pinned workspace was moved"
"$SA" acme acct-b --repin >/dev/null 2>&1
eq "$(live_token)" "sk-CLIENT-OWN-B"        && ok "--repin performs the move"         || bad "--repin did not move"
eq "$(cat "$CC/.pinned-account")" "acct-b"  && ok "--repin moves the pin too (no lie)" || bad "pin not updated"

echo "== --force must NOT be a back door to the pin (the override must never be automated) =="
# --force exists to override the already-on-target no-op for the repair path. If it ALSO overrode a
# pin, it would be a sanctioned route back to the exact lie this fix removes — reached deliberately
# instead of accidentally. Only --repin may move a pin, and it moves BOTH pin and state together.
reset_client
printf '{"claudeAiOauth":{"refreshToken":"sk-CLIENT-OWN-B"}}\n' > "$CC/.credentials.acct-b.json"
printf 'acct-a\n' > "$CC/.pinned-account"
out=$("$SA" acme acct-b --force 2>&1); rc=$?
check "--force does NOT override a pin"          test "$rc" -ne 0
eq "$rc" "3"                                  && ok "pin refusal exits 3 (policy) not 1 (failure)" || bad "pin refusal exit code: $rc"
eq "$(live_token)" "sk-CLIENT-OWN-A"          && ok "--force left the pinned credential alone"     || bad "--force moved a pinned workspace"
eq "$(cat "$CC/.pinned-account")" "acct-a"    && ok "--force left the pin alone"                   || bad "--force moved the pin"
check "refusal reads as policy, not failure"     bash -c "printf '%s' \"\$1\" | grep -qi 'policy, not a failure'" _ "$out"
# a real failure must stay distinguishable from a policy refusal
reset_client; rm -f "$CC/.credentials.acct-b.json"
"$SA" acme acct-b --force >/dev/null 2>&1; rc=$?
eq "$rc" "1" && ok "a missing stash still exits 1 (a real failure)" || bad "missing-stash exit code: $rc"
# and --repin moves pin + state together, never one without the other
reset_client
printf '{"claudeAiOauth":{"refreshToken":"sk-CLIENT-OWN-B"}}\n' > "$CC/.credentials.acct-b.json"
printf 'acct-a\n' > "$CC/.pinned-account"
"$SA" acme acct-b --repin >/dev/null 2>&1
eq "$(cat "$CC/.pinned-account")" "acct-b" && ok "--repin moves the pin"   || bad "--repin pin"
eq "$(cat "$CC/.account")" "acct-b"        && ok "--repin moves the state" || bad "--repin state"
eq "$(cat "$CC/.pinned-account")" "$(cat "$CC/.account")" && ok "pin and state never disagree after --repin" || bad "pin/state divergence"

echo "== swap-fleet may never reach a pin override on its own =="
# precise: no swap-account INVOCATION may carry --repin. (The string --repin does appear in
# swap-fleet — inside the skip message telling a human how to override deliberately. That is the
# point: the override is documented but never automated.)
check "no swap-account invocation passes --repin" \
  bash -c "! grep -E '\\$A/bin/swap-account\" .*--repin' '$REPO/bin/swap-fleet'"
check "the CLIENT invocation is the only one passing --force (projects/main must not)" \
  bash -c "grep -c -- '--force 2>&1' '$REPO/bin/swap-fleet' | grep -qx 1"
check "swap-fleet filters pinned workspaces out of the roster" grep -q 'is PINNED to' "$REPO/bin/swap-fleet"
check "a pinned skip is logged (log(), not echo — the tail's stdout goes to the journal)" \
  bash -c "grep -q 'log \"C: ⊘ skipped: \$_c is PINNED' '$REPO/bin/swap-fleet'"
check "a pinned skip is worded as expected policy, not an error" \
  bash -c "grep -q 'not a failure' '$REPO/bin/swap-fleet'"
check "swap-fleet maps exit 3 to a clean skip line" bash -c "grep -q 'crc\" = 3' '$REPO/bin/swap-fleet'"
check_not "a pinned skip does NOT page main"  bash -c "sed -n '/⊘ skipped:/,+2p' '$REPO/bin/swap-fleet' | grep -q 'send main'"

echo "== already-on-target is a no-op unless --force (the repair path) =="
reset_client
printf 'acct-a\n' > "$CC/.account"
out=$("$SA" acme acct-a 2>&1)
check "says already on"                    bash -c "printf '%s' \"\$1\" | grep -q 'already on'" _ "$out"
check_not "no bounce"                      test -s "$TMP/agentctl.calls"
rm -f "$TMP/agentctl.calls"
printf '{"claudeAiOauth":{"refreshToken":"sk-CLIENT-OWN-A-RESTASHED"}}\n' > "$CC/.credentials.acct-a.json"
"$SA" acme acct-a --force >/dev/null 2>&1
check "--force re-deploys the stash"       grep -q 'sk-CLIENT-OWN-A-RESTASHED' "$CC/.credentials.json"

echo "== a PROJECT workspace is unaffected by all of this =="
printf 'ROOT="$HOME/work/rig"\nACCOUNT="acct-a"\nSESSION_ID="bbbb"\n' > "$A/projects/rig.env"
mkdir -p "$TMP/work/rig"
ln -sfn "$TMP/.claude/projects" "$A/accounts/acct-a/projects" 2>/dev/null
ln -sfn "$TMP/.claude/projects" "$A/accounts/acct-b/projects" 2>/dev/null
mkdir -p "$TMP/.claude/projects"
before_tok=$(live_token)
out=$("$SA" rig acct-b 2>&1); rc=$?
check "project swap still succeeds"        test "$rc" -eq 0
check "project path took the shared-store route" bash -c "printf '%s' \"\$1\" | grep -q 'shared session store'" _ "$out"
eq "$(grep -oP '^ACCOUNT="\K[^"]+' "$A/projects/rig.env")" "acct-b" && ok "project descriptor updated" || bad "project descriptor"
# meaningful, not vacuous: the client's live credential must be byte-identical after a PROJECT swap
eq "$(live_token)" "$before_tok" && ok "a project swap leaves the client's credential untouched" || bad "project swap touched a confined workspace credential"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
