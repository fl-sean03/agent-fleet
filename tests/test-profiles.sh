#!/usr/bin/env bash
# test-profiles.sh — the workspace-profile config model (2026-07-15):
#   ~/.agents is canonical; each workspace's config dir is COMPOSED from base < profile < descriptor;
#   credentials NEVER live on that path (CLAUDE_SECURESTORAGE_CONFIG_DIR keeps them in the account);
#   a confined client composes into its ISOLATED cfg and the wall stays intact;
#   swap verification identifies a workspace's account by the credential store its process reads.
# Runs in a throwaway HOME. No agents started, no network, no live credentials.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT="$(cd "$HERE/.." && pwd)"; BIN="$KIT/bin"; REPO="$KIT"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-profiles.XXXXXX")
trap 'rm -rf "$TMP"; [ -n "${FAKEPID:-}" ] && kill "$FAKEPID" 2>/dev/null' EXIT

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  - $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL- $1"; }
eq(){ [ "$1" = "$2" ] || { echo "       got: '$1'  want: '$2'"; return 1; }; }
check(){ local d="$1"; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }
check_not(){ local d="$1"; shift; if "$@"; then bad "$d"; else ok "$d"; fi; }

# --- sandboxed fleet ---------------------------------------------------------------------------
export HOME="$TMP"
A="$TMP/.agents"; mkdir -p "$A"/{projects,accounts/acct-a,accounts/acct-b,confined-cfg/exampleco,skills} "$TMP/.claude/projects"
ln -sfn "$REPO/profiles" "$A/profiles"
for s in agent-workspaces investigation-tree loop-engineering host-access-skill brandkit; do
  mkdir -p "$A/skills/$s"; echo "# $s" > "$A/skills/$s/SKILL.md"
done
for acct in acct-a acct-b; do
  printf '{"claudeAiOauth":{"refreshToken":"sk-%s"}}\n' "$acct" > "$A/accounts/$acct/.credentials.json"
  printf '{"oauthAccount":{"emailAddress":"%s@example.com"},"projects":{}}\n' "$acct" > "$A/accounts/$acct/.claude.json"
  ln -sfn "$TMP/.claude/projects" "$A/accounts/$acct/projects"
done
echo acct-a > "$A/accounts/.active"
printf '{"oauthAccount":{"emailAddress":"exampleco-own@example.com"}}\n' > "$A/confined-cfg/exampleco/.claude.json"
# a confined workspace's LIVE hand-tuned settings — compose must not destroy these (a real fleet carries exactly these)
printf '{"env":{"DISABLE_AUTOUPDATER":"1"},"model":"claude-fable-5","effortLevel":"max"}\n' > "$A/confined-cfg/exampleco/settings.json"
printf '{"claudeAiOauth":{"refreshToken":"sk-EXAMPLECO-OWN"}}\n' > "$A/confined-cfg/exampleco/.credentials.json"
mkdir -p "$A/confined-cfg/exampleco/projects/-work"; echo '{}' > "$A/confined-cfg/exampleco/projects/-work/sess.jsonl"

mkdesc(){ printf '%s\n' "$2" > "$A/projects/$1.env"; }
mkdesc rig  'ROOT="$HOME/work/rig"
PROFILE="lab"
SESSION_ID="11111111-1111-1111-1111-111111111111"'
mkdesc exampleco 'ROOT="$HOME/clients/exampleco"
KIND="confined"
PROFILE="client"
SESSION_ID="22222222-2222-2222-2222-222222222222"'
mkdesc legacy 'ROOT="$HOME/work/legacy"
SESSION_ID="33333333-3333-3333-3333-333333333333"'
mkdesc ovr 'ROOT="$HOME/work/ovr"
PROFILE="lab"
MODEL="claude-sonnet-5"
SKILLS="loop-engineering"'
AP="$BIN/agent-profile"

echo "== profiles are discoverable =="
for p in base client lab; do check "$p profile listed" bash -c "'$AP' list | grep -qx $p"; done

