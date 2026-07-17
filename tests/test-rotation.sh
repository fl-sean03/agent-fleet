#!/usr/bin/env bash
# test-rotation.sh — unit tests for the usage-optimized rotation logic in bin/account-watch
# (sourced under WATCH_TEST=1: functions only, no side effects) + bin/account-usage --mock.
# No live HTTP, no probes, no swap-fleet — everything runs against a temp sandbox + canned JSON.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/../bin"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-rotation.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  - $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL- $1"; }
check(){ # check <desc> <cmd...>  — cmd rc 0 = pass
  local d="$1"; shift
  if "$@"; then ok "$d"; else bad "$d"; fi
}
check_not(){ local d="$1"; shift; if "$@"; then bad "$d"; else ok "$d"; fi; }
eq(){ [ "$1" = "$2" ] || { echo "       got: '$1'  want: '$2'"; return 1; }; }

# --- sandbox: fake ACCTS with credentialed accounts a,b,c (and none for 'nocred') ---
export A="$TMP/agents" ACCTS="$TMP/agents/accounts"
export ROT="$ACCTS/.rotation" ACTIVE_F="$ACCTS/.active" STATE="$ACCTS/.watch-state"
export LOG="$TMP/watch.log" ALERT_TS="$ACCTS/.watch-alert-ts"
mkdir -p "$ACCTS"/{a,b,c}
for x in a b c; do
  echo '{"claudeAiOauth":{"accessToken":"x"}}' > "$ACCTS/$x/.credentials.json"
  echo '{}' > "$ACCTS/$x/.claude.json"
done
printf 'a\nb\nc\n' > "$ROT"; echo a > "$ACTIVE_F"

WATCH_TEST=1 source "$BIN/account-watch"

mkjsonl(){ # mkjsonl <label> <u5> <r5> <u7> <r7>  → one usage JSONL line
  printf '{"label":"%s","five_hour":{"utilization":%s,"resets_at":"%s"},"seven_day":{"utilization":%s,"resets_at":"%s"}}\n' "$@"
}
T1="2026-07-10T06:00:00+00:00"; T2="2026-07-10T07:00:00+00:00"; T3="2026-07-10T08:00:00+00:00"
W1="2026-07-11T00:00:00+00:00"; W2="2026-07-12T00:00:00+00:00"

echo "== trigger thresholds (5h>=90 / 7d>=95, exact boundary) =="
aw_load_usage < <(mkjsonl a 90.0 "$T1" 10 "$W1"; mkjsonl b 89.9 "$T1" 10 "$W1"; mkjsonl c 50 "$T1" 95.0 "$W1")
r=$(aw_trigger_reason a); check "5h=90.0 triggers"        eq "$r" "5h=90.0%>=90"
check_not "5h=89.9 does NOT trigger"                      aw_trigger_reason b
r=$(aw_trigger_reason c); check "7d=95.0 triggers"        eq "$r" "7d=95.0%>=95"
aw_load_usage < <(mkjsonl b 50 "$T1" 94.9 "$W1")
check_not "7d=94.9 does NOT trigger"                      aw_trigger_reason b

echo "== endpoint error → rc 2 (degraded / probe-only fallback signal) =="
aw_load_usage < <(printf '{"label":"a","error":"http 401 Unauthorized"}\n'; mkjsonl b 10 "$T1" 10 "$W1")
aw_trigger_reason a; rc=$?
check "errored account reports rc=2 (usage unknown)"      eq "$rc" 2
check "error message captured"                            eq "${UERR[a]}" "http 401 Unauthorized"
check_not "errored account is never eligible"             aw_eligible a 100

echo "== eligibility + improvement guard =="
aw_load_usage < <(mkjsonl a 91 "$T1" 10 "$W1"; mkjsonl b 88 "$T1" 10 "$W1"; mkjsonl c 60 "$T2" 10 "$W1")
check_not "91→88 rejected (needs >=20-pt improvement)"    aw_eligible b 91
check     "91→60 accepted (31-pt improvement)"            aw_eligible c 91
check     "hardcap active=100 → 88 accepted"              aw_eligible b 100 0
aw_load_usage < <(mkjsonl b 90.0 "$T1" 10 "$W1"; mkjsonl c 10 "$T1" 96 "$W1")
check_not "target 5h=90 ineligible (must be <90)"         aw_eligible b 100 0
check_not "target 7d=96 ineligible (must be <95)"         aw_eligible c 100 0
aw_load_usage < <(mkjsonl nocred 5 "$T1" 5 "$W1")
check_not "no credential → ineligible"                    aw_eligible nocred 100 0

