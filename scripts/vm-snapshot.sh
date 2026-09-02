#!/usr/bin/env bash
# Publish every book's snapshot into the directory the dashboard and API serve.
#
# One Python process writes all seven. It used to be one process per book,
# which meant seven interpreter starts a minute, each loading the whole engine,
# on a box that also runs the agents themselves. That is a lot of memory churn
# to produce a few hundred kilobytes of JSON.
set -uo pipefail
ROOT=/opt/superio
cd "$ROOT"
OUT="$ROOT/dashboard/out"
mkdir -p "$OUT"

"$ROOT/.venv/bin/python" - "$OUT" <<'PY'
import importlib
import os
import sys
from pathlib import Path

out = Path(sys.argv[1])

# profile, variant, filename
BOOKS = [
    ("main", "barbell", "snapshot.json"),
    ("test2", "convex_tilt", "snapshot-test2.json"),
    ("test3", "income_only", "snapshot-test3.json"),
    ("main", "levered", "snapshot-diary-levered.json"),
    ("main", "vrp_router", "snapshot-diary-vrp_router.json"),
    ("main", "fat_credit", "snapshot-diary-fat_credit.json"),
    ("main", "long_gamma", "snapshot-diary-long_gamma.json"),
]

for profile, variant, filename in BOOKS:
    os.environ["ALPACA_PROFILE"] = profile
    os.environ["STRATEGY_VARIANT"] = variant
    # Settings are resolved at import time and every module holds the same
    # instance, so switching books means reloading config and everything that
    # captured it. Cheaper than seven interpreters, and the ordering matters:
    # config first, then whatever reads it.
    import engine.config
    importlib.reload(engine.config)
    for name in ("engine.state", "engine.premarket", "engine.report"):
        module = importlib.import_module(name)
        importlib.reload(module)
    import engine.report

    try:
        engine.report.write(out / filename)
        # The chart payload is per-account and only the judged one is charted.
        if filename == "snapshot.json":
            engine.report.write_chart(out / "chart.json")
    except Exception as exc:  # noqa: BLE001 - one bad book must not stop the rest
        print(f"{filename}: {exc}", file=sys.stderr)
PY
