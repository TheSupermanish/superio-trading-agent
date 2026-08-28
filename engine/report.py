"""Snapshot builder.

Turns the journal into the numbers a judge asks for: realized P&L, win rate,
profit factor, maximum drawdown, and the decision trail behind them. The
dashboard renders this; the write-up quotes it.

Rejected proposals are included on purpose. A trading agent that shows only its
trades is showing half its behaviour, and the half it refused to do is the half
that explains the risk gates.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine import state
from engine.calendar_gate import EVENTS, upcoming
from engine.config import SETTINGS
from engine.risk import GATES


def _rows(table: str, limit: int, order: str = "DESC") -> list[dict[str, Any]]:
    with state.db() as conn:
        rows = conn.execute(
            f"SELECT * FROM {table} ORDER BY id {order} LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def performance() -> dict[str, Any]:
    """Realized performance from closed structures, plus the equity path."""
    with state.db() as conn:
        closed = conn.execute(
            "SELECT realized_pnl, close_reason, sleeve, kind, underlying, opened_at, closed_at"
            " FROM structures WHERE status = 'closed' AND realized_pnl IS NOT NULL"
        ).fetchall()

    pnls = [float(r["realized_pnl"]) for r in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    curve = state.equity_curve()
    equity_values = [float(p["equity"]) for p in curve]

    peak, max_dd = 0.0, 0.0
    for value in equity_values:
        peak = max(peak, value)
        if peak:
            max_dd = max(max_dd, (peak - value) / peak)

    start = equity_values[0] if equity_values else 0.0
    latest = equity_values[-1] if equity_values else 0.0

    return {
        "trades_closed": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "realized_pnl": round(sum(pnls), 2),
        "avg_win": round(gross_win / len(wins), 2) if wins else None,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "max_drawdown_pct": round(max_dd, 4),
        "equity_start": round(start, 2),
        "equity_latest": round(latest, 2),
        "return_pct": round((latest / start - 1), 4) if start else None,
        "by_sleeve": _group(closed, "sleeve"),
        "by_kind": _group(closed, "kind"),
        "by_underlying": _group(closed, "underlying"),
    }


def _group(rows: list[Any], field: str) -> dict[str, Any]:
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        key = str(row[field])
        bucket = out.setdefault(key, {"n": 0, "pnl": 0.0, "wins": 0})
        bucket["n"] += 1
        pnl = float(row["realized_pnl"])
        bucket["pnl"] += pnl
        bucket["wins"] += 1 if pnl > 0 else 0
    for bucket in out.values():
        bucket["pnl"] = round(bucket["pnl"], 2)
    return out


def gate_activity(limit: int = 400) -> dict[str, Any]:
    """How often each gate refused something. The risk officer's own report card."""
    with state.db() as conn:
        rows = conn.execute(
            "SELECT verdict, reasons FROM decisions WHERE agent = 'risk_officer'"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    counts = {code: 0 for code, _ in GATES}
    approved = 0
    for row in rows:
        if row["verdict"] == "approved":
            approved += 1
            continue
        reasons = json.loads(row["reasons"])
        if not reasons:
            continue
        code = str(reasons[0]).split(" ", 1)[0]
        if code in counts:
            counts[code] += 1

    return {
        "considered": len(rows),
        "approved": approved,
        "rejected": len(rows) - approved,
        "rejections_by_gate": [
            {"gate": code, "name": name, "count": counts[code]} for code, name in GATES
        ],
    }


def build() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    open_structures = state.live_structures()

    return {
        "generated_at": now.isoformat(),
        "profile": SETTINGS.profile,
        "variant": SETTINGS.variant,
        "dry_run": SETTINGS.dry_run,
        "account_id": SETTINGS.account_id,
        "limits": {
            "max_risk_per_trade_pct": SETTINGS.risk.max_risk_per_trade_pct,
            "max_open_risk_pct": SETTINGS.risk.max_open_risk_pct,
            "max_convex_open_risk_pct": SETTINGS.risk.max_convex_open_risk_pct,
            "daily_loss_kill_pct": SETTINGS.risk.daily_loss_kill_pct,
            "total_drawdown_kill_pct": SETTINGS.risk.total_drawdown_kill_pct,
            "min_credit_to_width": SETTINGS.risk.min_credit_to_width,
            "max_debit_to_width": SETTINGS.risk.max_debit_to_width,
        },
        "performance": performance(),
        "gates": gate_activity(),
        "open_structures": open_structures,
        "open_risk": round(state.open_risk_total(), 2),
        "equity_curve": state.equity_curve(limit=2000),
        "recent_decisions": _rows("decisions", 120),
        "recent_orders": _rows("orders", 60),
        "recent_events": _rows("events", 40),
        "calendar": [
            {
                "name": e.name,
                "when": e.when.isoformat(),
                "impact": e.impact,
                "affects": list(e.affects),
            }
            for e in EVENTS
        ],
        "upcoming": [
            {"name": e.name, "when": e.when.isoformat(), "impact": e.impact}
            for e in upcoming(horizon_hours=168)
        ],
    }


def write(path: Path | None = None) -> Path:
    """Write the snapshot the dashboard reads."""
    target = path or (Path(__file__).resolve().parent.parent / "dashboard" / "public" / "snapshot.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build(), indent=2, default=str))
    return target


if __name__ == "__main__":
    written = write()
    data = json.loads(written.read_text())
    print(f"wrote {written}")
    print(json.dumps(data["performance"], indent=2))
    print(json.dumps(data["gates"], indent=2))