echo "== target sort: soonest 5h reset, tie-break soonest 7d reset =="
aw_load_usage < <(mkjsonl a 95 "$T1" 10 "$W1"; mkjsonl b 30 "$T3" 10 "$W1"; mkjsonl c 40 "$T2" 10 "$W1")
r=$(aw_select_targets 95 1 5h b c | tr '\n' ' ')
check "soonest 5h reset first (c@07:00 before b@08:00)"   eq "$r" "c b "
aw_load_usage < <(mkjsonl b 30 "$T2" 10 "$W2"; mkjsonl c 40 "$T2" 10 "$W1")
r=$(aw_select_targets 95 1 5h b c | tr '\n' ' ')
check "5h tie → soonest 7d reset wins (c)"                eq "$r" "c b "
r=$(aw_select_targets 95 1 5h c b | tr '\n' ' ')
check "tie-break independent of argument order"           eq "$r" "c b "

echo "== no-eligible hold =="
aw_load_usage < <(mkjsonl a 95 "$T1" 10 "$W1"; mkjsonl b 92 "$T1" 10 "$W1"; mkjsonl c 50 "$T1" 97 "$W1")
r=$(aw_select_targets 95 1 5h b c)
check "all over caps → empty target list (hold)"          eq "$r" ""
aw_load_usage < <(mkjsonl a 91 "$T1" 10 "$W1"; mkjsonl b 80 "$T1" 10 "$W1"; mkjsonl c 85 "$T1" 10 "$W1")
r=$(aw_select_targets 91 1 5h b c)
check "no candidate clears improvement guard → hold"      eq "$r" ""

echo "== debounce: 1 tick no, 2 ticks yes; account-scoped reset =="
rm -f "$STATE"
r=$(aw_debounce a 1 0); check "cap tick 1 → count 1"      eq "$r" "1 0"
r=$(aw_debounce a 1 1); check "cap tick 2 + usage tick 1" eq "$r" "2 1"
r=$(aw_debounce a 0 1); check "cap clears, usage tick 2"  eq "$r" "0 2"
r=$(aw_debounce b 1 1); check "account change resets both" eq "$r" "1 1"
echo "a 1" > "$STATE"   # legacy 2-field format
r=$(aw_debounce a 1 0); check "legacy 2-field state read" eq "$r" "2 0"

echo "== dwell guard (.active mtime) =="
touch "$ACTIVE_F"
check     "fresh swap (<30min) → dwell blocked"           aw_dwell_blocked
touch -d '40 minutes ago' "$ACTIVE_F"
check_not "old swap (>30min) → dwell clear"               aw_dwell_blocked
DWELL_SECS=3600
check     "DWELL_SECS honored (40min < 1h)"               aw_dwell_blocked
DWELL_SECS=1800

echo "== snapshot line (full picture incl. errors) =="
aw_load_usage < <(mkjsonl a 91 "$T1" 10 "$W1"; printf '{"label":"b","error":"network: timeout"}\n')
r=$(aw_snapshot a b)
check "snapshot shows util+resets and errors" eq "$r" "a 5h=91%@2026-07-10T06:00Z 7d=10%@2026-07-11T00:00Z | b ERR:network: timeout"

echo "== named-profile enforcement =="
check     "configured account resolves through profile helper" aw_cred_exists a
check_not "host is not a valid account label"             aw_cred_exists host
r=$(aw_cfg_of host 2>/dev/null || true)
check "host resolves to no config directory"              eq "$r" ""

echo "== alert throttle (1/hr) =="
rm -f "$ALERT_TS"
mkdir -p "$A/bin"                                      # alerts go via fl_send → $A/bin/fleet-msg
printf '#!/usr/bin/env bash\necho "SENT:$*" >> "%s/alerts"\n' "$TMP" > "$A/bin/fleet-msg"
chmod +x "$A/bin/fleet-msg"
aw_alert_main "first"  >/dev/null
aw_alert_main "second" >/dev/null
r=$(grep -c SENT "$TMP/alerts" 2>/dev/null || echo 0)
check "second alert within the hour throttled"            eq "$r" "1"
echo 0 > "$ALERT_TS"
aw_alert_main "third" >/dev/null
r=$(grep -c SENT "$TMP/alerts")
check "alert fires again after throttle window"           eq "$r" "2"
check "alert carries the system envelope identity"        bash -c "grep -q -- '--from system:account-watch' '$TMP/alerts'"

