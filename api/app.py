"""Fleet API.

One endpoint surface over every book the engine runs: the three paper accounts
and the diary presets that shadow them.

It reads the snapshot each agent writes, not the SQLite journals directly. That
was the first design and it hung: these are live WAL databases with a writer
attached, and opening one read-only from a second process needs the -shm
sidecar, which is exactly the kind of cross-process file-locking problem that
fails by blocking rather than by erroring. Every agent endpoint stalled while
/api/health, which touches no database, stayed green.

So the writer publishes and this only reads. The cost is that a number can be
up to a snapshot interval old, which is stated in every response rather than
hidden. The gain is that a monitor cannot contend with, block, or corrupt the
thing it is monitoring.

Read-only and broker-free by construction: no Alpaca call, no order path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "dashboard" / "out"


@dataclass(frozen=True)
class Agent:
    id: str
    label: str
    variant: str
    live: bool
    snapshot: str

    @property
    def stake(self) -> float:
        return 100_000.0 if self.live else 50_000.0

    @property
    def path(self) -> Path:
        return STATIC / self.snapshot


AGENTS: tuple[Agent, ...] = (
    Agent("main", "Main", "barbell", True, "snapshot.json"),
    Agent("test2", "Test 2", "convex_tilt", True, "snapshot-test2.json"),
    Agent("test3", "Test 3", "income_only", True, "snapshot-test3.json"),
    Agent("levered", "Levered", "levered", False, "snapshot-diary-levered.json"),
    Agent("vrp_router", "VRP router", "vrp_router", False, "snapshot-diary-vrp_router.json"),
    Agent("fat_credit", "Fat credit", "fat_credit", False, "snapshot-diary-fat_credit.json"),
    Agent("long_gamma", "Long gamma", "long_gamma", False, "snapshot-diary-long_gamma.json"),
)

BY_ID = {a.id: a for a in AGENTS}

app = Flask(__name__, static_folder=None)


def load(agent: Agent) -> dict[str, Any] | None:
    """Read one agent's published snapshot.

    A half-written file is a normal thing to catch here: the writer replaces it
    on a timer, so a read can land mid-write. Returning None means the caller
    reports the book as not yet published and shows the other six.
    """
    try:
        return json.loads(agent.path.read_text())
    except (OSError, ValueError):
        return None


def _age_seconds(generated_at: str | None) -> float | None:
    if not generated_at:
        return None
    try:
        stamp = datetime.fromisoformat(generated_at)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - stamp).total_seconds(), 1)


def _held_hours(opened: str | None, closed: str | None) -> float | None:
    if not opened or not closed:
        return None
    try:
        delta = datetime.fromisoformat(closed) - datetime.fromisoformat(opened)
    except ValueError:
        return None
    return round(delta.total_seconds() / 3600, 2)


def _enrich_closed(trade: dict[str, Any]) -> dict[str, Any]:
    """Fill in the two derived columns if the snapshot predates them."""
    if trade.get("held_hours") is None:
        trade["held_hours"] = _held_hours(trade.get("opened_at"), trade.get("closed_at"))
    if trade.get("return_on_risk") is None:
        max_loss = float(trade.get("max_loss") or 0)
        pnl = trade.get("realized_pnl")
        trade["return_on_risk"] = (
            round(float(pnl) / max_loss, 4) if pnl is not None and max_loss else None
        )
    return trade


def summarise(agent: Agent) -> dict[str, Any]:
    base = {
        "id": agent.id,
        "label": agent.label,
        "variant": agent.variant,
        "live": agent.live,
        "stake": agent.stake,
    }
    snap = load(agent)
    if snap is None:
        return {**base, "status": "not published yet", "performance": None}

    open_book = snap.get("open_structures") or []
    return {
        **base,
        "status": "running",
        "performance": snap.get("performance"),
        "open_structures": len(open_book),
        "open_risk": snap.get("open_risk", 0.0),
        "gates": snap.get("gates"),
        "dry_run": snap.get("dry_run"),
        "diary": snap.get("diary"),
        "generated_at": snap.get("generated_at"),
        "age_seconds": _age_seconds(snap.get("generated_at")),
    }


@app.after_request
def no_store(response):
    # A monitor that caches is a monitor that lies.
    if response.mimetype == "application/json":
        response.headers["Cache-Control"] = "no-store"
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "now": datetime.now(timezone.utc).isoformat(),
        "agents": len(AGENTS),
        "published": sum(1 for a in AGENTS if a.path.exists()),
    })


@app.get("/api/agents")
def list_agents():
    return jsonify({"agents": [summarise(a) for a in AGENTS]})


@app.get("/api/agents/<agent_id>")
def agent_detail(agent_id: str):
    agent = BY_ID.get(agent_id)
    if agent is None:
        return jsonify({"error": f"unknown agent {agent_id}"}), 404

    summary = summarise(agent)
    snap = load(agent)
    if snap is None:
        return jsonify({**summary, "open": [], "closed": [], "equity_curve": [],
                        "decisions": [], "events": []})

    return jsonify({
        **summary,
        "open": snap.get("open_structures") or [],
        "closed": [_enrich_closed(t) for t in (snap.get("closed_structures") or [])],
        "equity_curve": snap.get("equity_curve") or [],
        "decisions": snap.get("recent_decisions") or [],
        "events": snap.get("recent_events") or [],
        "orders": snap.get("recent_orders") or [],
        "session_plan": snap.get("session_plan"),
        "upcoming": snap.get("upcoming") or [],
    })


@app.get("/api/agents/<agent_id>/<collection>")
def agent_collection(agent_id: str, collection: str):
    keys = {
        "trades": "closed_structures",
        "open": "open_structures",
        "decisions": "recent_decisions",
        "events": "recent_events",
        "orders": "recent_orders",
    }
    if collection not in keys:
        return jsonify({"error": f"unknown collection {collection}"}), 404
    agent = BY_ID.get(agent_id)
    if agent is None:
        return jsonify({"error": f"unknown agent {agent_id}"}), 404
    snap = load(agent) or {}
    return jsonify({collection: snap.get(keys[collection]) or []})


# --- static site ------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/<path:asset>")
def static_asset(asset: str):
    """Serve the exported site.

    Next's export writes a route as `<name>.html` and also leaves a `<name>/`
    directory holding its RSC payload files but no index. Checking the
    directory first therefore 404s exactly the routes it is meant to serve, so
    the sibling .html is tried before anything else.
    """
    for candidate in (asset, f"{asset}.html", f"{asset}/index.html"):
        if (STATIC / candidate).is_file():
            return send_from_directory(STATIC, candidate)
    if asset.endswith(".json"):
        return jsonify({"error": f"no such file {asset}"}), 404
    return send_from_directory(STATIC, "404.html"), 404


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, threaded=True)