echo "== policy resolution: descriptor > profile > base =="
eq "$("$AP" policy rig KIND)"     "project"          && ok "lab → KIND=project (profile)"        || bad "rig KIND"
eq "$("$AP" policy exampleco KIND)"    "confined"         && ok "client profile → KIND=confined (auto-confined)" || bad "exampleco KIND"
eq "$("$AP" policy exampleco SANDBOX)" "bwrap"            && ok "client → SANDBOX=bwrap (profile)"    || bad "exampleco SANDBOX"
eq "$("$AP" policy rig SANDBOX)"  ""                 && ok "lab → SANDBOX empty"                 || bad "rig SANDBOX"
eq "$("$AP" policy rig MODEL)"    "claude-opus-4-8"  && ok "MODEL falls through to base"         || bad "rig MODEL"
eq "$("$AP" policy ovr MODEL)"    "claude-sonnet-5"  && ok "descriptor MODEL beats profile"      || bad "ovr MODEL"
eq "$("$AP" policy ovr SKILLS)"   "loop-engineering" && ok "descriptor SKILLS beats profile all" || bad "ovr SKILLS"
eq "$("$AP" policy legacy KIND)"  "project"          && ok "no PROFILE → base policy"            || bad "legacy KIND"

echo "== compose: project workspace =="
cr=$("$AP" compose rig)
eq "$cr" "$A/cfg/rig" && ok "composed dir is ~/.agents/cfg/<ws>" || bad "cfg path: $cr"
check "AGENTS.md written"                     test -s "$cr/AGENTS.md"
check "carries the BASE layer"                grep -q "Checkpoint to disk" "$cr/AGENTS.md"
check "carries the PROFILE layer"             grep -q "Lab / personal work" "$cr/AGENTS.md"
check "CLAUDE.md mirrors AGENTS.md"           test -L "$cr/CLAUDE.md"
check "settings.json is valid JSON"           bash -c "python3 -m json.tool '$cr/settings.json' >/dev/null"
# THE UNATTENDED-OPERATION CONTRACT. A composed config dir REPLACES the account dir's settings, so
# anything the fleet relies on must survive composition. Dropping these stranded a live agent on the
# bypass-permissions modal — input blocked, nobody there to answer (first live migration, 2026-07-15).
check "composed settings keep skipDangerousModePermissionPrompt" \
  bash -c "python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get(\"skipDangerousModePermissionPrompt\") is True else 1)' '$cr/settings.json'"
check "composed settings keep bypassPermissions default" \
  bash -c "python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get(\"permissions\",{}).get(\"defaultMode\")==\"bypassPermissions\" else 1)' '$cr/settings.json'"
check "composed settings keep the transcript-retention floor" \
  bash -c "python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get(\"cleanupPeriodDays\",0) >= 3650 else 1)' '$cr/settings.json'"
check_not "composed settings do NOT pin a model (descriptor MODEL= owns that)" \
  bash -c "python3 -c 'import json,sys; sys.exit(0 if \"model\" in json.load(open(sys.argv[1])) else 1)' '$cr/settings.json'"
check "session store → the ONE shared store"  bash -c "[ \"\$(readlink -f '$cr/projects')\" = \"\$(readlink -f '$TMP/.claude/projects')\" ]"
check "lab gets all 5 skills"                 test "$(ls "$cr/skills" | wc -l)" -eq 5
check ".claude.json seeded from the account"  grep -q 'acct-a@example.com' "$cr/.claude.json"
eq "$(cat "$cr/.account")" "acct-a" && ok "composed dir records its account" || bad ".account marker"

