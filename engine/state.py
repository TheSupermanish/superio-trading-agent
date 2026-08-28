"""SQLite journal.

Every proposal, every gate verdict, every order and every equity mark is
written here. The dashboard reads it, the write-up cites it, and the judges
can replay exactly why the agent did what it did.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from engine.config import SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS structures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    sleeve TEXT NOT NULL,
    underlying TEXT NOT NULL,
    kind TEXT NOT NULL,
    legs TEXT NOT NULL,
    qty INTEGER NOT NULL,
    net_price REAL NOT NULL,
    net_price_mid REAL,
    max_loss REAL NOT NULL,
    max_gain REAL NOT NULL,
    status TEXT NOT NULL,
    realized_pnl REAL,
    close_reason TEXT,
    client_order_id TEXT,
    thesis TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    agent TEXT NOT NULL,
    sleeve TEXT,
    underlying TEXT,
    proposal TEXT NOT NULL,
    verdict TEXT NOT NULL,
    reasons TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    structure_id INTEGER,
    client_order_id TEXT,
    broker_order_id TEXT,
    intent TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    fill_price REAL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS equity (
    ts TEXT PRIMARY KEY,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    buying_power REAL NOT NULL,
    open_risk REAL NOT NULL,
    day_pnl REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


_INITIALISED: set[Path] = set()


def _connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or SETTINGS.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # Each profile gets its own database file, so create the schema the first
    # time this process touches one rather than relying on a setup step.
    if db_path not in _INITIALISED:
        conn.executescript(SCHEMA)
        conn.commit()
        _INITIALISED.add(db_path)
    return conn


@contextmanager
def db(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = _connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path: Path | None = None) -> None:
    with db(path) as conn:
        conn.executescript(SCHEMA)


def log_event(kind: str, message: str, level: str = "info", data: Any = None) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO events (ts, level, kind, message, data) VALUES (?, ?, ?, ?, ?)",
            (utcnow(), level, kind, message, json.dumps(data, default=str) if data else None),
        )


def log_decision(
    agent: str,
    proposal: dict[str, Any],
    verdict: str,
    reasons: list[str],
    sleeve: str | None = None,
    underlying: str | None = None,
) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO decisions (ts, agent, sleeve, underlying, proposal, verdict, reasons)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                utcnow(),
                agent,
                sleeve,
                underlying,
                json.dumps(proposal, default=str),
                verdict,
                json.dumps(reasons),
            ),
        )
        return int(cur.lastrowid)


def record_order(
    intent: str,
    payload: dict[str, Any],
    status: str,
    structure_id: int | None = None,
    client_order_id: str | None = None,
    broker_order_id: str | None = None,
    fill_price: float | None = None,
    error: str | None = None,
) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO orders (ts, structure_id, client_order_id, broker_order_id, intent,"
            " payload, status, fill_price, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                utcnow(),
                structure_id,
                client_order_id,
                broker_order_id,
                intent,
                json.dumps(payload, default=str),
                status,
                fill_price,
                error,
            ),
        )
        return int(cur.lastrowid)


def open_structure(
    sleeve: str,
    underlying: str,
    kind: str,
    legs: list[dict[str, Any]],
    qty: int,
    net_price: float,
    net_price_mid: float,
    max_loss: float,
    max_gain: float,
    client_order_id: str,
    thesis: str = "",
) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO structures (opened_at, sleeve, underlying, kind, legs, qty, net_price,"
            " net_price_mid, max_loss, max_gain, status, client_order_id, thesis)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (
                utcnow(),
                sleeve,
                underlying,
                kind,
                json.dumps(legs, default=str),
                qty,
                net_price,
                net_price_mid,
                max_loss,
                max_gain,
                client_order_id,
                thesis,
            ),
        )
        return int(cur.lastrowid)


def set_structure_status(structure_id: int, status: str) -> None:
    with db() as conn:
        conn.execute("UPDATE structures SET status = ? WHERE id = ?", (status, structure_id))


def close_structure(structure_id: int, realized_pnl: float, reason: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE structures SET status='closed', closed_at=?, realized_pnl=?, close_reason=?"
            " WHERE id = ?",
            (utcnow(), realized_pnl, reason, structure_id),
        )


def live_structures() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM structures WHERE status IN ('pending', 'open') ORDER BY opened_at"
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["legs"] = json.loads(item["legs"])
        out.append(item)
    return out


def open_risk_total() -> float:
    with db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(max_loss), 0) AS total FROM structures"
            " WHERE status IN ('pending', 'open')"
        ).fetchone()
    return float(row["total"])


def open_risk_by(field: str, value: str) -> float:
    if field not in {"sleeve", "underlying"}:
        raise ValueError(f"unsupported field: {field}")
    with db() as conn:
        row = conn.execute(
            f"SELECT COALESCE(SUM(max_loss), 0) AS total FROM structures"
            f" WHERE status IN ('pending', 'open') AND {field} = ?",
            (value,),
        ).fetchone()
    return float(row["total"])


def trades_opened_today(include_simulated: bool | None = None) -> int:
    """Structures opened today.

    Simulated structures count while the agent is simulating, so a dry run
    respects the same daily budget a live session would. They stop counting the
    moment the agent arms, because rehearsing a trade never consumed anything.
    """
    if include_simulated is None:
        include_simulated = SETTINGS.dry_run
    today = datetime.now(timezone.utc).date().isoformat()
    clause = "" if include_simulated else " AND status != 'dry_run'"
    with db() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM structures WHERE substr(opened_at, 1, 10) = ?{clause}",
            (today,),
        ).fetchone()
    return int(row["n"])


def record_equity(
    equity_value: float, cash: float, buying_power: float, open_risk: float, day_pnl: float
) -> None:
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO equity (ts, equity, cash, buying_power, open_risk, day_pnl)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (utcnow(), equity_value, cash, buying_power, open_risk, day_pnl),
        )


def equity_curve(limit: int = 5000) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM equity ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def peak_equity() -> float | None:
    with db() as conn:
        row = conn.execute("SELECT MAX(equity) AS peak FROM equity").fetchone()
    return float(row["peak"]) if row and row["peak"] is not None else None
