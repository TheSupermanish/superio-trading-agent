#!/usr/bin/env bash
# Push the latest dashboard snapshot to GitHub.
#
# The deployed dashboard is a static export that fetches snapshot.json in the
# browser, so refreshing that one file is enough to keep the public page live.
# Committing it also leaves a tamper-evident history of what the agent believed
# at each point in time, which is exactly what an auditor wants.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

"$ROOT/scripts/snapshot.sh" >/dev/null 2>&1 || exit 0

if git diff --quiet -- dashboard/public/; then
  exit 0
fi

git add dashboard/public/
git -c user.email="manish.shivabhakti@d8s.co.jp" -c user.name="Superio Agent" \
  commit -q -m "chore(snapshot): $(date -u +%FT%TZ)"
git push -q origin main 2>/dev/null || echo "$(date -u +%FT%TZ) push failed" >> logs/publish.log