echo "== account-usage --mock end-to-end =="
cat > "$TMP/mock.json" <<EOF
{
  "a": {"five_hour": {"utilization": 92.5, "resets_at": "$T1"}, "seven_day": {"utilization": 41.0, "resets_at": "$W1"}},
  "b": {"error": "http 401 Unauthorized"},
  "c": {"five_hour": {"utilization": 12.0, "resets_at": "$T2"}, "seven_day": {"utilization": 9.0, "resets_at": "$W2"}}
}
EOF
out=$("$BIN/account-usage" --mock "$TMP/mock.json" a b c 2>/dev/null)
r=$(echo "$out" | python3 -c 'import json,sys; rs=[json.loads(l) for l in sys.stdin]; print(rs[0]["five_hour"]["utilization"], "error" in rs[1], rs[2]["label"])')
check "mock JSONL fields parse round-trip"                eq "$r" "92.5 True c"
check "mixed results exit 0"                              "$BIN/account-usage" --mock "$TMP/mock.json" a b c >/dev/null 2>&1
check_not "all-error exits nonzero"                       "$BIN/account-usage" --mock "$TMP/mock.json" b >/dev/null 2>&1
check_not "missing mock entry = per-account error"        "$BIN/account-usage" --mock "$TMP/mock.json" ghost >/dev/null 2>&1
out=$("$BIN/account-usage" --table --mock "$TMP/mock.json" a b c 2>/dev/null)
check "table mode renders rotate-zone + error rows" bash -c "echo '$out' | grep -q 'HIGH-5H' && echo '$out' | grep -q 'ERROR: http 401'"
check_not "no token material in any output"        bash -c "$BIN/account-usage --mock '$TMP/mock.json' a b c 2>&1 | grep -qi 'accesstoken\|bearer'"

echo "== account-watch ↔ account-usage integration (aw_load_usage over --mock output) =="
aw_load_usage < <("$BIN/account-usage" --mock "$TMP/mock.json" a b c 2>/dev/null)
r=$(aw_trigger_reason a); check "mock a triggers via pipeline" eq "$r" "5h=92.5%>=90"
aw_trigger_reason b; rc=$?; check "mock b degraded via pipeline" eq "$rc" 2
r=$(aw_select_targets "${U5[a]}" 1 5h b c); check "pipeline target = c" eq "$r" "c"


# ---- regression: fresh account with resets_at=null must not kill the snapshot (2026-07-10) ------
# A 0%-fresh account returns "resets_at": null; the parser used to TypeError on the FIRST such
# account, leaving U5 empty for ALL accounts → "no data" → probe-only → rotation silently disabled.
echo "## null-resets_at regression"
aw_load_usage <<'JSONL'
{"label":"fresh1","five_hour":{"utilization":0.0,"resets_at":null},"seven_day":{"utilization":21.0,"resets_at":"2026-07-11T06:00:00+00:00"}}
{"label":"busy1","five_hour":{"utilization":92.0,"resets_at":"2026-07-10T12:00:00+00:00"},"seven_day":{"utilization":30.0,"resets_at":null}}
JSONL
check "null-reset fresh account still parsed (U5 set)"  eq "${U5[fresh1]:-UNSET}" "0.0"
check "later accounts survive a null in an earlier one"  eq "${U5[busy1]:-UNSET}" "92.0"
r=$(aw_trigger_reason busy1); check "trigger evaluates after a null-reset account" eq "$r" "5h=92.0%>=90"
check "null-reset epoch sorts far-future"                eq "${R5E[fresh1]}" "9999999999"
mkdir -p "$ACCTS/fresh1"
echo '{"claudeAiOauth":{"accessToken":"x"}}' > "$ACCTS/fresh1/.credentials.json"
echo '{}' > "$ACCTS/fresh1/.claude.json"
printf 'fresh1\n' >> "$ROT"
r=$(aw_select_targets 92.0 1 5h fresh1)
check "fresh (null-reset) account is still an eligible target" eq "$r" "fresh1"

