#!/usr/bin/env bash
# test-session-guard.sh — the ROUTING GUARD added after the 2026-07-15 main incident (a plugin
# rewrote settings.json with an env block pointing ANTHROPIC_BASE_URL at a local proxy; main ran on a
# non-Anthropic model for hours and the fleet had no signal). Also covers the shared-store preflight
# for composed cfg dirs.
# Fully sandboxed: fake HOME, stubbed tmux/pgrep/agentctl — no real panes read, no real alerts sent.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-sguard.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  - $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL- $1"; }
check(){ local d="$1"; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }
check_not(){ local d="$1"; shift; if "$@"; then bad "$d"; else ok "$d"; fi; }

export HOME="$TMP"
A="$TMP/.agents"
mkdir -p "$A"/{accounts/acct-a,cfg/ws1,confined-cfg/cli1} "$TMP/.claude/projects" "$TMP/bin" "$TMP/.local/bin"
ln -sfn "$TMP/.claude/projects" "$A/accounts/acct-a/projects"
ln -sfn "$TMP/.claude/projects" "$A/cfg/ws1/projects"

# stubs: no tmux server, no live procs, and agentctl RECORDS alerts instead of sending them
cat > "$TMP/bin/tmux" <<'EOF'
#!/usr/bin/env bash
exit 0        # no sessions: the scrollback/stuck scan is not what this file tests
EOF
cat > "$TMP/bin/pgrep" <<'EOF'
#!/usr/bin/env bash
exit 1        # no live claude procs — the proc scan reads /proc and cannot be faked honestly
EOF
cat > "$TMP/.local/bin/agentctl" <<'EOF'
#!/usr/bin/env bash
# agentctl send main "<msg>"  → record it
[ "${1:-}" = send ] && { shift 2; printf '%s\n' "$*" >> "$HOME/alerts.txt"; }
exit 0
EOF
# systemctl/journalctl stubs, fixture-driven — the failed-unit and campaign-stall sweeps must be
# tested deterministically, not against whatever the host happens to have failed today.
cat > "$TMP/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
scope=sys; [ "$1" = "--user" ] && { scope=usr; shift; }
case "$*" in
  *--state=failed*)          cat "$HOME/fixture-failed-$scope.txt" 2>/dev/null ;;
  *--state=running*)         cat "$HOME/fixture-running-$scope.txt" 2>/dev/null ;;
esac
exit 0
EOF
cat > "$TMP/bin/journalctl" <<'EOF'
#!/usr/bin/env bash
# emit one short-unix line whose age is controlled by the fixture (epoch seconds)
for a in "$@"; do case "$prev" in -u) unit="$a";; esac; prev="$a"; done
ts=$(cat "$HOME/fixture-journal-$unit.ts" 2>/dev/null)
[ -n "$ts" ] && echo "$ts $unit[1]: heartbeat"
exit 0
EOF
chmod +x "$TMP/bin/tmux" "$TMP/bin/pgrep" "$TMP/.local/bin/agentctl" "$TMP/bin/systemctl" "$TMP/bin/journalctl"
export PATH="$TMP/bin:$PATH"
run_guard(){ rm -f "$TMP/alerts.txt"; bash "$REPO/bin/session-guard" >/dev/null 2>&1; }
alerts(){ cat "$TMP/alerts.txt" 2>/dev/null; }

echo "== a routing env block is caught at rest =="
cat > "$A/accounts/acct-a/settings.json" <<'EOF'
{"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:3456","ANTHROPIC_MODEL":"Codex API/gpt-5.6-terra"}}
EOF
run_guard
check "alerts main about the routing override"      bash -c "alerts(){ cat '$TMP/alerts.txt' 2>/dev/null; }; alerts | grep -q 'CONFIG DRIFT'"
check "names the offending keys"                    bash -c "grep -q 'ANTHROPIC_BASE_URL' '$TMP/alerts.txt'"
check "names the config file"                       bash -c "grep -q 'acct-a/settings.json' '$TMP/alerts.txt'"
check "logged as well as alerted"                   grep -q "SETTINGS ROUTING OVERRIDE" "$A/session-guard.log"

