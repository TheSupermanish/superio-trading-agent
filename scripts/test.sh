#!/usr/bin/env bash
# Run every test suite. Exits non-zero if any of them fail.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
failed=0

# Every suite gets its own empty journal. Reading the operator's real one let a
# structure rehearsed at a keyboard weeks ago charge risk against a test's
# imaginary book, so a passing suite depended on what was lying around in
# data/.
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

for suite in tests_risk.py tests_carry.py tests_adopt.py tests_fills.py tests_manager.py tests_retry.py; do
  echo "=== $suite ==="
  DRY_RUN=true SUPERIO_DB="$SCRATCH/${suite%.py}.db" "$PY" "$suite" || failed=1
done
[ "$failed" = "0" ] && echo "ALL SUITES PASS" || echo "SOME SUITES FAILED"
exit $failed