# --- fable-cap markers: the MODEL-specific cap the usage endpoint can't see (2026-07-10) ---------
echo "## fable-cap markers"
aw_load_usage < <(mkjsonl a 72.0 "$T1" 40 "$W1"; mkjsonl b 30 "$T2" 20 "$W1"; mkjsonl c 20 "$T3" 20 "$W1")
now=$(date +%s)
echo $((now + 3600)) > "$ACCTS/.fable-cap.b"
check "fresh marker → aw_fable_capped true"               aw_fable_capped b
check_not "no marker → aw_fable_capped false"             aw_fable_capped c
# marker presence is model-agnostic; whether it BLOCKS rotation is model-aware (below)
FLEET_MODEL="claude-fable-5"   # Fable fleet: a Fable-cap makes an account ineligible
check_not "FABLE fleet: fable-capped account NOT eligible" aw_eligible b 72.0 0
check "FABLE fleet: unmarked twin IS eligible"            aw_eligible c 72.0 0
r=$(aw_select_targets 72.0 0 5h b c)
check "FABLE fleet: selection skips the fable-capped one" eq "$r" "c"
check "fable_cap_relevant true on Fable fleet"            fable_cap_relevant b
echo $((now - 10)) > "$ACCTS/.fable-cap.c"
check_not "EXPIRED marker no longer blocks"               aw_fable_capped c
check "expired marker file was removed on read"           test ! -f "$ACCTS/.fable-cap.c"
echo "garbage" > "$ACCTS/.fable-cap.c"
check_not "garbled marker treated as absent (removed)"    aw_fable_capped c

# --- MODEL-AWARE eligibility (2026-07-11 bug fix): an OPUS fleet must ignore Fable-cap markers -----
echo "## model-aware fable-cap gating"
echo $((now + 3600)) > "$ACCTS/.fable-cap.b"   # b is fable-capped again
FLEET_MODEL="claude-opus-4-8"   # Opus fleet: Fable works nowhere-relevant → markers must NOT block
check_not "OPUS fleet: fable_cap_relevant is FALSE even with a fresh marker" fable_cap_relevant b
check "OPUS fleet: fable-capped account IS still eligible"  aw_eligible b 72.0 0
r=$(aw_select_targets 72.0 0 5h b c)
check "OPUS fleet: selection does NOT skip a fable-capped candidate" bash -c "echo '$r' | grep -q b"
FLEET_MODEL="claude-fable-5"   # restore for any later assertions
rm -f "$ACCTS/.fable-cap.b" "$ACCTS/.fable-cap.c"

# --- deadline-aware backoff (no-eligible hold cadence, the operator 2026-07-10) --------------------------
echo "## deadline-aware backoff"
export BACKOFF_F="$ACCTS/.watch-backoff"; export SAFETY_POLL_SECS=1800 BACKOFF_MARGIN=60
rm -f "$ACCTS"/.fable-cap.* "$BACKOFF_F"
NOW=1783900000
# 5h resets: a soon (NOW+600), b far (NOW+9000); plus a Fable marker on c at NOW+300 (soonest)
aw_load_usage < <(
  printf '{"label":"a","five_hour":{"utilization":95,"resets_at":"%s"},"seven_day":{"utilization":10,"resets_at":"%s"}}\n' "$(date -u -d @$((NOW+600)) +%FT%T+00:00)" "$W1"
  printf '{"label":"b","five_hour":{"utilization":95,"resets_at":"%s"},"seven_day":{"utilization":10,"resets_at":"%s"}}\n' "$(date -u -d @$((NOW+9000)) +%FT%T+00:00)" "$W1")
echo $((NOW+300)) > "$ACCTS/.fable-cap.c"
r=$(aw_earliest_reset "$NOW"); check "earliest reset = soonest across 5h + fable markers" eq "$r" "$((NOW+300))"
# deadline = min(reset+margin, now+floor): reset(+60) is sooner than the 1800 floor here
d=$(aw_backoff_deadline "$NOW" "$((NOW+300))"); check "deadline uses reset+margin when < floor" eq "$d" "$((NOW+360))"
d=$(aw_backoff_deadline "$NOW" "$((NOW+999999))"); check "deadline capped by safety floor when reset far" eq "$d" "$((NOW+1800))"
rm -f "$ACCTS"/.fable-cap.c
# arm + skip semantics
aw_backoff_arm a $((NOW+360))
check "skip WHILE within window (now<deadline)"          aw_backoff_skip a "$NOW"
check_not "no skip once deadline passed"                 aw_backoff_skip a "$((NOW+400))"
check "expired marker removed on read"                   test ! -f "$BACKOFF_F"
aw_backoff_arm a $((NOW+360))
check_not "skip does NOT fire when active account changed (swap happened)" aw_backoff_skip b "$NOW"
check "stale (acct-mismatch) marker removed"             test ! -f "$BACKOFF_F"
check_not "no marker → never skip"                       aw_backoff_skip a "$NOW"