echo "== throttling: a standing misconfiguration must not re-page every run =="
run_guard
check_not "second run inside the window sends nothing" bash -c "grep -q 'CONFIG DRIFT' '$TMP/alerts.txt' 2>/dev/null"

echo "== benign env is NOT flagged (a guard that cries wolf gets ignored) =="
rm -f "$A/.session-guard-alert."*
cat > "$A/accounts/acct-a/settings.json" <<'EOF'
{"env":{"DISABLE_AUTOUPDATER":"1"},"cleanupPeriodDays":3650}
EOF
run_guard
# Scope the assertion to THIS guard. `test -s alerts.txt` also caught the disk/liveness guards, so a
# real low-disk page on the host failed a test about env parsing — the classic over-broad assertion
# that turns an unrelated true positive into a red suite.
check_not "no alert for DISABLE_AUTOUPDATER (a real fleet sets this)" bash -c "grep -q 'CONFIG DRIFT' '$TMP/alerts.txt' 2>/dev/null"

echo "== a secret in an env block is never echoed into logs or messages =="
rm -f "$A/.session-guard-alert."*
cat > "$A/cfg/ws1/settings.json" <<'EOF'
{"env":{"ANTHROPIC_AUTH_TOKEN":"sk-ant-SUPERSECRET-do-not-log"}}
EOF
run_guard
check "the offending KEY is reported"           bash -c "grep -q 'ANTHROPIC_AUTH_TOKEN' '$TMP/alerts.txt'"
check_not "the VALUE never reaches the alert"   bash -c "grep -q 'SUPERSECRET' '$TMP/alerts.txt'"
check_not "the VALUE never reaches the log"     grep -q 'SUPERSECRET' "$A/session-guard.log"
rm -f "$A/cfg/ws1/settings.json"

echo "== composed cfg dirs are held to the shared-store invariant =="
rm -f "$A/.session-guard-alert."* "$A/session-guard.log"
rm -f "$A/cfg/ws1/projects"; mkdir -p "$A/cfg/ws1/projects"      # a diverged private store
run_guard
check "a forked session store under cfg/ is caught" grep -q "SHARED-STORE VIOLATION.*cfg/ws1" "$A/session-guard.log"

echo "== failed-unit sweep: nothing failed → silence =="
rm -f "$A"/.session-guard-alert.*
run_guard
check_not "no FAILED UNITS alert when none are failed" bash -c "grep -q 'FAILED UNITS' '$TMP/alerts.txt' 2>/dev/null"

echo "== failed-unit sweep: a failed unit pages, and a NEW failure re-pages inside the window =="
printf 'pilot-qe.service loaded failed failed compute pilot\n' > "$TMP/fixture-failed-usr.txt"
run_guard
check "a failed user unit is alerted"        bash -c "grep -q 'FAILED UNITS.*usr:pilot-qe' '$TMP/alerts.txt'"
run_guard
check_not "same failed set is throttled"     bash -c "grep -q 'FAILED UNITS' '$TMP/alerts.txt' 2>/dev/null"
printf 'logrotate.service loaded failed failed Rotate logs\n' > "$TMP/fixture-failed-sys.txt"
run_guard
check "a NEW failure pages despite the old one being in-window" bash -c "grep -q 'FAILED UNITS.*sys:logrotate' '$TMP/alerts.txt'"
rm -f "$TMP/fixture-failed-usr.txt" "$TMP/fixture-failed-sys.txt"

echo "== campaign-stall: active-but-silent compute unit pages; a chatty one does not =="
rm -f "$A"/.session-guard-alert.*
printf 'demo-camp.service loaded active running demo campaign\n' > "$TMP/fixture-running-usr.txt"
date +%s > "$TMP/fixture-journal-demo-camp.service.ts"          # logged just now
run_guard
check_not "a campaign logging NOW is not flagged" bash -c "grep -q 'CAMPAIGN STALL' '$TMP/alerts.txt' 2>/dev/null"
echo $(( $(date +%s) - 10800 )) > "$TMP/fixture-journal-demo-camp.service.ts"   # silent 3h
run_guard
check "an active campaign silent 3h is flagged"  bash -c "grep -q 'CAMPAIGN STALL: demo-camp' '$TMP/alerts.txt'"
check "the alert names the journal command"      bash -c "grep -q 'journalctl --user -u demo-camp' '$TMP/alerts.txt'"
printf 'nginx.service loaded active running Web\n' > "$TMP/fixture-running-sys.txt"
echo $(( $(date +%s) - 90000 )) > "$TMP/fixture-journal-nginx.service.ts"
rm -f "$A"/.session-guard-alert.*
run_guard
check_not "a non-compute unit is never a campaign" bash -c "grep -q 'CAMPAIGN STALL: nginx' '$TMP/alerts.txt' 2>/dev/null"
rm -f "$TMP"/fixture-running-*.txt "$TMP"/fixture-journal-*.ts

