#!/usr/bin/env bash
# Run every test suite. Exits non-zero if any of them fail.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
failed=0
for suite in tests_risk.py tests_fills.py tests_manager.py tests_retry.py; do
  echo "=== $suite ==="
  DRY_RUN=true "$PY" "$suite" || failed=1
done
[ "$failed" = "0" ] && echo "ALL SUITES PASS" || echo "SOME SUITES FAILED"
exit $failed