# --- 7d-guard regression (incident 2026-07-12: fleet stranded, main hand-swapped) ----------------
# Active red: 7d=95 (TRIGGER) but 5h=1 (just reset). Old code guarded the 7d trigger on the 5h axis:
# every target needed 5h <= 1-20 = -19 → impossible → "NO ELIGIBLE" forever. The guard must compare
# the TRIGGERING axis: b (7d=60 <= 95-20) eligible; account-c (7d=81 > 75) still correctly rejected.
echo "## 7d-trigger guard axis (2026-07-12 stranding regression)"
FLEET_MODEL="claude-opus-4-8"
rm -f "$ACCTS"/.fable-cap.*
aw_load_usage < <(mkjsonl a 1.0 "$T1" 95.0 "$W1"; mkjsonl b 0.0 "$T1" 60.0 "$W1"; mkjsonl c 0.0 "$T2" 81.0 "$W1")
r=$(aw_trigger_reason a); check "scenario triggers on 7d"          eq "$r" "7d=95.0%>=95"
check     "7d axis: 95→60 target eligible (35-pt 7d improvement)"  aw_eligible b 95.0 1 7d
check_not "7d axis: 95→81 target rejected (needs >=20-pt)"         aw_eligible c 95.0 1 7d
check_not "old 5h-axis compare WOULD have stranded (b vs 5h=1)"    aw_eligible b 1.0 1 5h
r=$(aw_select_targets 95.0 1 7d b c)
check "7d-axis selection finds the target the incident needed"     eq "$r" "b"
r=$(aw_select_targets 1.0 1 5h b c)
check "5h-axis selection over the same data = empty (the old bug)" eq "$r" ""
r=$(aw_select_targets 95.0 1 bogus b c 2>/dev/null); rc=$?
check "bad axis fails LOUDLY (rc!=0, no silent candidate loss)"    bash -c "[ '$rc' != 0 ] && [ -z '$r' ]"

# --- per-model Fable cap from the USAGE ENDPOINT (incident 2026-07-12: example-confined stranded 6h) ----------
echo "## fable cap via usage endpoint (aw_fable_blocked)"
mkjsonl_fable(){ # <label> <u5> <r5> <u7> <r7> <fable_u> <fable_r> → JSONL line WITH fable field
  printf '{"label":"%s","five_hour":{"utilization":%s,"resets_at":"%s"},"seven_day":{"utilization":%s,"resets_at":"%s"},"fable":{"utilization":%s,"resets_at":"%s"}}\n' "$@"
}
FR="2026-07-20T09:00:00+00:00"
rm -f "$ACCTS"/.fable-cap.*
aw_load_usage < <(mkjsonl_fable a 10 "$T1" 20 "$W1" 100 "$FR"; mkjsonl_fable b 10 "$T2" 20 "$W1" 98 "$FR"; mkjsonl a2 10 "$T3" 20 "$W1")
check "FU parsed from the fable field"                     eq "${FU[a]}" "100"
check "fable-less account has empty FU (no crash)"         eq "${FU[a2]:-EMPTY}" "EMPTY"
check     "fable=100 via endpoint → blocked (no marker!)"  aw_fable_blocked a
check_not "fable=98 → not blocked (below 99 threshold)"    aw_fable_blocked b
check_not "no fable data → not blocked"                    aw_fable_blocked a2
now=$(date +%s); echo $((now + 3600)) > "$ACCTS/.fable-cap.a2"
check "marker alone still blocks (event-driven path kept)" aw_fable_blocked a2
rm -f "$ACCTS/.fable-cap.a2"
# pick a "now" PAST the 5h/7d resets (2026-07-10/11) but BEFORE the fable reset (2026-07-20):
# the fable weekly reset must then be the only future reset aw_earliest_reset can find.
r=$(aw_earliest_reset "$(date -u -d '2026-07-15T00:00:00+00:00' +%s)")
check "earliest reset includes the fable weekly reset"     eq "$r" "$(date -u -d "$FR" +%s)"

