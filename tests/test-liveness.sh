#!/usr/bin/env bash
# test-liveness.sh — the guards added after 2026-07-15/16, when three failures went unnoticed:
#   • an agent was OOM-killed; its tmux session stayed up so every check read it healthy
#     for ~19h (idle-down even kept it — its liveness proxy is transcript mtime).
#   • a remote agent died on an unanswered trust modal; its pane fell back to a SHELL, and
#     fleet-msg kept pasting messages + Enter into it — so message text EXECUTED as bash commands.
#   • a compute campaign filled the disk with its own scratch and starved, unremarked.
#
# Uses a PRIVATE tmux server (-L) via a PATH stub, so the panes and processes are real (the whole
# point — a mocked pane cannot prove a process-tree walk) while the operator's own tmux is untouched.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SOCK="sgtest-$$"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-liveness.XXXXXX")
cleanup(){ /usr/bin/tmux -L "$SOCK" kill-server 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  - $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL- $1"; }
check(){ local d="$1"; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }
check_not(){ local d="$1"; shift; if "$@"; then bad "$d"; else ok "$d"; fi; }

export HOME="$TMP"
A="$TMP/.agents"; mkdir -p "$A/projects" "$TMP/bin" "$TMP/.local/bin" "$TMP/.claude/projects"
# tmux stub → private server. session-guard/fleet-msg call bare `tmux`, so this isolates them.
printf '#!/usr/bin/env bash\nexec /usr/bin/tmux -L %s "$@"\n' "$SOCK" > "$TMP/bin/tmux"
# agentctl stub → record alerts instead of sending them
printf '#!/usr/bin/env bash\n[ "${1:-}" = send ] && { shift 2; printf "%%s\\n" "$*" >> "$HOME/alerts.txt"; }\nexit 0\n' > "$TMP/.local/bin/agentctl"
chmod +x "$TMP/bin/tmux" "$TMP/.local/bin/agentctl"
# session-guard's alerts ride fl_send → $A/bin/fleet-msg (2026-07-17 envelope consolidation) — record
# them the same way. The delivery tests below still call the REAL fleet-msg by absolute path ($FM).
mkdir -p "$A/bin"
cat > "$A/bin/fleet-msg" <<'EOF'
#!/usr/bin/env bash
while [ $# -gt 0 ]; do case "$1" in send) shift;; --to|--from) shift 2;; *) break;; esac; done
printf '%s\n' "$*" >> "$HOME/alerts.txt"
exit 0
EOF
chmod +x "$A/bin/fleet-msg"
export PATH="$TMP/bin:$PATH"
# a real executable named `claude` (a bash script would appear as `bash` in /proc/<pid>/comm — the
# exact reason a naive check reports a healthy agent as dead)
cp -L "$(command -v python3)" "$TMP/bin/claude"

mkws(){ printf 'ROOT="%s"\nAGENTS="%s"\nSESSION_ID="sid-%s"\n' "$TMP/work/$1" "${2:-claude}" "$1" > "$A/projects/$1.env"; mkdir -p "$TMP/work/$1"; }
run_guard(){ rm -f "$TMP/alerts.txt"; bash "$REPO/bin/session-guard" >/dev/null 2>&1; }
alerts(){ cat "$TMP/alerts.txt" 2>/dev/null; }

# --- a LIVE agent (bash launcher → claude child, exactly like run-claude) ---
mkws live
tmux new-session -d -s agent-live -n main -c "$TMP" "bash -c 'exec bash'" 2>/dev/null
tmux split-window -t agent-live:main -c "$TMP" "exec bash -c '\"$TMP/bin/claude\" -c \"import time;time.sleep(300)\" sid-live'" 2>/dev/null
# --- a DEAD agent (pane fell back to a shell — the shape both incidents left behind) ---
mkws dead
tmux new-session -d -s agent-dead -n main -c "$TMP" "bash -c 'exec bash'" 2>/dev/null
tmux split-window -t agent-dead:main -c "$TMP" "exec bash" 2>/dev/null
sleep 1

echo "== liveness: a live agent is never flagged =="
run_guard
check_not "no DEAD alert for a healthy agent"      bash -c "alerts(){ cat '$TMP/alerts.txt' 2>/dev/null; }; alerts | grep -q 'DEAD AGENT.*live'"
check_not "no liveness log line for a healthy agent" grep -q "liveness: live " "$A/session-guard.log"

echo "== liveness: a dead agent is caught — but only after a DEBOUNCE =="
# first sighting must NOT page: an agent mid-bash-tool-call can momentarily show no agent process,
# and a guard that cries wolf gets ignored.
check "first sighting logs, does not alert"  grep -q "liveness: dead (claude) has no agent process" "$A/session-guard.log"
check_not "no alert on the first sighting"   bash -c "grep -q 'DEAD AGENT' '$TMP/alerts.txt' 2>/dev/null"
run_guard
check "second consecutive sighting alerts"   bash -c "grep -q 'DEAD AGENT' '$TMP/alerts.txt'"
check "the alert names the workspace"        bash -c "grep -q \"workspace 'dead'\" '$TMP/alerts.txt'"
check "the alert gives the revival command"  bash -c "grep -q 'agentctl stop dead && agentctl up dead' '$TMP/alerts.txt'"
check "logged as well as alerted"            grep -q "DEAD AGENT: dead" "$A/session-guard.log"

echo "== liveness: recovery clears the state (no stale alerts after a fix) =="
tmux split-window -t agent-dead:main -c "$TMP" "exec bash -c '\"$TMP/bin/claude\" -c \"import time;time.sleep(300)\" sid-dead'" 2>/dev/null
sleep 1; run_guard
check_not "a revived agent stops alerting"   bash -c "grep -q 'DEAD AGENT' '$TMP/alerts.txt' 2>/dev/null"
check_not "its debounce marker is cleared"   test -f "$A/.session-guard-liveness/dead.claude"

echo "== a REMOTE workspace's agent is its ssh, not a local claude =="
# claude-remote has no local claude by design; matching on 'claude' would page every 15 minutes.
mkws remote claude-remote
tmux new-session -d -s agent-remote -n main -c "$TMP" "bash -c 'exec bash'" 2>/dev/null
cp -L "$(command -v python3)" "$TMP/bin/ssh"
tmux split-window -t agent-remote:main -c "$TMP" "exec bash -c '\"$TMP/bin/ssh\" -c \"import time;time.sleep(300)\" sid-remote'" 2>/dev/null
sleep 1; run_guard; run_guard
check_not "an ssh-backed remote agent is NOT flagged dead" bash -c "grep -q 'DEAD AGENT.*remote' '$TMP/alerts.txt' 2>/dev/null"

echo "== memory: warn BEFORE the kernel kills =="
# one run only: alert_main throttles per key, so a second run inside the window is correctly silent
rm -f "$A/.session-guard-alert."* "$TMP/alerts.txt"
MEM_WARN_GB=0.001 bash "$REPO/bin/session-guard" >/dev/null 2>&1
check "a memory hog is alerted on"        bash -c "grep -q 'MEMORY' '$TMP/alerts.txt'"
check "the alert names the workspace"     bash -c "grep -qE \"workspace '(live|dead|remote)'\" '$TMP/alerts.txt'"
rm -f "$A/.session-guard-alert."*
run_guard
check_not "a normal-sized agent is NOT flagged" bash -c "grep -q 'MEMORY' '$TMP/alerts.txt' 2>/dev/null"

echo "== disk: warn before agents start failing to write =="
rm -f "$A/.session-guard-alert."*
rm -f "$TMP/alerts.txt"; DISK_MIN_GB=999999 bash "$REPO/bin/session-guard" >/dev/null 2>&1
check "a low disk floor alerts"           bash -c "grep -q 'DISK' '$TMP/alerts.txt'"
check "it explains the starvation shape"  bash -c "grep -q 'starved on its own next write' '$TMP/alerts.txt'"
rm -f "$A/.session-guard-alert."*
rm -f "$TMP/alerts.txt"; DISK_MIN_GB=0 bash "$REPO/bin/session-guard" >/dev/null 2>&1
check_not "ample disk does NOT alert"     bash -c "grep -q 'DISK' '$TMP/alerts.txt' 2>/dev/null"

echo "== fleet-msg NEVER pastes into a dead agent's shell (the accidental-RCE incident) =="
# Delivery is a bracketed paste + Enter. Into an agent's input box that is a message; into the SHELL
# a dead agent leaves behind, bash EXECUTES it. the dead pane still carried the wreckage:
#   msg-20260715T005831Z-3666]: command not found
#   bash: syntax error near unexpected token `main'
# A message body is arbitrary text from another agent, so this was remote code execution by accident.
FM="$REPO/bin/fleet-msg"
alive_of(){ REPO="$REPO" python3 - "$1" <<'PYX'
import os, sys
from importlib.machinery import SourceFileLoader
fm = SourceFileLoader("fm", os.environ["REPO"] + "/bin/fleet-msg").load_module()
p = fm._claude_pane(sys.argv[1])
print("alive" if (p and fm._agent_alive(p)) else "dead")
PYX
}
check "a live agent is reachable"            bash -c "[ \"$(alive_of live)\" = alive ]"
check "a DEAD agent is not reachable"        bash -c "[ \"$(alive_of dead2)\" = dead ]"
check "a confined/remote-style agent counts as alive" bash -c "[ \"$(alive_of remote)\" = alive ]"

# the real thing: a message with shell metacharacters, delivered to a dead agent, must not execute
mkws dead2
tmux new-session -d -s agent-dead2 -n main -c "$TMP" "bash -c 'exec bash'" 2>/dev/null
tmux split-window -t agent-dead2:main -c "$TMP" "exec bash" 2>/dev/null
sleep 1
WS_NAME=tester python3 "$FM" send --to dead2 --from tester 'probe $(touch "'"$TMP"'/PWNED") `id`' >/dev/null 2>&1
sleep 2
check_not "the injected command did NOT execute"  test -f "$TMP/PWNED"
check "the message was QUEUED instead of executed" test -s "$A/messages/pending/dead2.jsonl"

echo "== flush must not empty the whole queue into a shell =="
WS_NAME=tester python3 "$FM" send --to dead2 --from tester 'second $(touch "'"$TMP"'/PWNED2")' >/dev/null 2>&1
out=$(python3 "$FM" flush dead2 2>&1)
check_not "flush did NOT execute the queue"   test -f "$TMP/PWNED2"
check "flush says the agent is dead"          bash -c "printf '%s' \"$out\" | grep -q 'AGENT is dead'"
check "flush LEFT the messages queued"        test -s "$A/messages/pending/dead2.jsonl"

echo "== echo-guard: kick never submits the agent's OWN screen output back into it (live incident 2026-07-17) =="
# The incident: a kick fired a fragment of an agent's own conversation content (a journal title it
# had been discussing), stranded into the composer by a stray selection-paste and flushed back into
# the agent as operator input. Discriminator: composer text that appears VERBATIM in the agent's own
# output above the composer = echo (clear, never submit); a genuine swallowed directive appears
# nowhere in the output and must still flush.
mkws echoer
tmux new-session -d -s agent-echoer -n main -c "$TMP" "exec bash" 2>/dev/null
tmux split-window -t agent-echoer:main -c "$TMP" \
  "printf 'The reviewer cited Journal of Example Research for the preconditioner\n❯ Journal of Example Research\n'; exec \"$TMP/bin/claude\" -c 'import time;time.sleep(300)' sid-echoer" 2>/dev/null
mkws genuine
tmux new-session -d -s agent-genuine -n main -c "$TMP" "exec bash" 2>/dev/null
tmux split-window -t agent-genuine:main -c "$TMP" \
  "printf 'agent: say the word and it goes\n❯ resume the queue\n'; exec \"$TMP/bin/claude\" -c 'import time;time.sleep(300)' sid-genuine" 2>/dev/null
sleep 1
echo_of(){ REPO="$REPO" python3 - "$1" "$2" <<'PYX'
import os, sys
from importlib.machinery import SourceFileLoader
fm = SourceFileLoader("fm", os.environ["REPO"] + "/bin/fleet-msg").load_module()
p = fm._claude_pane(sys.argv[1])
print("echo" if (p and fm._is_own_echo(p, sys.argv[2])) else "no-echo")
PYX
}
check "own-output text reads as echo"              bash -c "[ \"$(echo_of echoer 'Journal of Example Research')\" = echo ]"
check "a genuine swallowed directive is NOT echo"  bash -c "[ \"$(echo_of genuine 'resume the queue')\" = no-echo ]"
kout=$(python3 "$FM" kick echoer --as test 2>&1)
check "kick CLEARS the echo instead of submitting" bash -c "printf '%s' \"$kout\" | grep -q 'echo-guard:.*cleared screen-echo'"
check_not "the echo was never submitted"           bash -c "printf '%s' \"$kout\" | grep -q 'submitted stuck input'"
dout=$(python3 "$FM" kick genuine --as test --dry 2>&1)
check "the genuine directive would still be submitted" bash -c "printf '%s' \"$dout\" | grep -q 'WOULD submit stuck input'"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
