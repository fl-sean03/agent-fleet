#!/usr/bin/env bash
# test-profiles.sh — the workspace-profile config model: ~/.agents is canonical, ~/.claude is a
# projection, and each workspace's config is COMPOSED (base < profile < descriptor) into its own
# config dir. Runs in a throwaway HOME; no agents started, no network.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT="$(cd "$HERE/.." && pwd)"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-profiles.XXXXXX")
trap 'rm -rf "$TMP"' EXIT
export HOME="$TMP"

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  - $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL- $1"; }
eq(){ [ "$1" = "$2" ] || { echo "       got: '$1'  want: '$2'"; return 1; }; }
check(){ local d="$1"; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }

bash "$KIT/install.sh" >/dev/null 2>&1
AC="$KIT/bin/agentctl"; AP="$KIT/bin/agent-profile"
mkdir -p "$TMP/.agents/accounts/account-a"
echo '{"claudeAiOauth":{"accessToken":"x"}}' > "$TMP/.agents/accounts/account-a/.credentials.json"
echo '{}' > "$TMP/.agents/accounts/account-a/.claude.json"
echo account-a > "$TMP/.agents/accounts/.active"

echo "== canonical + projection =="
check "~/.agents/profiles exists (canonical)"      test -d "$TMP/.agents/profiles/base"
check "~/.agents/skills is the canonical location" test -e "$TMP/.agents/skills"
check "~/.claude/CLAUDE.md is a symlink"           test -L "$TMP/.claude/CLAUDE.md"
eq "$(readlink "$TMP/.claude/CLAUDE.md")" "$TMP/.agents/profiles/base/AGENTS.md" && ok "CLAUDE.md → base AGENTS.md (source of truth)" || bad "CLAUDE.md projection target"
check "~/.claude/skills mirrors canonical skills"  test "$(ls "$TMP/.claude/skills" | wc -l)" -gt 0

echo "== profiles list =="
check "base profile listed"   bash -c "'$AP' list | grep -qx base"
check "client profile listed" bash -c "'$AP' list | grep -qx client"
check "lab profile listed"    bash -c "'$AP' list | grep -qx lab"

echo "== creation adopts profile policy =="
bash "$AC" new acme --profile client >/dev/null 2>&1
bash "$AC" new rig  --profile lab --root "$TMP/work/rig" >/dev/null 2>&1
eq "$(grep -oP '^PROFILE="\K[^"]+' "$TMP/.agents/projects/acme.env")" "client" && ok "acme descriptor has PROFILE=client" || bad "acme PROFILE"
eq "$(grep -oP '^KIND="\K[^"]+' "$TMP/.agents/projects/acme.env")" "confined" && ok "client profile → KIND=confined (auto-confined)" || bad "acme KIND"
eq "$(grep -oP '^KIND="\K[^"]+' "$TMP/.agents/projects/rig.env")" "project" && ok "lab profile → KIND=project" || bad "rig KIND"
check "unknown profile rejected" bash -c "! '$AC' new bad --profile nonesuch >/dev/null 2>&1"

echo "== policy resolution (descriptor > profile > base) =="
eq "$(bash "$AP" policy acme SANDBOX)" "bwrap" && ok "client SANDBOX=bwrap (from profile)" || bad "acme SANDBOX"
eq "$(bash "$AP" policy rig SANDBOX)" "" && ok "lab SANDBOX empty (base)" || bad "rig SANDBOX"
eq "$(bash "$AP" policy acme MODEL)" "claude-opus-4-8" && ok "MODEL resolves" || bad "MODEL"
# descriptor override wins over profile
printf 'PROFILE="lab"\nROOT="%s/x"\nMODEL="claude-sonnet-5"\nSKILLS="loop-engineering"\n' "$TMP" > "$TMP/.agents/projects/ovr.env"
eq "$(bash "$AP" policy ovr MODEL)" "claude-sonnet-5" && ok "descriptor MODEL overrides profile" || bad "override MODEL"
eq "$(bash "$AP" policy ovr SKILLS)" "loop-engineering" && ok "descriptor SKILLS overrides profile 'all'" || bad "override SKILLS"

echo "== composition: two profiles get genuinely different config =="
ca=$(bash "$AP" compose acme); cr=$(bash "$AP" compose rig)
check "composed config dirs exist"  test -d "$ca" -a -d "$cr"
na=$(ls "$ca/skills" | wc -l); nr=$(ls "$cr/skills" | wc -l)
check "client config has the NARROW skill set (3)" test "$na" -eq 3
check "lab config has MORE skills than client"     test "$nr" -gt "$na"
check "client AGENTS.md carries confined instructions" grep -q "CONFINED workspace" "$ca/AGENTS.md"
check "lab AGENTS.md does NOT"                          bash -c "! grep -q 'CONFINED workspace' '$cr/AGENTS.md'"
check "composed AGENTS.md includes the BASE layer"     grep -q "Checkpoint to disk" "$ca/AGENTS.md"
check "CLAUDE.md mirror inside composed dir"            test -L "$ca/CLAUDE.md"
check "settings.json composed (valid JSON)"            bash -c "python3 -m json.tool '$ca/settings.json' >/dev/null"

echo "== credentials + session store symlinked from the account (rotation-ready) =="
eq "$(readlink "$ca/.credentials.json")" "$TMP/.agents/accounts/account-a/.credentials.json" && ok "creds symlinked from active account" || bad "creds symlink"
eq "$(cat "$ca/.account")" "account-a" && ok "composed dir records its account" || bad ".account marker"
check "session store symlinked (shared)" test -L "$ca/projects"
# recompose onto a different account → only the credential symlink retargets (config dir stable)
mkdir -p "$TMP/.agents/accounts/account-b"; echo '{}' > "$TMP/.agents/accounts/account-b/.credentials.json"; echo '{}' > "$TMP/.agents/accounts/account-b/.claude.json"
ca2=$(bash "$AP" compose acme --account account-b)
eq "$ca2" "$ca" && ok "config dir path is STABLE across account change" || bad "config dir path changed"
eq "$(readlink "$ca/.credentials.json")" "$TMP/.agents/accounts/account-b/.credentials.json" && ok "swap retargets creds, not the config dir" || bad "cred retarget"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
