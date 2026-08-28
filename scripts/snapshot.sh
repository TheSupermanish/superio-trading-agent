#!/usr/bin/env bash
# Regenerate the dashboard snapshot for every profile.
# The competition profile writes dashboard/public/snapshot.json; the others
# write alongside it so the dashboard can compare variants.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ALPACA_PROFILE=main  STRATEGY_VARIANT=barbell     .venv/bin/python -c \
  "from engine.report import write; print(write())"
for pair in test2:convex_tilt test3:income_only; do
  prof="${pair%%:*}"; var="${pair##*:}"
  ALPACA_PROFILE="$prof" STRATEGY_VARIANT="$var" .venv/bin/python -c \
    "from pathlib import Path; from engine.report import write; \
     print(write(Path('dashboard/public/snapshot-$prof.json')))"
done
