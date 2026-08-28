"""Position manager.

Marks every open structure to market, then applies exit rules. Exits are
deterministic: a profit target, a stop expressed as a multiple of the credit
received, and a time stop that flattens anything close to expiry so the account
never carries assignment risk into the close.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from engine import closing, state
from engine.config import SETTINGS
from engine.marketdata import latest_mids
from engine.types import CONTRACT_MULTIPLIER

log = logging.getLogger(__name__)

#: Alpaca begins auto-exercise and auto-assignment at 15:30 ET on expiration
#: day. Assignment is evaluated per leg, not per spread, so a short leg that
#: finishes in the money is assigned while the out-of-the-money long leg simply
#: expires -- leaving an unhedged stock position overnight. We flatten well
#: before that window rather than find out.
TIME_STOP_DTE = 0
ASSIGNMENT_WINDOW_ET = dt_time(15, 30)
FLATTEN_BEFORE_ET = dt_time(15, 0)


@dataclass
class Mark:
    structure_id: int
    kind: str
    sleeve: str
    underlying: str
    qty: int
    entry_price: float          # signed per spread: positive credit, negative debit
    current_price: float        # cost to close one spread, positive number
    unrealized_pnl: float
    pct_of_max_gain: float
    dte: int
    action: str                 # hold | take_profit | stop_loss | time_stop
    rationale: str


def past_flatten_time(now: datetime | None = None) -> bool:
    """True once we are inside the pre-assignment flatten window in New York."""
    now = now or datetime.now(ZoneInfo("America/New_York"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("America/New_York"))
    return now.astimezone(ZoneInfo("America/New_York")).time() >= FLATTEN_BEFORE_ET


def _structure_value(legs: list[dict[str, Any]], mids: dict[str, float]) -> float | None:
    """Net mid value of one spread: what it costs to buy the package back."""
    total = 0.0
    for leg in legs:
        mid = mids.get(leg["symbol"])
        if mid is None:
            return None
        ratio = int(leg.get("ratio_qty", 1))
        total += mid * ratio if leg["side"] == "sell" else -mid * ratio
    return total


def mark_structure(structure: dict[str, Any], mids: dict[str, float]) -> Mark | None:
    legs = structure["legs"]
    qty = int(structure["qty"])
    entry = float(structure["net_price"])
    current = _structure_value(legs, mids)
    if current is None:
        return None

    expiry = min(date.fromisoformat(leg["expiry"]) for leg in legs)
    dte = (expiry - datetime.now(timezone.utc).date()).days

    p = SETTINGS.strategy
    if entry > 0:
        # Credit structure: we sold for `entry`, we buy it back for `current`.
        unrealized = (entry - current) * CONTRACT_MULTIPLIER * qty
        max_gain = entry * CONTRACT_MULTIPLIER * qty
        captured = (entry - current) / entry if entry else 0.0
        if captured >= p.core_profit_target:
            action, why = "take_profit", (
                f"captured {captured:.0%} of the {entry:.2f} credit, target is "
                f"{p.core_profit_target:.0%}"
            )
        elif current >= entry * p.core_stop_multiple:
            action, why = "stop_loss", (
                f"cost to close {current:.2f} is {current / entry:.1f}x the credit received, "
                f"stop is {p.core_stop_multiple:.1f}x"
            )
        else:
            action, why = "hold", f"captured {captured:.0%} of credit, {dte} days to expiry"
    else:
        # Debit structure: we paid `-entry`, it is now worth `-current`.
        debit = -entry
        value = -current
        unrealized = (value - debit) * CONTRACT_MULTIPLIER * qty
        max_gain = float(structure["max_gain"])
        gain_ratio = (value - debit) / debit if debit else 0.0
        if gain_ratio >= p.convex_profit_target:
            action, why = "take_profit", (
                f"up {gain_ratio:.0%} on the debit paid, target is "
                f"{p.convex_profit_target:.0%}"
            )
        elif value <= debit * 0.25:
            action, why = "stop_loss", (
                f"structure has lost {1 - value / debit:.0%} of its value"
            )
        else:
            action, why = "hold", f"up {gain_ratio:+.0%} on debit, {dte} days to expiry"

    if dte <= TIME_STOP_DTE and action == "hold" and past_flatten_time():
        action, why = "time_stop", (
            f"expires today and it is past {FLATTEN_BEFORE_ET:%H:%M} ET; flattening before "
            f"the {ASSIGNMENT_WINDOW_ET:%H:%M} ET assignment window"
        )

    pct_max = (unrealized / max_gain) if max_gain else 0.0

    return Mark(
        structure_id=int(structure["id"]),
        kind=structure["kind"],
        sleeve=structure["sleeve"],
        underlying=structure["underlying"],
        qty=qty,
        entry_price=entry,
        current_price=current,
        unrealized_pnl=unrealized,
        pct_of_max_gain=pct_max,
        dte=dte,
        action=action,
        rationale=why,
    )


def mark_book() -> list[Mark]:
    structures = [s for s in state.live_structures() if s["status"] in {"open", "dry_run"}]
    if not structures:
        return []
    symbols = sorted({leg["symbol"] for s in structures for leg in s["legs"]})
    mids = latest_mids(symbols)
    marks = [mark_structure(s, mids) for s in structures]
    return [m for m in marks if m is not None]


def manage(force_flatten: bool = False) -> list[Mark]:
    """Mark the book and act on every structure that has hit an exit rule."""
    marks = mark_book()
    for mark in marks:
        action = "forced_flatten" if force_flatten else mark.action
        if action == "hold":
            continue

        structure = next(
            (s for s in state.live_structures() if int(s["id"]) == mark.structure_id), None
        )
        if structure is None:
            continue

        symbols = [leg["symbol"] for leg in structure["legs"]]
        mids = latest_mids(symbols)
        outcome = closing.close_structure(
            structure, mids, net_price=mark.current_price, reason=action
        )

        state.log_decision(
            agent="manager",
            proposal={"structure_id": mark.structure_id, "mark": mark.__dict__},
            verdict=action,
            reasons=[mark.rationale, f"path: {outcome['path']}", outcome["detail"]],
            sleeve=mark.sleeve,
            underlying=mark.underlying,
        )
        if not outcome["ok"]:
            log.error("structure #%s could not be fully closed: %s", mark.structure_id, outcome)
            state.log_event(
                "partial_close",
                f"structure {mark.structure_id} has legs that would not close",
                level="error",
                data=outcome,
            )
            continue
        if True:
            state.close_structure(mark.structure_id, mark.unrealized_pnl, action)
            log.info(
                "closed #%s (%s): %s -> P&L %.2f",
                mark.structure_id,
                action,
                mark.rationale,
                mark.unrealized_pnl,
            )
    return marks
