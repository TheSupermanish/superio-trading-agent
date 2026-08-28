#!/usr/bin/env bash
# Supervisor for one trading profile.
#
# Runs the loop continuously. The loop itself is a no-op when the market is
# closed, so this can stay resident all week; it costs one clock call per pass.
#
# Usage: scripts/run.sh [profile] [variant] [interval_seconds]
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PROFILE="${1:-main}"
VARIANT="${2:-barbell}"
INTERVAL="${3:-180}"
LOG="$ROOT/logs/${PROFILE}.log"

mkdir -p "$ROOT/logs"

export ALPACA_PROFILE="$PROFILE"
export STRATEGY_VARIANT="$VARIANT"

echo "$(date -u +%FT%TZ) starting profile=$PROFILE variant=$VARIANT interval=${INTERVAL}s" >> "$LOG"

# Restart on crash rather than dying silently mid-week.
while true; do
  "$ROOT/.venv/bin/python" -m engine.loop --interval "$INTERVAL" >> "$LOG" 2>&1
  code=$?
  echo "$(date -u +%FT%TZ) loop exited with code $code, restarting in 30s" >> "$LOG"
  sleep 30
done
