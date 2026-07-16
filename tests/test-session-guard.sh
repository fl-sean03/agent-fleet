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
chmod +x "$TMP/bin/tmux" "$TMP/bin/pgrep" "$TMP/.local/bin/agentctl"
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

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