echo "== THE INVARIANT: credentials never live on the composed path =="
# Claude Code rename-writes .credentials.json (uf(): write tmp → rename onto target), which REPLACES
# a symlink with a private regular file. A credential placed here would fork the rotating refresh
# token per workspace and strand the account's copy. Credentials belong to the account, full stop.
check_not "NO .credentials.json in the composed cfg dir" test -e "$cr/.credentials.json"
eq "$("$AP" credstore rig)" "$A/accounts/acct-a" && ok "credstore(project) = the ACCOUNT dir" || bad "credstore rig"
eq "$("$AP" credstore rig --account acct-b)" "$A/accounts/acct-b" && ok "credstore follows --account" || bad "credstore acct-b"
eq "$("$AP" credstore exampleco)" "$A/confined-cfg/exampleco" && ok "credstore(confined) = its ISOLATED cfg (never an account)" || bad "credstore exampleco"

echo "== rotation-readiness: an account change moves creds, not the config dir =="
cr2=$("$AP" compose rig --account acct-b)
eq "$cr2" "$cr" && ok "config dir path is STABLE across an account change" || bad "cfg path moved: $cr2"
check ".claude.json re-seeded for the new account" grep -q 'acct-b@example.com' "$cr/.claude.json"
eq "$(cat "$cr/.account")" "acct-b" && ok ".account marker follows the swap" || bad ".account after swap"
check_not "still no credential file in the cfg dir" test -e "$cr/.credentials.json"
check "acct-a credential untouched by the swap"  grep -q 'sk-acct-a' "$A/accounts/acct-a/.credentials.json"
check "acct-b credential untouched by the swap"  grep -q 'sk-acct-b' "$A/accounts/acct-b/.credentials.json"