# --- divergent-Fable workspace detection + target preference -------------------------------------
echo "## divergent-model preference (example-confined/ws-beta on an Opus fleet)"
mkdir -p "$A/projects"
printf 'ROOT="/x"\nMODEL="claude-fable-5"   # pinned\n' > "$A/projects/ws-beta.env"
printf 'ROOT="/x"\nMODEL="claude-opus-4-8"\n' > "$A/projects/ws-gpu.env"
printf 'ROOT="/x"\n' > "$A/projects/nomodel.env"
FLEET_MODEL="claude-opus-4-8"
r=$(aw_divergent_fable | sort | tr '\n' ' ')
check "fable-pinned ws detected on an Opus fleet"          eq "$r" "ws-beta "
FLEET_MODEL="claude-fable-5"
r=$(aw_divergent_fable)
check "no divergence when the fleet itself is Fable"       eq "$r" ""
FLEET_MODEL="claude-opus-4-8"
aw_load_usage < <(mkjsonl_fable a 10 "$T1" 20 "$W1" 100 "$FR"; mkjsonl_fable b 10 "$T2" 20 "$W1" 5 "$FR")
r=$(aw_prefer_fable_ok a b | tr '\n' ' ')
check "partition: fable-healthy target first"              eq "$r" "b a "
r=$(aw_prefer_fable_ok b a | tr '\n' ' ')
check "partition stable when already ordered"              eq "$r" "b a "
aw_load_usage < <(mkjsonl_fable a 10 "$T1" 20 "$W1" 100 "$FR"; mkjsonl_fable b 10 "$T2" 20 "$W1" 100 "$FR")
r=$(aw_prefer_fable_ok a b | tr '\n' ' ')
check "all blocked → original order kept (fleet still moves)" eq "$r" "a b "
check "OPUS fleet: fable-blocked account still ELIGIBLE (preference, not veto)" aw_eligible a 100 0

# --- 401 self-heal (incident 2026-07-12: idle tokens expire → failover blind) --------------------
echo "## 401 self-heal (aw_heal_401s)"
STUB_LOG="$TMP/refresh-calls"
cat > "$TMP/refresh-stub" <<'EOF'
#!/usr/bin/env bash
echo "$1" >> "${STUB_LOG:?}"
case "$1" in deadtoken) exit 2 ;; *) exit 0 ;; esac
EOF
chmod +x "$TMP/refresh-stub"
export STUB_LOG ACCOUNT_REFRESH="$TMP/refresh-stub"
rm -f "$STUB_LOG"
aw_load_usage < <(
  printf '{"label":"a","error":"http 401 Unauthorized"}\n'
  printf '{"label":"b","error":"http 429 Too Many Requests"}\n'
  printf '{"label":"c","error":"network: timeout"}\n'
  mkjsonl a2 10 "$T1" 10 "$W1")
r=$(aw_heal_401s)
check "exactly the 401 account healed (count=1)"           eq "$r" "1"
check "refresh stub called for the 401 account only"       eq "$(cat "$STUB_LOG" | tr '\n' ' ')" "a "
rm -f "$STUB_LOG"
aw_load_usage < <(printf '{"label":"deadtoken","error":"http 401 Unauthorized"}\n')
r=$(aw_heal_401s)
check "failed refresh (dead refresh token) → healed=0"     eq "$r" "0"
check "refresh WAS attempted before giving up"             eq "$(cat "$STUB_LOG")" "deadtoken"
rm -f "$STUB_LOG"
aw_load_usage < <(mkjsonl a 10 "$T1" 10 "$W1")
r=$(aw_heal_401s)
check "no errors → no refresh calls, healed=0"             bash -c "[ '$r' = 0 ] && [ ! -f '$STUB_LOG' ]"
unset ACCOUNT_REFRESH STUB_LOG

# --- scoped-cap TRIGGER + fable-headroom eligibility (2026-07-16 blind-stall regression) ----------
# The active account's scoped fable cap sat at 100% for ~2h while 5h=43/7d=57 read healthy. Ticks
# logged only 5h/7d; aw_trigger_reason never consulted FU; a naive destination pick could have landed
# the fleet right back on a fable-exhausted account. Rotation fired only when 5h independently hit 96.
echo "## scoped fable cap trigger (2026-07-16 blind-stall regression)"
FLEET_MODEL="claude-fable-5"; rm -f "$ACCTS"/.fable-cap.*
FR2="2026-07-18T06:00:00+00:00"; FR3="2026-07-20T09:00:00+00:00"
# the incident's exact shape: a=active(exhausted)  b,c=healthy targets
aw_load_usage < <(mkjsonl_fable a 43.0 "$T1" 57.0 "$W1" 100.0 "$FR"
                  mkjsonl_fable b 0.0  "$T2" 33.0 "$W1" 49.0  "$FR2"
                  mkjsonl_fable c 36.0 "$T3" 27.0 "$W1" 34.0  "$FR3")
