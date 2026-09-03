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

from engine import premarket, state
from engine.calendar_gate import EVENTS, upcoming
from engine.config import SETTINGS
from engine.risk import GATES

import logging

log = logging.getLogger(__name__)


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

    # What the account has actually made, from the broker's own equity rather
    # than from this journal's arithmetic. The two differ whenever the journal
    # is younger than the account: the host holding it died mid-week, the agent
    # came up elsewhere and adopted the open book, and the closed trades from
    # before that were simply not in the new journal. Realized P&L then reads
    # zero on an account that is up over a percent, which is the dashboard
    # calling a winning week a losing one.
    #
    # Equity is not arguable, so it anchors the headline. Whatever equity has
    # gained that is not sitting in the open book has been realized, whether or
    # not this journal recorded the trade that did it.
    total_pnl = latest - start

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
        "total_pnl": round(total_pnl, 2),
        "realized_in_journal": round(sum(pnls), 2),
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


def closed_structures(limit: int = 200) -> list[dict[str, Any]]:
    """Every closed trade, leg by leg.

    The aggregate in performance() says how much was made. This says which
    trades made it, what they were, and why each one ended. A judge checking
    the P&L against Alpaca's own account history needs the second thing.
    """
    with state.db() as conn:
        rows = conn.execute(
            "SELECT * FROM structures WHERE status = 'closed'"
            " ORDER BY closed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        trade = dict(row)
        try:
            trade["legs"] = json.loads(trade["legs"])
        except (TypeError, ValueError):
            pass
        opened, closed = trade.get("opened_at"), trade.get("closed_at")
        trade["held_hours"] = _held_hours(opened, closed)
        max_loss = float(trade.get("max_loss") or 0.0)
        pnl = trade.get("realized_pnl")
        trade["return_on_risk"] = (
            round(float(pnl) / max_loss, 4) if pnl is not None and max_loss else None
        )
        out.append(trade)
    return out


def _held_hours(opened: str | None, closed: str | None) -> float | None:
    if not opened or not closed:
        return None
    try:
        delta = datetime.fromisoformat(closed) - datetime.fromisoformat(opened)
    except ValueError:
        return None
    return round(delta.total_seconds() / 3600, 2)


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


def _google_section() -> dict[str, Any]:
    """Connected accounts, their tasks, and the catalysts they contribute.

    Entirely optional. Any failure here degrades to an empty section rather
    than costing us the snapshot the dashboard depends on.
    """
    try:
        from engine import calendar_gate, google_accounts

        connected = google_accounts.status()
        if not connected:
            return {"connected": [], "tasks": [], "events": []}
        return {
            "connected": connected,
            "tasks": google_accounts.tasks()[:12],
            "events": [
                {
                    "name": e.name,
                    "when": e.when.isoformat(),
                    "impact": e.impact,
                    "affects": list(e.affects),
                }
                for e in calendar_gate.external_events()
            ],
        }
    except Exception:  # noqa: BLE001
        return {"connected": [], "tasks": [], "events": []}


def build() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    open_structures = state.live_structures()
    live = live_marks()

    return {
        "generated_at": now.isoformat(),
        "profile": SETTINGS.profile,
        "variant": SETTINGS.variant,
        "dry_run": SETTINGS.dry_run,
        "diary": SETTINGS.diary,
        "account_id": SETTINGS.account_id,
        "limits": {
            "max_risk_per_trade_pct": SETTINGS.risk.max_risk_per_trade_pct,
            "max_open_risk_pct": SETTINGS.risk.max_open_risk_pct,
            "max_convex_open_risk_pct": SETTINGS.risk.max_convex_open_risk_pct,
            "max_carry_open_risk_pct": SETTINGS.risk.max_carry_open_risk_pct,
            "max_carry_risk_per_trade_pct": SETTINGS.risk.max_carry_risk_per_trade_pct,
            "daily_loss_kill_pct": SETTINGS.risk.daily_loss_kill_pct,
            "total_drawdown_kill_pct": SETTINGS.risk.total_drawdown_kill_pct,
            "min_credit_to_width": SETTINGS.risk.min_credit_to_width,
            "max_debit_to_width": SETTINGS.risk.max_debit_to_width,
        },
        "performance": _with_broker_truth(performance(), live),
        "live": live,
        "budget": risk_budget(),
        "gates": gate_activity(),
        "open_structures": open_structures,
        "closed_structures": closed_structures(),
        "open_risk": round(state.open_risk_total(), 2),
        "equity_curve": state.equity_curve(limit=2000),
        "recent_decisions": _rows("decisions", 120),
        "recent_orders": _rows("orders", 60),
        "recent_events": _rows("events", 40),
        "session_plan": premarket.last_study(),
        "google": _google_section(),
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


def _with_broker_truth(perf: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Reconcile the journal's realized P&L against the broker's equity.

    Equity is not arguable. Whatever it has gained that is not sitting in the
    open book has been realized, whether or not this journal holds the trade
    that did it. When the two agree this changes nothing; when they do not, the
    gap is named rather than quietly showing the smaller number.
    """
    marks = live.get("marks") or []
    open_pnl = sum(float(m.get("unrealized_pnl") or 0.0) for m in marks)
    perf["open_pnl"] = round(open_pnl, 2)

    if not live.get("ok"):
        perf["realized_unrecorded"] = 0.0
        return perf

    implied = float(perf.get("total_pnl") or 0.0) - open_pnl
    recorded = float(perf.get("realized_in_journal") or 0.0)
    perf["realized_implied"] = round(implied, 2)
    perf["realized_unrecorded"] = round(implied - recorded, 2)
    return perf


def live_marks() -> dict[str, Any]:
    """What every open structure is worth right now, and how close it is to an exit.

    The journal says what was risked. It does not say that a position is eighty
    percent of the way to its profit target with four days left, or that spot
    is sitting a dollar under the short strike. That is the difference between
    a dashboard and a table, and the manager already computes all of it on
    every pass to decide whether to close anything.

    Broker calls, so it is allowed to fail: a dashboard without live marks is
    worse than one with them and far better than a snapshot that does not get
    written.
    """
    from engine import manager, marketdata

    out: dict[str, Any] = {"marks": [], "spots": {}, "ok": False}

    try:
        for symbol in SETTINGS.strategy.universe:
            try:
                out["spots"][symbol] = round(marketdata.underlying_price(symbol), 2)
            except Exception:  # noqa: BLE001
                continue

        marks = manager.mark_book()
    except Exception as exc:  # noqa: BLE001
        log.warning("live marks unavailable: %s", exc)
        return out

    structures = {int(s["id"]): s for s in state.live_structures()}
    for mark in marks:
        structure = structures.get(mark.structure_id)
        if structure is None:
            continue

        max_gain = float(structure.get("max_gain") or 0)
        max_loss = float(structure.get("max_loss") or 0)
        sleeve = str(structure.get("sleeve") or "core")
        p = SETTINGS.strategy

        # How far along the two exits this position is, as a fraction. The
        # dashboard draws these as progress, so they are clamped and the basis
        # is named rather than assumed.
        if sleeve == "carry":
            tp_target, sl_target = p.carry_profit_target, p.carry_stop_fraction
            tp_progress = (mark.unrealized_pnl / max_gain / tp_target) if max_gain and tp_target else 0.0
            sl_progress = (-mark.unrealized_pnl / max_loss / sl_target) if max_loss and sl_target else 0.0
            tp_basis = f"{tp_target:.0%} of max gain"
            sl_basis = f"{sl_target:.1f}x risk"
        elif mark.entry_price > 0:
            credit = mark.entry_price * 100 * mark.qty
            captured = (mark.unrealized_pnl / credit) if credit else 0.0
            tp_progress = captured / p.core_profit_target if p.core_profit_target else 0.0
            loss_multiple = (-mark.unrealized_pnl / credit) if credit else 0.0
            sl_progress = loss_multiple / (p.core_stop_multiple - 1) if p.core_stop_multiple > 1 else 0.0
            tp_basis = f"{p.core_profit_target:.0%} of credit"
            sl_basis = f"{p.core_stop_multiple:.1f}x credit"
        else:
            debit = abs(mark.entry_price) * 100 * mark.qty
            gain = (mark.unrealized_pnl / debit) if debit else 0.0
            tp_progress = gain / p.convex_profit_target if p.convex_profit_target else 0.0
            sl_progress = (-mark.unrealized_pnl / debit / 0.75) if debit else 0.0
            tp_basis = f"{p.convex_profit_target:.0%} on debit"
            sl_basis = "75% of debit lost"

        legs = structure.get("legs") or []
        shorts = [leg for leg in legs if leg.get("side") == "sell"]
        spot = out["spots"].get(mark.underlying)

        # Distance to the nearest short strike, which is the level that decides
        # whether this position is comfortable or not.
        nearest = None
        if spot is not None and shorts:
            nearest = min(shorts, key=lambda leg: abs(float(leg["strike"]) - spot))

        out["marks"].append({
            "structure_id": mark.structure_id,
            "underlying": mark.underlying,
            "kind": mark.kind,
            "sleeve": mark.sleeve,
            "qty": mark.qty,
            "entry_price": round(mark.entry_price, 2),
            "current_price": round(mark.current_price, 2),
            "unrealized_pnl": round(mark.unrealized_pnl, 2),
            "pct_of_max_gain": round(mark.pct_of_max_gain, 4),
            "dte": mark.dte,
            "action": mark.action,
            "rationale": mark.rationale,
            "max_loss": max_loss,
            "max_gain": max_gain,
            "spot": spot,
            "short_strike": float(nearest["strike"]) if nearest else None,
            "distance_pct": (
                round((float(nearest["strike"]) - spot) / spot, 4)
                if nearest and spot else None
            ),
            "tp_progress": round(max(min(tp_progress, 1.5), 0.0), 4),
            "sl_progress": round(max(min(sl_progress, 1.5), 0.0), 4),
            "tp_basis": tp_basis,
            "sl_basis": sl_basis,
        })

    out["ok"] = True
    return out


def risk_budget() -> dict[str, Any]:
    """Deployed risk against every cap that binds, sleeve by sleeve.

    The single "open risk" figure hid the finding that mattered all week: the
    income sleeve had no cap of its own and filled the budget first, so the
    sleeves that pay a multiple of what they risk were competing for scraps.
    A dashboard that shows one number cannot show that.
    """
    r = SETTINGS.risk
    equity = 0.0
    curve = state.equity_curve(limit=1)
    if curve:
        equity = float(curve[0]["equity"])

    sleeves = []
    for name, cap in (
        ("core", r.max_core_open_risk_pct),
        ("convex", r.max_convex_open_risk_pct),
        ("carry", r.max_carry_open_risk_pct),
    ):
        used = state.open_risk_by("sleeve", name)
        sleeves.append({
            "sleeve": name,
            "used": round(used, 2),
            "cap_pct": cap,
            "cap": round(equity * cap, 2),
            "used_pct_of_equity": round(used / equity, 5) if equity else 0.0,
            "utilisation": round(used / (equity * cap), 4) if equity and cap else None,
        })

    total_used = state.open_risk_total()
    return {
        "equity": round(equity, 2),
        "sleeves": sleeves,
        "total_used": round(total_used, 2),
        "total_cap": round(equity * r.max_open_risk_pct, 2),
        "total_cap_pct": r.max_open_risk_pct,
        "total_utilisation": (
            round(total_used / (equity * r.max_open_risk_pct), 4)
            if equity and r.max_open_risk_pct else None
        ),
        "daily_kill_pct": r.daily_loss_kill_pct,
        "drawdown_kill_pct": r.total_drawdown_kill_pct,
        "max_new_trades_per_day": r.max_new_trades_per_day,
        "max_open_structures": r.max_open_structures,
        "trades_today": state.trades_opened_today(),
        "failed_today": state.failed_entries_today(),
    }


def chart_data(sessions: int = 120) -> dict[str, Any]:
    """Candles per underlying, plus every structure placed on that timeline.

    The dashboard needs to answer "why there" and not just "how much", so each
    structure carries the levels that actually matter on a price chart. For an
    option spread those are the strikes: the short strike is where the
    position starts losing, the long strike is where the loss stops, and the
    breakeven sits between them. A take-profit on a spread is a premium level
    rather than a price level, so it is reported in premium terms and labelled
    as such instead of being drawn as a line that would be a lie.
    """
    from engine import marketdata

    out: dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat()}
    symbols = SETTINGS.strategy.universe

    bars: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        try:
            bars[symbol] = marketdata.daily_bars(symbol, days=sessions)
        except Exception as exc:  # noqa: BLE001 - a missing chart must not break the snapshot
            log.warning("bars failed for %s: %s", symbol, exc)
            bars[symbol] = []
    out["bars"] = bars

    with state.db() as conn:
        rows = conn.execute(
            "SELECT * FROM structures WHERE status IN ('open', 'closed', 'dry_run')"
            " ORDER BY opened_at ASC"
        ).fetchall()
        decisions = conn.execute(
            "SELECT ts, underlying, verdict, reasons, proposal FROM decisions"
            " WHERE agent != 'manager' AND verdict = 'approve'"
            " ORDER BY id ASC"
        ).fetchall()

    # The gate trail that approved a structure is not linked to it by id, so it
    # is matched on underlying and the nearest approval at or before the open.
    # Approximate by construction, and labelled that way in the UI rather than
    # presented as a hard join.
    approvals: dict[str, list[tuple[str, str]]] = {}
    for row in decisions:
        approvals.setdefault(str(row["underlying"]), []).append(
            (str(row["ts"]), str(row["reasons"]))
        )

    trades: list[dict[str, Any]] = []
    for row in rows:
        trade = dict(row)
        try:
            trade["legs"] = json.loads(trade["legs"] or "[]")
        except (TypeError, ValueError):
            trade["legs"] = []

        shorts = [leg for leg in trade["legs"] if leg.get("side") == "sell"]
        longs = [leg for leg in trade["legs"] if leg.get("side") == "buy"]
        strikes = [float(leg["strike"]) for leg in trade["legs"] if leg.get("strike")]

        net = float(trade.get("net_price") or 0)
        qty = int(trade.get("qty") or 1)
        max_loss = float(trade.get("max_loss") or 0)
        max_gain = float(trade.get("max_gain") or 0)
        sleeve = str(trade.get("sleeve") or "core")
        p = SETTINGS.strategy

        # Exit levels, in the units the manager actually compares against.
        if sleeve == "carry":
            take_profit = {
                "basis": "fraction of maximum gain",
                "target_pct": p.carry_profit_target,
                "target_value": round(max_gain * p.carry_profit_target, 2),
            }
            stop = {
                "basis": "multiple of the risk underwritten",
                "target_pct": p.carry_stop_fraction,
                "target_value": round(-max_loss * p.carry_stop_fraction, 2),
            }
        elif net > 0:
            take_profit = {
                "basis": "fraction of the credit captured",
                "target_pct": p.core_profit_target,
                "target_value": round(net * 100 * qty * p.core_profit_target, 2),
            }
            stop = {
                "basis": "multiple of the credit received",
                "target_pct": p.core_stop_multiple,
                "target_value": round(-net * 100 * qty * (p.core_stop_multiple - 1), 2),
            }
        else:
            debit = abs(net) * 100 * qty
            take_profit = {
                "basis": "gain on the debit paid",
                "target_pct": p.convex_profit_target,
                "target_value": round(debit * p.convex_profit_target, 2),
            }
            stop = {
                "basis": "share of the debit remaining",
                "target_pct": 0.25,
                "target_value": round(-debit * 0.75, 2),
            }

        # Breakeven on the underlying, which is a real price level.
        breakeven = None
        if shorts and len(trade["legs"]) == 2:
            short_strike = float(shorts[0]["strike"])
            is_call = bool(shorts[0].get("is_call"))
            breakeven = round(
                short_strike + net if is_call else short_strike - net, 2
            )

        gates = ""
        opened = str(trade.get("opened_at") or "")
        for ts, reasons in approvals.get(str(trade.get("underlying")), []):
            if ts <= opened:
                gates = reasons
        trade["gates"] = gates

        trade["levels"] = {
            "short_strikes": sorted(float(leg["strike"]) for leg in shorts),
            "long_strikes": sorted(float(leg["strike"]) for leg in longs),
            "min_strike": min(strikes) if strikes else None,
            "max_strike": max(strikes) if strikes else None,
            "breakeven": breakeven,
        }
        trade["take_profit"] = take_profit
        trade["stop"] = stop
        trade["held_hours"] = _held_hours(
            trade.get("opened_at"), trade.get("closed_at")
        )
        trades.append(trade)

    out["trades"] = trades
    out["exit_rules"] = {
        "core_profit_target": SETTINGS.strategy.core_profit_target,
        "core_stop_multiple": SETTINGS.strategy.core_stop_multiple,
        "convex_profit_target": SETTINGS.strategy.convex_profit_target,
        "carry_profit_target": SETTINGS.strategy.carry_profit_target,
        "carry_stop_fraction": SETTINGS.strategy.carry_stop_fraction,
        "carry_min_hold_dte": SETTINGS.strategy.carry_min_hold_dte,
    }
    return out


def write_chart(path: Path | None = None) -> Path:
    """Publish the chart payload beside the snapshot."""
    target = path or (
        Path(__file__).resolve().parent.parent
        / "dashboard"
        / "public"
        / ("chart.json" if SETTINGS.profile == "main" else f"chart-{SETTINGS.profile}.json")
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(chart_data(), indent=2, default=str))
    return target


def write(path: Path | None = None) -> Path:
    """Write the snapshot the dashboard reads."""
    target = path or (
        Path(__file__).resolve().parent.parent
        / "dashboard"
        / "public"
        / (
            "snapshot.json"
            if SETTINGS.profile == "main"
            else f"snapshot-{SETTINGS.profile}.json"
        )
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build(), indent=2, default=str))
    return target


if __name__ == "__main__":
    written = write()
    data = json.loads(written.read_text())
    print(f"wrote {written}")
    print(json.dumps(data["performance"], indent=2))
    print(json.dumps(data["gates"], indent=2))