echo "== fleet-model drift: .fleet-model disagreeing with descriptors pages (the disarmed-gating find) =="
rm -f "$A"/.session-guard-alert.*
mkdir -p "$A/accounts" "$A/projects"
printf 'MODEL="claude-fable-5"\nROOT="/x"\n' > "$A/projects/wsA.env"
printf 'MODEL="claude-fable-5"\nROOT="/x"\n' > "$A/projects/wsB.env"
echo claude-opus-4-8 > "$A/accounts/.fleet-model"
run_guard
check "drift between .fleet-model and descriptors pages" bash -c "grep -q 'FLEET-MODEL DRIFT' '$TMP/alerts.txt'"
check "the alert carries the one-line fix"               bash -c "grep -q 'fleet-model' '$TMP/alerts.txt'"
echo claude-fable-5 > "$A/accounts/.fleet-model"
rm -f "$A"/.session-guard-alert.*
run_guard
check_not "agreement is silent"                          bash -c "grep -q 'FLEET-MODEL DRIFT' '$TMP/alerts.txt' 2>/dev/null"
rm -f "$A/projects/wsA.env" "$A/projects/wsB.env" "$A/accounts/.fleet-model"

echo "== stall override: file progress counts even when the journal is silent =="
rm -f "$A"/.session-guard-alert.*
printf 'filecamp-queue.service loaded active running FileCamp\n' > "$TMP/fixture-running-usr.txt"
echo $(( $(date +%s) - 90000 )) > "$TMP/fixture-journal-filecamp-queue.service.ts"   # journal: silent 25h
mkdir -p "$TMP/campdir"; touch "$TMP/campdir/results.csv"                            # files: fresh NOW
printf 'filecamp-queue.service %s\n' "$TMP/campdir" > "$A/stall-progress.conf"
run_guard
check_not "fresh progress FILES suppress the stall page" bash -c "grep -q 'CAMPAIGN STALL' '$TMP/alerts.txt' 2>/dev/null"
touch -d "-4 hours" "$TMP/campdir/results.csv" "$TMP/campdir"                        # files AND dir stale
# (the dir itself counts in the find — its mtime bumps on any create/delete within, which is
#  exactly the activity signal we want; a truly stalled campaign leaves both old)
run_guard
check "stale files + silent journal DO page"             bash -c "grep -q 'CAMPAIGN STALL: filecamp-queue' '$TMP/alerts.txt'"
rm -f "$A/stall-progress.conf" "$TMP/fixture-running-usr.txt" "$TMP"/fixture-journal-*.ts
echo "== disk growth: a fast drop is attributed =="
rm -f "$A"/.session-guard-alert.*
avail_now=$(df -BG --output=avail / | tail -1 | tr -cd '0-9')
echo $(( avail_now + 25 )) > "$A/.session-guard-avail"       # pretend we had 25G more last run
mkdir -p "$TMP/duroot/grower"; dd if=/dev/zero of="$TMP/duroot/grower/blob" bs=1M count=200 status=none
DU_ROOT="$TMP/duroot" bash "$REPO/bin/session-guard" >/dev/null 2>&1
check "a >=20G drop in one interval pages"     bash -c "grep -q 'DISK GROWTH' '$TMP/alerts.txt'"
check "the alert names a top grower"           bash -c "grep -q 'grower' '$TMP/alerts.txt'"
rm -f "$A"/.session-guard-alert.* "$A/.session-guard-avail" "$A"/.session-guard-du.*
run_guard
check_not "steady disk does not page growth"   bash -c "grep -q 'DISK GROWTH' '$TMP/alerts.txt' 2>/dev/null"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