r=$(aw_trigger_reason a); check "FABLE fleet: fable=100 TRIGGERS though 5h=43/7d=57 (the blind spot)" eq "$r" "fable=100.0%>=90"
check_not "fable=49 under threshold does NOT trigger"          aw_trigger_reason b
check_not "fable-triggered active is itself no longer eligible" aw_eligible a 100 0
r=$(aw_select_targets 100.0 1 fable b c | tr '\n' ' ')
check "fable axis: both healthy targets eligible, soonest-5h-reset order" eq "$r" "b c "
FLEET_MODEL="claude-opus-4-8"
check_not "OPUS fleet: fable=100 does NOT trigger (model-aware)"  aw_trigger_reason a
check     "OPUS fleet: fable=100 account still eligible as target" aw_eligible a 100 0
FLEET_MODEL="claude-fable-5"
# boundary + capless
aw_load_usage < <(mkjsonl_fable a 10 "$T1" 10 "$W1" 90.0 "$FR"; mkjsonl_fable b 10 "$T2" 10 "$W1" 89.9 "$FR"; mkjsonl c 10 "$T3" 10 "$W1")
r=$(aw_trigger_reason a); check "fable=90.0 boundary triggers"    eq "$r" "fable=90.0%>=90"
check_not "fable=89.9 does NOT trigger"                           aw_trigger_reason b
check_not "capless account (FU empty) never fable-triggers"       aw_trigger_reason c
check_not "FABLE fleet: target at fable=90 ineligible (ceiling)"  aw_eligible a 100 0
check     "FABLE fleet: target at fable=89.9 eligible"            aw_eligible b 100 0
check     "FABLE fleet: capless target eligible (FU empty = headroom)" aw_eligible c 100 0
# improvement guard measured on the FABLE axis (active fable=100, IMPROVE_PTS=20 → target <= 80)
aw_load_usage < <(mkjsonl_fable a 10 "$T1" 10 "$W1" 100.0 "$FR"
                  mkjsonl_fable b 10 "$T2" 10 "$W1" 85.0  "$FR2"
                  mkjsonl_fable c 10 "$T3" 10 "$W1" 49.0  "$FR3"
                  mkjsonl d 10 "$T3" 10 "$W1")
mkdir -p "$ACCTS/d"; echo '{"claudeAiOauth":{"accessToken":"x"}}' > "$ACCTS/d/.credentials.json"; echo '{}' > "$ACCTS/d/.claude.json"
printf 'd\n' >> "$ROT"   # account-profile validates labels against the rotation allow-list
check_not "fable guard: 100→85 rejected (needs >=20-pt fable improvement)" aw_eligible b 100.0 1 fable
check     "fable guard: 100→49 accepted"                          aw_eligible c 100.0 1 fable
check     "fable guard: capless target = 0% fable (always improves)" aw_eligible d 100.0 1 fable
# snapshot now carries the fable field (the HOLD/DECISION visibility fix)
r=$(aw_snapshot a)
check "snapshot includes fable%%"                                 bash -c "echo '$r' | grep -q 'fable=100.0%'"
# end-to-end through the REAL endpoint parser: --mock body with a live-schema limits[] array
cat > "$TMP/mock-fable.json" <<EOF
{
  "a": {"five_hour": {"utilization": 43.0, "resets_at": "$T1"}, "seven_day": {"utilization": 57.0, "resets_at": "$W1"},
        "limits": [{"kind": "weekly_scoped", "percent": 100, "resets_at": "$FR",
                    "scope": {"model": {"id": null, "display_name": "Fable"}, "surface": null}}]},
  "c": {"five_hour": {"utilization": 36.0, "resets_at": "$T3"}, "seven_day": {"utilization": 27.0, "resets_at": "$W1"}}
}
EOF
aw_load_usage < <("$BIN/account-usage" --mock "$TMP/mock-fable.json" a c 2>/dev/null)
check "limits[]→fable→FU round-trip (real parser)"                eq "${FU[a]}" "100.0"
check "no limits[] → FU empty through the real parser"            eq "${FU[c]:-EMPTY}" "EMPTY"
r=$(aw_trigger_reason a); check "END-TO-END: the stall scenario now triggers rotation" eq "$r" "fable=100.0%>=90"