echo "== compose: confined workspace — the wall holds =="
ca=$("$AP" compose exampleco)
eq "$ca" "$A/confined-cfg/exampleco" && ok "confined ws composes INTO its isolated cfg" || bad "client cfg path: $ca"
check "confined instructions present"        grep -q "confined workspace" "$ca/AGENTS.md"
check "CLAUDE.md is a real FILE (copy)"    bash -c "test -f '$ca/CLAUDE.md' && ! test -L '$ca/CLAUDE.md'"
check "skills are COPIED, not symlinked"   bash -c "test -d '$ca/skills/investigation-tree' && ! test -L '$ca/skills/investigation-tree'"
check "copied skill has real content"      test -s "$ca/skills/investigation-tree/SKILL.md"
check "confined ws keeps its OWN credential"    grep -q 'sk-EXAMPLECO-OWN' "$ca/.credentials.json"
# pre-existing settings are LIVE STATE, not ours to delete: they become the lowest layer.
jget(){ python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."): d = d.get(k, {}) if isinstance(d, dict) else {}
print(d if not isinstance(d,dict) or d else "")' "$1" "$2"; }
eq "$(jget "$ca/settings.json" env.DISABLE_AUTOUPDATER)" "1"   && ok "confined ws's own env setting survives compose"   || bad "clobbered DISABLE_AUTOUPDATER"
eq "$(jget "$ca/settings.json" model)" "claude-fable-5"        && ok "confined ws's own model pin survives compose"     || bad "clobbered client model pin"
eq "$(jget "$ca/settings.json" effortLevel)" "max"             && ok "unknown pre-existing keys survive compose"         || bad "clobbered effortLevel"
eq "$(jget "$ca/settings.json" skipDangerousModePermissionPrompt)" "True" && ok "base contract still layers ON TOP" || bad "base layer lost for client"
check "pre-profile snapshot kept for reversibility" test -f "$ca/.settings.pre-profile.json"
check "confined session store NOT redirected to the shared store" bash -c "! test -L '$ca/projects'"
check "confined private session survives compose" test -f "$ca/projects/-work/sess.jsonl"
# the client profile must not hand host-specific skills into someone else's sandbox
check_not "no fleet-ops skill in the confined sandbox" test -e "$ca/skills/fleet-ops-skill"
check_not "no host-access skill in the confined sandbox"      test -e "$ca/skills/hpc-access"
eq "$(ls "$ca/skills" | wc -l)" "2" && ok "confined skill set is exactly the narrow 2" || bad "client skill count"

echo "== composition is a pure function of the layers (a project cfg dir is fully MANAGED) =="
# Re-composing must not seed from its own previous OUTPUT, or a key removed from a profile would be
# resurrected from the last run forever.
python3 - "$cr/settings.json" <<'PY'
import json,sys; d=json.load(open(sys.argv[1])); d["strayKey"]="left over from a previous compose"
json.dump(d, open(sys.argv[1],"w"))
PY
"$AP" compose rig >/dev/null
check_not "a project cfg dir is never seeded from its own output" grep -q strayKey "$cr/settings.json"
check_not "no pre-profile snapshot for a managed project dir"     test -e "$cr/.settings.pre-profile.json"

echo "== skills resolve most-specific-wins (a narrow layer NARROWS) =="
co=$("$AP" compose ovr)
eq "$(ls "$co/skills")" "loop-engineering" && ok "descriptor SKILLS narrows base 'all' to 1" || bad "ovr skills: $(ls "$co/skills")"

echo "== unknown profile fails closed =="
mkdesc bogus 'ROOT="$HOME/work/bogus"
PROFILE="nonesuch"'
check_not "compose rejects an unknown profile" bash -c "'$AP' compose bogus >/dev/null 2>&1"

echo "== swap verification identifies the account by the CREDENTIAL STORE =="
# Real processes, not mocks: verify_ws reads /proc/<pid>/environ, so we stand up a fake `claude`
# carrying the session id in argv and assert the rule both ways.
SWAP_TEST=1 A="$A" ACCTS="$A/accounts" . "$BIN/swap-fleet" x >/dev/null 2>&1
# The fake must be a real EXECUTABLE named `claude`, not a bash script: verify_ws checks
# basename(argv[0]), and a script's argv[0] is the interpreter (`bash`) — which the real code would
# correctly skip. Copy the python binary and give it the session id in argv so pgrep -f finds it.
mkdir -p "$TMP/fakebin"; cp -L "$(command -v python3)" "$TMP/fakebin/claude"
fake_claude(){ env -i PATH=/usr/bin:/bin HOME="$TMP" "$@" & FAKEPID=$!; sleep 0.5; }
export VERIFY_TRIES=1
# profile workspace: config dir is cfg/rig, credentials point at acct-b
fake_claude CLAUDE_CONFIG_DIR="$A/cfg/rig" CLAUDE_SECURESTORAGE_CONFIG_DIR="$A/accounts/acct-b" \
    "$TMP/fakebin/claude" -c 'import time;time.sleep(60)' 11111111-1111-1111-1111-111111111111
TCFG="$A/accounts/acct-b"; check "profile ws on acct-b VERIFIES against acct-b" verify_ws rig
TCFG="$A/accounts/acct-a"; check_not "profile ws on acct-b is NOT verified as acct-a" verify_ws rig
# the trap this rule exists for: the config dir alone would have matched neither/wrongly
TCFG="$A/cfg/rig";         check_not "composed config dir is not mistaken for an account" verify_ws rig
kill "$FAKEPID" 2>/dev/null; wait "$FAKEPID" 2>/dev/null; FAKEPID=""
# legacy workspace: no securestorage var → falls back to CLAUDE_CONFIG_DIR (= the account dir)
fake_claude CLAUDE_CONFIG_DIR="$A/accounts/acct-a" \
    "$TMP/fakebin/claude" -c 'import time;time.sleep(60)' 33333333-3333-3333-3333-333333333333
TCFG="$A/accounts/acct-a"; check "legacy ws still verifies via CLAUDE_CONFIG_DIR (no regression)" verify_ws legacy
TCFG="$A/accounts/acct-b"; check_not "legacy ws on acct-a is NOT verified as acct-b" verify_ws legacy
kill "$FAKEPID" 2>/dev/null; wait "$FAKEPID" 2>/dev/null; FAKEPID=""

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
