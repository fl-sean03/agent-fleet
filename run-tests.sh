#!/usr/bin/env bash
# run-tests.sh — the whole suite. Sandboxed fixtures only: no live credentials, no network, no tmux
# side effects on your real fleet.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
PY="${PYTHON:-python3}"
pass=0; fail=0
echo "=== shell suites ==="
for t in tests/*.sh; do
  out=$(timeout 240 bash "$t" 2>&1); line=$(echo "$out" | tail -1)
  p=$(echo "$line" | grep -oP 'PASS=\K[0-9]+'); f=$(echo "$line" | grep -oP 'FAIL=\K[0-9]+')
  printf "  %-30s %s\n" "$(basename "$t")" "$line"
  [ "${f:-1}" = 0 ] || { fail=$((fail+${f:-1})); echo "$out" | grep -- "FAIL-" | head -5; }
  pass=$((pass+${p:-0}))
done
echo "=== brain (python) ==="
if "$PY" -c "import pytest" 2>/dev/null; then
  "$PY" -m pytest brain/tests -q 2>&1 | tail -3
  "$PY" -m pytest brain/tests -q >/dev/null 2>&1 || fail=$((fail+1))
  pass=$((pass + $("$PY" -m pytest brain/tests -q 2>/dev/null | grep -oP '\d+(?= passed)' | tail -1)))
else
  echo "  SKIP: pytest not installed (pip install pytest)"
fi
echo
echo "TOTAL PASS=$pass  FAIL=$fail"
[ "$fail" = 0 ]