# --- weekly-over-fable policy (operator directive 2026-07-17 [[rotation-weekly-over-fable]]) -------
echo "## weekly-over-fable: 24h-reset preference + drop-to-Opus fallback"
FLEET_MODEL="claude-fable-5"; rm -f "$ACCTS"/.fable-cap.*
IMM7=$(date -u -d '+12 hours' +%FT%T+00:00)
FAR7=$(date -u -d '+100 hours' +%FT%T+00:00)
T5a=$(date -u -d '+2 hours' +%FT%T+00:00); T5b=$(date -u -d '+1 hour' +%FT%T+00:00)
elig_relax(){ local rc; FABLE_RELAX=1; aw_eligible "$@"; rc=$?; unset FABLE_RELAX; return $rc; }
sel_relax(){ FABLE_RELAX=1; aw_select_targets "$@"; unset FABLE_RELAX; }

aw_load_usage < <(mkjsonl a 10 "$T5a" 50 "$IMM7"; mkjsonl b 10 "$T5a" 50 "$FAR7"
                  printf '{"label":"c","five_hour":{"utilization":10,"resets_at":"%s"},"seven_day":{"utilization":0,"resets_at":null}}\n' "$T5a")
check "imminent: 7d reset in 12h → true"                aw_weekly_imminent a
check_not "not imminent: 7d reset in 100h → false"      aw_weekly_imminent b
check_not "no 7d window (null reset) → not imminent"    aw_weekly_imminent c

aw_load_usage < <(mkjsonl_fable a 10 "$T5a" 96 "$IMM7" 100 "$FAR7")
check "imminent: 7d=96 over ceiling + fable=100 exhausted → STILL eligible" aw_eligible a 100 1
aw_load_usage < <(mkjsonl a 85 "$T5a" 50 "$IMM7"; mkjsonl b 85 "$T5a" 50 "$FAR7")
check "imminent: improvement guard waived (5h=85 vs active 90)"   aw_eligible a 90 1 5h
check_not "non-imminent twin: same 5h=85 vs 90 rejected by guard" aw_eligible b 90 1 5h

aw_load_usage < <(mkjsonl a 100 "$T5a" 10 "$IMM7")
check_not "imminent but 5h=100 → still ineligible (5h ceiling always)"  aw_eligible a 100 0
check_not "relax but 5h=100 → still ineligible (5h ceiling always)"     elig_relax a 100 0

aw_load_usage < <(mkjsonl a 10 "$T5b" 10 "$FAR7"; mkjsonl b 10 "$T5a" 10 "$IMM7")
r=$(aw_select_targets 100 0 5h a b | tr '\n' ' ')
check "imminent account sorts FIRST despite later 5h reset (a@1h non-imm, b@2h imm)" eq "$r" "b a "

aw_load_usage < <(mkjsonl_fable a 10 "$T5a" 50 "$FAR7" 100 "$FAR7"; mkjsonl_fable b 10 "$T5b" 50 "$FAR7" 100 "$FAR7")
check "strict fable-select: empty (both fable-exhausted)"        eq "$(aw_select_targets 100 0 fable a b)" ""
r=$(sel_relax 100 0 5h a b | tr '\n' ' ')
check "relaxed select: both fable-exhausted accounts eligible → drop-to-Opus" eq "$r" "b a "

rm -f "$A"/projects/*.env
printf 'ROOT="/x"\nMODEL="claude-fable-5"   # keep this comment\n' > "$A/projects/wf-a.env"
printf 'ROOT="/x"\nMODEL="claude-opus-4-8"\n' > "$A/projects/wf-b.env"
printf 'ROOT="/x"\n' > "$A/projects/wf-none.env"
echo claude-fable-5 > "$ACCTS/.fleet-model"
aw_flip_to_opus
check "flip: .fleet-model → opus"                 eq "$(cat "$ACCTS/.fleet-model")" "claude-opus-4-8"
check "flip: fable descriptor MODEL → opus"       grep -q '^MODEL="claude-opus-4-8"' "$A/projects/wf-a.env"
check "flip: trailing comment preserved"          grep -q 'keep this comment' "$A/projects/wf-a.env"
check "flip: already-opus descriptor unchanged"   grep -q '^MODEL="claude-opus-4-8"' "$A/projects/wf-b.env"
check_not "flip: no-MODEL descriptor still has no MODEL"  grep -q MODEL "$A/projects/wf-none.env"
rm -f "$A"/projects/*.env; FLEET_MODEL="claude-opus-4-8"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
