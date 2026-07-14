#!/usr/bin/env bash
# test-swap-model-gate.sh — unit tests for swap-fleet's per-model cap gate (incident 2026-07-12:
# example-confined, the sole Fable-5 workspace, was moved onto a Fable-capped account and stranded ~6h; swap
# validated only 5h/7d). Sources bin/swap-fleet under SWAP_TEST=1 (helpers only, no roster/moves).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/../bin"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-swap-gate.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  - $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL- $1"; }
check(){ local d="$1"; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }
check_not(){ local d="$1"; shift; if "$@"; then bad "$d"; else ok "$d"; fi; }
eq(){ [ "$1" = "$2" ] || { echo "       got: '$1'  want: '$2'"; return 1; }; }

# sandbox: fake agents dir + a stub account-usage the gate will consult
export A="$TMP/agents" ACCTS="$TMP/agents/accounts" LOG="$TMP/swap.log"
mkdir -p "$ACCTS" "$A/projects" "$A/bin"
cat > "$A/bin/account-usage" <<'EOF'
#!/usr/bin/env bash
# stub: emits the canned line for the requested label from $USAGE_CANNED
shift 0
for a in "$@"; do case "$a" in --json) ;; *) lbl="$a" ;; esac; done
grep "\"label\": \"$lbl\"" "${USAGE_CANNED:?}" || true
EOF
chmod +x "$A/bin/account-usage"
export AUSAGE="$A/bin/account-usage" USAGE_CANNED="$TMP/canned.jsonl"

SWAP_TEST=1 source "$BIN/swap-fleet" dummy-acct

echo "== fable_blocked: marker path =="
now=$(date +%s)
echo $((now + 3600)) > "$ACCTS/.fable-cap.acct1"
check "fresh marker → blocked"                          fable_blocked acct1
echo $((now - 10)) > "$ACCTS/.fable-cap.acct2"
cat > "$USAGE_CANNED" <<'EOF'
EOF
check_not "expired marker + no usage data → not blocked" fable_blocked acct2
rm -f "$ACCTS"/.fable-cap.*

echo "== fable_blocked: usage-endpoint path (no marker) =="
cat > "$USAGE_CANNED" <<'EOF'
{"label": "acct3", "five_hour": {"utilization": 5}, "seven_day": {"utilization": 5}, "fable": {"utilization": 100, "resets_at": "2026-07-20T09:00:00+00:00"}}
{"label": "acct4", "five_hour": {"utilization": 5}, "seven_day": {"utilization": 5}, "fable": {"utilization": 55, "resets_at": "2026-07-20T09:00:00+00:00"}}
{"label": "acct5", "five_hour": {"utilization": 5}, "seven_day": {"utilization": 5}, "fable": null}
{"label": "acct6", "error": "http 401 Unauthorized"}
EOF
check     "endpoint fable=100 → blocked (no marker needed)" fable_blocked acct3
check_not "endpoint fable=55 → not blocked"                 fable_blocked acct4
check_not "fable null (no cap on account) → not blocked"    fable_blocked acct5
check_not "endpoint error → fail OPEN (not blocked)"        fable_blocked acct6
FABLE_BLOCK_PCT=50
check "FABLE_BLOCK_PCT honored (55 >= 50 → blocked)"        fable_blocked acct4
FABLE_BLOCK_PCT=99

echo "== ws_model / ws_rc_on descriptor parsing =="
printf 'ROOT="/x"\nMODEL="claude-fable-5"   # pinned\nREMOTE_CONTROL="on"\n' > "$A/projects/wsf.env"
printf 'ROOT="/x"\nMODEL="claude-opus-4-8"\nREMOTE_CONTROL="off"\n' > "$A/projects/wso.env"
printf 'ROOT="/x"\n' > "$A/projects/wsn.env"
check "fable pin parsed"                    eq "$(ws_model wsf)" "claude-fable-5"
check "opus pin parsed"                     eq "$(ws_model wso)" "claude-opus-4-8"
check "no MODEL line → empty"               eq "$(ws_model wsn)" ""
check "RC on detected"                      ws_rc_on wsf
check_not "RC off is not on"                ws_rc_on wso
check_not "missing RC line is not on"       ws_rc_on wsn

echo "== model_gate_skip: the actual leave-behind decision =="
TARGET_FABLE_BLOCKED=1
check     "fable ws + blocked target → SKIP (left behind)"  model_gate_skip wsf
check_not "opus ws + blocked target → moves normally"       model_gate_skip wso
check_not "modelless ws + blocked target → moves normally"  model_gate_skip wsn
TARGET_FABLE_BLOCKED=0
check_not "fable ws + healthy target → moves normally"      model_gate_skip wsf

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
