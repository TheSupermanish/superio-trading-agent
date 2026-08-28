#!/usr/bin/env bash
# Refresh and publish the snapshot on an interval.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INTERVAL="${1:-600}"
while true; do
  "$ROOT/scripts/publish.sh" >> "$ROOT/logs/publish.log" 2>&1
  sleep "$INTERVAL"
done
