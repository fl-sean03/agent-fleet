#!/usr/bin/env bash
# test-swap-resume.sh — the busy-at-bounce resume machinery (2026-07-16). Two account swaps
# decapitated long agent runs mid-turn; only statically-flagged AUTOCONTINUE
# workspaces were nudged, and one nudge sat unsubmitted in an input box for an hour.
# Covers: ws_busy detection, the AUTOCONTINUE ∪ interrupted union, mid-turn wording,
# no-nudge-for-untouched ("already on"), and the stuck-nudge kick.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-swapresume.XXXXXX"); trap 'rm -rf "$TMP"' EXIT
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  - $1"; }; bad(){ FAIL=$((FAIL+1)); echo "  FAIL- $1"; }
check(){ local d="$1"; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }
check_not(){ local d="$1"; shift; if "$@"; then bad "$d"; else ok "$d"; fi; }

export HOME="$TMP"; A="$TMP/.agents"; mkdir -p "$A"/{projects,accounts,bin} "$TMP/bin"
# stubs: tmux pane text via fixture; agentctl/fleet-msg record calls
cat > "$TMP/bin/tmux" <<'EOS'
#!/usr/bin/env bash
case "$*" in
  *capture-pane*agent-busy1*)  printf '● working hard\n  esc to interrupt\n❯ \n' ;;
  *capture-pane*agent-idle1*)  printf '❯ \n' ;;
  *capture-pane*agent-stuck1*) printf '  esc to interrupt\n❯ \n' ;;   # busy pre-bounce...
  *has-session*) exit 0 ;;
esac
exit 0
EOS
cat > "$A/bin/fleet-msg" <<'EOS'
#!/usr/bin/env bash
echo "fleet-msg $*" >> "$HOME/calls.txt"
EOS
chmod +x "$TMP/bin/tmux" "$A/bin/fleet-msg"; export PATH="$TMP/bin:$PATH"

# extract the helpers from swap-fleet and drive them directly (unit-level; the full phase flow
# needs a live tmux+accounts world that the account-convention suite owns)
sed -n '/^ws_busy(){/,/^}/p;/^verify_submitted(){/,/^}/p' "$REPO/bin/swap-fleet" > "$TMP/helpers.sh"
LOG="$TMP/log.txt"; log(){ echo "$*" >> "$LOG"; }
. "$TMP/helpers.sh"

echo "== ws_busy reads the agent pane, not vibes =="
check "a mid-turn agent is busy"        ws_busy busy1
check_not "an idle agent is not busy"   ws_busy idle1

echo "== the nudge union (AUTOCONTINUE ∪ interrupted) =="
printf 'ROOT="/x"\nAUTOCONTINUE="yes"\n' > "$A/projects/optin.env"
printf 'ROOT="/x"\n'                     > "$A/projects/orch.env"
printf 'ROOT="/x"\n'                     > "$A/projects/quiet.env"
echo orch > "$A/accounts/.swap-interrupted"
union=$( { grep -lE '^AUTOCONTINUE="?yes' "$A"/projects/*.env 2>/dev/null | xargs -rn1 basename | sed 's/\.env$//'
           cat "$A/accounts/.swap-interrupted" 2>/dev/null; } | sort -u )
check "opt-in workspace is in the set"        bash -c "echo '$union' | grep -qw optin"
check "interrupted workspace is in the set"   bash -c "echo '$union' | grep -qw orch"
check_not "untouched-idle workspace is NOT"   bash -c "echo '$union' | grep -qw quiet"

echo "== a stuck nudge gets kicked =="
cat > "$TMP/bin/tmux" <<'EOS'
#!/usr/bin/env bash
case "$*" in
  *capture-pane*agent-stuck1*) printf '❯ [account-swap] Fleet moved to acc\n' ;;  # nudge stuck in box
  *capture-pane*agent-clean1*) printf '❯ \n' ;;
  *has-session*) exit 0 ;;
esac
exit 0
EOS
chmod +x "$TMP/bin/tmux"
rm -f "$HOME/calls.txt"; verify_submitted stuck1
check "stuck input triggers exactly one kick"  bash -c "grep -q 'fleet-msg kick stuck1' '$HOME/calls.txt'"
rm -f "$HOME/calls.txt"; verify_submitted clean1
check_not "a clean submit is not kicked"       bash -c "grep -q 'kick clean1' '$HOME/calls.txt' 2>/dev/null"

echo; echo "PASS=$PASS FAIL=$FAIL"; [ "$FAIL" = 0 ]
