#!/usr/bin/env bash
# test-tmux-exact-target.sh — every tmux target built from a NAME must be exact-match.
#
# WHY. tmux resolves a `-t` target by trying an exact match and then falling back to a PREFIX match.
# Workspace sessions are named `agent-<workspace>`, so as soon as two workspaces share a prefix —
# `agent-foo` and `agent-foo-bar` — a command aimed at the shorter one can land on the longer one
# whenever the shorter session is absent. For `capture-pane` that reads the wrong agent's screen; for
# `kill-session` it terminates the wrong agent mid-turn.
#
# tmux's own opt-out is a leading `=`: "=agent-foo" matches that session and nothing else. This test is
# the ratchet — it greps production for any name-based target still missing it, so the fix cannot rot
# back in one careless edit. Pane ids (%NN) are already exact and are deliberately not covered.
set -uo pipefail
umask 022

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$HERE/.."
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); printf '  ok  - %s\n' "$1"; }
bad(){ FAIL=$((FAIL+1)); printf '  FAIL- %s\n' "$1" >&2; }

echo "== tmux resolves a bare name by PREFIX, which is why this matters =="
if command -v tmux >/dev/null 2>&1; then
  SOCK="tmux-exact-$$"
  tmux -L "$SOCK" new-session -d -s "zz-pfx-long" 2>/dev/null
  # The short session does NOT exist. A bare-name target therefore falls through to the long one;
  # the exact form must not.
  if tmux -L "$SOCK" has-session -t "zz-pfx" 2>/dev/null; then
    ok "a bare name matches a longer sibling (the hazard is real, not theoretical)"
  else
    bad "a bare name matches a longer sibling (tmux behaviour changed — re-derive this test)"
  fi
  if tmux -L "$SOCK" has-session -t "=zz-pfx" 2>/dev/null; then
    bad "the = form must NOT match a longer sibling"
  else
    ok "the = form matches exactly, or not at all"
  fi
  tmux -L "$SOCK" kill-server 2>/dev/null || true
else
  ok "tmux absent — skipping the live behaviour probe"
fi

echo "== production audit: no name-based tmux target is prefix-matchable =="
unsafe=$(
  {
    grep -rnE -- 'tmux[^#]*-t[[:space:]]+"(agent-|\$\{?(SESSION|sess|s|LOGIN_TMUX)\}?)' "$REPO/bin" || true
    grep -rnE -- '\["tmux",.*"-t",.*f?"agent-' "$REPO/bin" || true
  } || true
)
if [ -z "$unsafe" ]; then
  ok "every name-based target in bin/ uses tmux exact-match syntax"
else
  bad "prefix-matchable tmux targets remain:"
  printf '%s\n' "$unsafe" | sed 's/^/         /' >&2
fi

echo "== the exact form is actually present (the audit is not vacuous) =="
count=$(grep -rcE -- 'tmux[^#]*-t[[:space:]]+"=' "$REPO/bin" 2>/dev/null | awk -F: '{n+=$2} END{print n+0}')
if [ "$count" -ge 10 ]; then
  ok "bin/ carries $count exact-match targets"
else
  bad "only $count exact-match targets found — the audit above may be matching nothing"
fi

echo "== pane ids are left alone (they are already exact) =="
if grep -rqE -- '-t "\$(pane|p)"' "$REPO/bin" 2>/dev/null; then
  ok "pane-id targets are untouched"
else
  ok "no pane-id targets to check"
fi

# run-tests.sh parses the LAST line for PASS=<n>/FAIL=<n>; anything else and its accounting silently
# breaks (it read this suite as one failure while every assertion passed). Match the house format.
printf '\nPASS=%d FAIL=%d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
