#!/usr/bin/env bash
# test-account-usage.sh — unit tests for bin/account-usage's endpoint parsing, focused on the
# per-model (Fable) weekly_scoped cap surface added 2026-07-13 (incident: example-confined stranded 6h on a
# Fable-capped account invisible to 5h/7d). Everything runs against --mock canned bodies; no HTTP.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/../bin"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-account-usage.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  - $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL- $1"; }
check(){ local d="$1"; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }
eq(){ [ "$1" = "$2" ] || { echo "       got: '$1'  want: '$2'"; return 1; }; }

# sandbox accounts dir so default_labels()/cfg_of() never touch the real one
export HOME="$TMP/home"
mkdir -p "$HOME/.agents/accounts"
printf 'a\n' > "$HOME/.agents/accounts/.rotation"

W5='{"utilization": 10.0, "resets_at": "2026-07-13T18:00:00+00:00"}'
W7='{"utilization": 20.0, "resets_at": "2026-07-20T09:00:00+00:00"}'
base(){ echo "{\"five_hour\": $W5, \"seven_day\": $W7 $1}"; }

jget(){ python3 -c "
import json,sys
r=json.loads(sys.stdin.readline())
v=r
for k in sys.argv[1:]:
    v=v.get(k) if isinstance(v,dict) else None
print(json.dumps(v))" "$@"; }

run(){ "$BIN/account-usage" --mock "$1" --json a; }

echo "— live-schema fable extraction —"
# exactly the shape the real endpoint returned 2026-07-13 (id NULL, display_name "Fable", percent int)
cat > "$TMP/m1.json" <<EOF
{"a": $(base ', "limits": [
  {"kind":"session","group":"session","percent":48,"resets_at":"2026-07-13T14:10:00+00:00","scope":null,"is_active":true},
  {"kind":"weekly_all","group":"weekly","percent":9,"resets_at":"2026-07-20T09:00:00+00:00","scope":null,"is_active":false},
  {"kind":"weekly_scoped","group":"weekly","percent":18,"resets_at":"2026-07-20T09:00:00+00:00","scope":{"model":{"id":null,"display_name":"Fable"},"surface":null},"is_active":false}]')}
EOF
check "fable.utilization from live schema (percent, id=null)" eq "$(run "$TMP/m1.json" | jget fable utilization)" "18.0"
check "fable.resets_at carried through" eq "$(run "$TMP/m1.json" | jget fable resets_at)" '"2026-07-20T09:00:00+00:00"'
check "scoped map contains the fable entry" eq "$(run "$TMP/m1.json" | jget scoped fable utilization)" "18.0"

echo "— missing / malformed limits —"
cat > "$TMP/m2.json" <<EOF
{"a": $(base '')}
EOF
check "no limits key → fable null, record still emitted" eq "$(run "$TMP/m2.json" | jget fable)" "null"
check "no limits key → scoped is {}" eq "$(run "$TMP/m2.json" | jget scoped)" "{}"
cat > "$TMP/m3.json" <<EOF
{"a": $(base ', "limits": {"not": "a list"}')}
EOF
check "limits not a list → fable null (no crash)" eq "$(run "$TMP/m3.json" | jget fable)" "null"
cat > "$TMP/m4.json" <<EOF
{"a": $(base ', "limits": [null, 42, "x", {"kind":"weekly_scoped","scope":null,"percent":50},
  {"kind":"weekly_scoped","scope":{"model":null},"percent":50},
  {"kind":"weekly_scoped","scope":{"model":{"id":null,"display_name":null}},"percent":50}]')}
EOF
check "garbage entries + null scopes/names all skipped safely" eq "$(run "$TMP/m4.json" | jget fable)" "null"

echo "— multiple scoped models / duplicates —"
cat > "$TMP/m5.json" <<EOF
{"a": $(base ', "limits": [
  {"kind":"weekly_scoped","percent":40,"resets_at":"2026-07-20T09:00:00+00:00","scope":{"model":{"id":null,"display_name":"Fable"}}},
  {"kind":"weekly_scoped","percent":90,"resets_at":"2026-07-21T09:00:00+00:00","scope":{"model":{"id":null,"display_name":"Fable"}}},
  {"kind":"weekly_scoped","percent":70,"resets_at":"2026-07-20T09:00:00+00:00","scope":{"model":{"id":null,"display_name":"Opus"}}}]')}
EOF
check "duplicate fable entries → worst (90) wins" eq "$(run "$TMP/m5.json" | jget fable utilization)" "90.0"
check "other scoped model (opus) present in scoped map" eq "$(run "$TMP/m5.json" | jget scoped opus utilization)" "70.0"
check "other scoped model does NOT leak into fable" eq "$(run "$TMP/m5.json" | jget fable resets_at)" '"2026-07-21T09:00:00+00:00"'

echo "— identity via model id when display_name is absent —"
cat > "$TMP/m6.json" <<EOF
{"a": $(base ', "limits": [
  {"kind":"weekly_scoped","percent":33,"scope":{"model":{"id":"claude-fable-5","display_name":null}}}]')}
EOF
check "matched via scope.model.id" eq "$(run "$TMP/m6.json" | jget fable utilization)" "33.0"
check "missing resets_at → null (not crash)" eq "$(run "$TMP/m6.json" | jget fable resets_at)" "null"

echo "— alternate utilization key + non-numeric percent —"
cat > "$TMP/m7.json" <<EOF
{"a": $(base ', "limits": [
  {"kind":"weekly_scoped","utilization":55,"scope":{"model":{"display_name":"Fable"}}}]')}
EOF
check "alternate 'utilization' key accepted" eq "$(run "$TMP/m7.json" | jget fable utilization)" "55.0"
cat > "$TMP/m8.json" <<EOF
{"a": $(base ', "limits": [
  {"kind":"weekly_scoped","percent":"garbage","scope":{"model":{"display_name":"Fable"}}}]')}
EOF
check "non-numeric percent → utilization null, entry kept" eq "$(run "$TMP/m8.json" | jget fable utilization)" "null"

echo "— error passthrough + table rendering —"
cat > "$TMP/m9.json" <<'EOF'
{"a": {"error": "http 401 Unauthorized"}}
EOF
check "error label passthrough unchanged" eq "$("$BIN/account-usage" --mock "$TMP/m9.json" --json a | jget error)" '"http 401 Unauthorized"'
cat > "$TMP/m10.json" <<EOF
{"a": $(base ', "limits": [
  {"kind":"weekly_scoped","percent":100,"resets_at":"2026-07-20T09:00:00+00:00","scope":{"model":{"display_name":"Fable"}}}]')}
EOF
tbl=$("$BIN/account-usage" --mock "$TMP/m10.json" --table a)
check "table shows FABLE-CAPPED at 100%" bash -c "grep -q 'FABLE-CAPPED' <<<'$tbl'"
check "table has FABLE% column" bash -c "grep -q 'FABLE%' <<<'$tbl'"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
