"""Fleet API.

One endpoint surface over every book the engine runs: the three paper accounts
and the diary presets that shadow them. It reads the journals directly rather
than the per-agent snapshot files, so a number here is never stale by up to a
minute and never depends on the snapshot timer having run.

Deliberately read-only and broker-free. Nothing in this process can place,
cancel or close an order, and it makes no call to Alpaca, so a monitor left
open in a browser tab cannot cost anything or hold up a trading pass.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATIC = ROOT / "dashboard" / "out"


@dataclass(frozen=True)
class Agent:
    id: str
    label: str
    variant: str
    live: bool
    db: Path

    @property
    def stake(self) -> float:
        return 100_000.0 if self.live else 50_000.0


AGENTS: tuple[Agent, ...] = (
    Agent("main", "Main", "barbell", True, DATA / "superio-main.db"),
    Agent("test2", "Test 2", "convex_tilt", True, DATA / "superio-test2.db"),
    Agent("test3", "Test 3", "income_only", True, DATA / "superio-test3.db"),
    Agent("levered", "Levered", "levered", False, DATA / "diary-levered.db"),
    Agent("vrp_router", "VRP router", "vrp_router", False, DATA / "diary-vrp_router.db"),
    Agent("fat_credit", "Fat credit", "fat_credit", False, DATA / "diary-fat_credit.db"),
    Agent("long_gamma", "Long gamma", "long_gamma", False, DATA / "diary-long_gamma.db"),
)

BY_ID = {a.id: a for a in AGENTS}

#: Journal states that mean the book is carrying the structure. A diary book
#: has no broker position, so its simulated entries are its open book.
LIVE_STATES = ("pending", "open", "dry_run")

app = Flask(__name__, static_folder=None)


#: Long enough to ride out a writer committing a pass, short enough that a
#: wedged journal cannot hold the whole listing open.
BUSY_TIMEOUT_S = 3.0


def connect(agent: Agent) -> sqlite3.Connection | None:
    """Open the journal read-only, or give up quickly.

    These are live SQLite files with a writer attached, in WAL mode, so a
    reader needs to touch the -shm sidecar. If that file is not readable by
    this process, or the journal is mid-recovery, the open fails: the monitor
    reports the book as unreadable and moves on. One unhappy journal must never
    be able to stall the view of the other six.
    """
    if not agent.db.exists():
        return None
    try:
        conn = sqlite3.connect(
            f"file:{agent.db}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_S
        )
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _decode_legs(structure: dict[str, Any]) -> dict[str, Any]:
    try:
        structure["legs"] = json.loads(structure.get("legs") or "[]")
    except (TypeError, ValueError):
        structure["legs"] = []
    return structure


def _held_hours(opened: str | None, closed: str | None) -> float | None:
    if not opened or not closed:
        return None
    try:
        delta = datetime.fromisoformat(closed) - datetime.fromisoformat(opened)
    except ValueError:
        return None
    return round(delta.total_seconds() / 3600, 2)


def performance(conn: sqlite3.Connection, stake: float) -> dict[str, Any]:
    closed = rows(
        conn,
        "SELECT realized_pnl, sleeve, kind, underlying FROM structures"
        " WHERE status = 'closed' AND realized_pnl IS NOT NULL",
    )
    pnls = [float(r["realized_pnl"]) for r in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win, gross_loss = sum(wins), abs(sum(losses))

    curve = rows(conn, "SELECT ts, equity FROM equity ORDER BY ts ASC LIMIT 20000")
    values = [float(p["equity"]) for p in curve]
    peak, drawdown = 0.0, 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            drawdown = max(drawdown, (peak - value) / peak)

    start = values[0] if values else stake
    latest = values[-1] if values else stake

    groups: dict[str, dict[str, dict[str, float]]] = {}
    for field in ("sleeve", "kind", "underlying"):
        bucket: dict[str, dict[str, float]] = {}
        for row in closed:
            key = str(row[field])
            entry = bucket.setdefault(key, {"n": 0, "pnl": 0.0, "wins": 0})
            entry["n"] += 1
            entry["pnl"] += float(row["realized_pnl"])
            entry["wins"] += 1 if float(row["realized_pnl"]) > 0 else 0
        for entry in bucket.values():
            entry["pnl"] = round(entry["pnl"], 2)
        groups[field] = bucket

    return {
        "trades_closed": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "realized_pnl": round(sum(pnls), 2),
        "avg_win": round(gross_win / len(wins), 2) if wins else None,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "max_drawdown_pct": round(drawdown, 4),
        "equity_start": round(start, 2),
        "equity_latest": round(latest, 2),
        "return_pct": round(latest / start - 1, 4) if start else None,
        "by_sleeve": groups["sleeve"],
        "by_kind": groups["kind"],
        "by_underlying": groups["underlying"],
    }


def gate_activity(conn: sqlite3.Connection, limit: int = 400) -> dict[str, Any]:
    """How often each gate refused something.

    The refusals matter as much as the trades: an agent that shows only what it
    did is showing half its behaviour.
    """
    decisions = rows(
        conn,
        "SELECT verdict, reasons FROM decisions WHERE agent != 'manager'"
        " ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    approved = sum(1 for d in decisions if d["verdict"] == "approve")
    counts: dict[str, int] = {}
    for decision in decisions:
        if decision["verdict"] == "approve":
            continue
        text = str(decision["reasons"] or "")
        for gate in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"):
            if f"{gate} " in text or f"{gate}:" in text:
                counts[gate] = counts.get(gate, 0) + 1
                break
    return {
        "considered": len(decisions),
        "approved": approved,
        "rejected": len(decisions) - approved,
        "rejections_by_gate": [
            {"gate": g, "count": c} for g, c in sorted(counts.items())
        ],
    }


def summarise(agent: Agent) -> dict[str, Any]:
    base = {
        "id": agent.id,
        "label": agent.label,
        "variant": agent.variant,
        "live": agent.live,
        "stake": agent.stake,
    }
    conn = connect(agent)
    if conn is None:
        status = "no journal yet" if not agent.db.exists() else "journal unreadable"
        return {**base, "status": status, "performance": None}
    try:
        marks = ",".join("?" for _ in LIVE_STATES)
        open_row = conn.execute(
            f"SELECT COUNT(*) AS n, COALESCE(SUM(max_loss), 0) AS risk"
            f" FROM structures WHERE status IN ({marks})",
            LIVE_STATES,
        ).fetchone()
        last = conn.execute(
            "SELECT ts FROM events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            **base,
            "status": "running",
            "performance": performance(conn, agent.stake),
            "open_structures": int(open_row["n"]),
            "open_risk": round(float(open_row["risk"]), 2),
            "gates": gate_activity(conn, limit=200),
            "last_seen": last["ts"] if last else None,
        }
    except sqlite3.Error as exc:
        # A read that fails is a fact about one book, not a reason to fail the
        # request that was asked about all of them.
        return {**base, "status": f"read failed: {exc}", "performance": None}
    finally:
        conn.close()


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
        "journals_present": sum(1 for a in AGENTS if a.db.exists()),
    })


@app.get("/api/agents")
def list_agents():
    return jsonify({"agents": [summarise(a) for a in AGENTS]})


@app.get("/api/agents/<agent_id>")
def agent_detail(agent_id: str):
    agent = BY_ID.get(agent_id)
    if agent is None:
        return jsonify({"error": f"unknown agent {agent_id}"}), 404
    conn = connect(agent)
    if conn is None:
        return jsonify({**summarise(agent), "open": [], "closed": []})
    try:
        marks = ",".join("?" for _ in LIVE_STATES)
        open_book = [
            _decode_legs(s)
            for s in rows(
                conn,
                f"SELECT * FROM structures WHERE status IN ({marks})"
                " ORDER BY opened_at DESC",
                LIVE_STATES,
            )
        ]
        closed = []
        for structure in rows(
            conn,
            "SELECT * FROM structures WHERE status = 'closed'"
            " ORDER BY closed_at DESC LIMIT 200",
        ):
            _decode_legs(structure)
            structure["held_hours"] = _held_hours(
                structure.get("opened_at"), structure.get("closed_at")
            )
            max_loss = float(structure.get("max_loss") or 0)
            pnl = structure.get("realized_pnl")
            structure["return_on_risk"] = (
                round(float(pnl) / max_loss, 4) if pnl is not None and max_loss else None
            )
            closed.append(structure)

        return jsonify({
            **summarise(agent),
            "open": open_book,
            "closed": closed,
            "equity_curve": rows(
                conn, "SELECT * FROM equity ORDER BY ts ASC LIMIT 5000"
            ),
            "decisions": rows(
                conn, "SELECT * FROM decisions ORDER BY id DESC LIMIT 120"
            ),
            "events": rows(conn, "SELECT * FROM events ORDER BY id DESC LIMIT 60"),
            "orders": rows(conn, "SELECT * FROM orders ORDER BY id DESC LIMIT 60"),
        })
    finally:
        conn.close()


@app.get("/api/agents/<agent_id>/<table>")
def agent_table(agent_id: str, table: str):
    if table not in {"trades", "decisions", "events", "orders"}:
        return jsonify({"error": f"unknown collection {table}"}), 404
    agent = BY_ID.get(agent_id)
    if agent is None:
        return jsonify({"error": f"unknown agent {agent_id}"}), 404
    conn = connect(agent)
    if conn is None:
        return jsonify({table: []})
    try:
        if table == "trades":
            data = [
                _decode_legs(s)
                for s in rows(
                    conn, "SELECT * FROM structures ORDER BY opened_at DESC LIMIT 300"
                )
            ]
        else:
            data = rows(conn, f"SELECT * FROM {table} ORDER BY id DESC LIMIT 300")
        return jsonify({table: data})
    finally:
        conn.close()


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
    candidates = [asset, f"{asset}.html", f"{asset}/index.html"]
    for candidate in candidates:
        target = STATIC / candidate
        if target.is_file():
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
