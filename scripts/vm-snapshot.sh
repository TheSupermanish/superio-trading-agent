#!/usr/bin/env bash
# Write each profile's snapshot into the directory the dashboard serves.
set -uo pipefail
ROOT=/opt/superio
cd "$ROOT"
OUT="$ROOT/dashboard/out"
mkdir -p "$OUT"
ALPACA_PROFILE=main STRATEGY_VARIANT=barbell "$ROOT/.venv/bin/python" -c \
  "from pathlib import Path; from engine.report import write; write(Path('$OUT/snapshot.json'))" 2>/dev/null
for pair in test2:convex_tilt test3:income_only; do
  prof="${pair%%:*}"; var="${pair##*:}"
  ALPACA_PROFILE="$prof" STRATEGY_VARIANT="$var" "$ROOT/.venv/bin/python" -c \
    "from pathlib import Path; from engine.report import write; write(Path('$OUT/snapshot-$prof.json'))" 2>/dev/null
done
