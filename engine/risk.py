"""The risk officer.

The LLM agents propose structures. Nothing reaches the broker unless this
module approves it. Every rule is deterministic, unit-testable, and expressed
as a number in `engine/config.py` -- there is no model call in this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engine import calendar_gate, state
from engine.config import SETTINGS
from engine.types import CONTRACT_MULTIPLIER, Proposal, Verdict


@dataclass
class PortfolioSnapshot:
    equity: float
    last_equity: float
    cash: float
    buying_power: float
    open_risk: float
    peak_equity: float
    open_structures: int
    trades_today: int
    failed_today: int = 0

    @property
    def day_pnl(self) -> float:
        return self.equity - self.last_equity

    @property
    def day_pnl_pct(self) -> float:
        return self.day_pnl / self.last_equity if self.last_equity else 0.0

    @property
    def drawdown_pct(self) -> float:
        if not self.peak_equity:
            return 0.0
        return (self.peak_equity - self.equity) / self.peak_equity


def snapshot_from_account(account: dict[str, Any]) -> PortfolioSnapshot:
    equity = float(account.get("equity", 0) or 0)
    last_equity = float(account.get("last_equity", 0) or 0) or equity
    peak = state.peak_equity() or equity
    return PortfolioSnapshot(
        equity=equity,
        last_equity=last_equity,
        cash=float(account.get("cash", 0) or 0),
        buying_power=float(account.get("buying_power", 0) or 0),
        open_risk=state.open_risk_total(),
        peak_equity=max(peak, equity),
        open_structures=len(state.live_structures()),
        trades_today=state.trades_opened_today(),
        failed_today=state.failed_entries_today(),
    )


# --- Structural checks -----------------------------------------------------

def _is_defined_risk(proposal: Proposal) -> tuple[bool, str]:
    """Every short leg must be covered by a long leg of the same type and expiry.

    This is the gate that makes a blown-up account structurally impossible: no
    naked short options, ever, regardless of what the model asked for.
    """
    shorts = [leg for leg in proposal.legs if leg.side == "sell"]
    longs = [leg for leg in proposal.legs if leg.side == "buy"]

    if not shorts:
        return True, "long-only structure, risk is the debit paid"

    short_units = sum(leg.ratio_qty for leg in shorts)
    long_units = sum(leg.ratio_qty for leg in longs)
    if long_units < short_units:
        return False, f"{short_units} short units covered by only {long_units} long units"

    for short in shorts:
        cover = [
            leg
            for leg in longs
            if leg.is_call == short.is_call and leg.expiry == short.expiry
        ]
        if not cover:
            return False, f"short leg {short.symbol} has no same-expiry, same-type cover"

    # A same-type, same-expiry long leg bounds the loss on a short leg from
    # either side. A long call above a short call caps a credit spread at its
    # width; a long call below a short call is a debit spread whose loss is the
    # premium paid. Both are defined risk, so strike direction is not a gate --
    # the absence of cover is.
    return True, "all short legs covered by same-expiry long legs"


def _liquidity_ok(proposal: Proposal) -> tuple[bool, str]:
    p = SETTINGS.strategy
    for leg in proposal.legs:
        if leg.mid < p.min_leg_price:
            return False, f"{leg.symbol} mid {leg.mid:.2f} below floor {p.min_leg_price:.2f}"
        if leg.spread_pct > p.max_bid_ask_pct:
            return False, (
                f"{leg.symbol} bid/ask spread {leg.spread_pct:.1%} wider than "
                f"{p.max_bid_ask_pct:.1%}"
            )
    return True, "all legs liquid"


def _pricing_ok(proposal: Proposal) -> tuple[bool, str]:
    r = SETTINGS.risk
    width = proposal.pricing_width
    if width <= 0:
        return False, "structure has no width"
    ratio = abs(proposal.net_price) / width
    if proposal.is_credit:
        if ratio < r.min_credit_to_width:
            return False, (
                f"credit {ratio:.1%} of width is below the {r.min_credit_to_width:.0%} floor"
            )
        return True, f"credit is {ratio:.1%} of width"
    if ratio > r.max_debit_to_width:
        return False, f"debit {ratio:.1%} of width exceeds the {r.max_debit_to_width:.0%} cap"
    return True, f"debit is {ratio:.1%} of width"


# --- Sizing ----------------------------------------------------------------

def size_position(proposal: Proposal, snap: PortfolioSnapshot) -> tuple[int, list[str]]:
    """Largest quantity that respects every capital limit at once."""
    r = SETTINGS.risk
    notes: list[str] = []
    unit_risk = proposal.max_loss_per_unit
    if unit_risk <= 0:
        return 0, ["structure reports zero max loss, refusing to size it"]

    per_trade_pct = (
        r.max_carry_risk_per_trade_pct
        if proposal.sleeve == "carry"
        else r.max_risk_per_trade_pct
    )
    per_trade_cap = snap.equity * per_trade_pct
    qty = int(per_trade_cap // unit_risk)
    notes.append(f"per-trade cap {per_trade_cap:,.0f} / unit risk {unit_risk:,.0f} -> {qty}")

    portfolio_room = snap.equity * r.max_open_risk_pct - snap.open_risk
    qty = min(qty, int(max(portfolio_room, 0) // unit_risk))
    notes.append(f"portfolio room {portfolio_room:,.0f} -> {qty}")

    SLEEVE_CAPS = {
        "convex": r.max_convex_open_risk_pct,
        "carry": r.max_carry_open_risk_pct,
    }
    if proposal.sleeve in SLEEVE_CAPS:
        cap = SLEEVE_CAPS[proposal.sleeve]
        sleeve_room = snap.equity * cap - state.open_risk_by("sleeve", proposal.sleeve)
        qty = min(qty, int(max(sleeve_room, 0) // unit_risk))
        notes.append(f"{proposal.sleeve} sleeve room {sleeve_room:,.0f} -> {qty}")

    underlying_room = snap.equity * r.max_risk_per_underlying_pct - state.open_risk_by(
        "underlying", proposal.underlying
    )
    qty = min(qty, int(max(underlying_room, 0) // unit_risk))
    notes.append(f"{proposal.underlying} room {underlying_room:,.0f} -> {qty}")

    buying_power_room = snap.buying_power * 0.5
    qty = min(qty, int(max(buying_power_room, 0) // unit_risk)) if unit_risk else qty
    notes.append(f"buying power room {buying_power_room:,.0f} -> {qty}")

    return max(qty, 0), notes


# --- Kill switches ---------------------------------------------------------

def kill_switch(snap: PortfolioSnapshot) -> tuple[bool, str | None]:
    """Returns (halted, reason)."""
    r = SETTINGS.risk
    if snap.day_pnl_pct <= -r.daily_loss_kill_pct:
        return True, (
            f"daily loss {snap.day_pnl_pct:.2%} hit the {-r.daily_loss_kill_pct:.0%} kill switch"
        )
    if snap.drawdown_pct >= r.total_drawdown_kill_pct:
        return True, (
            f"drawdown {snap.drawdown_pct:.2%} hit the {r.total_drawdown_kill_pct:.0%} kill switch"
        )
    return False, None


# --- Entry point -----------------------------------------------------------

def _volatility_side_ok(proposal: Proposal) -> tuple[bool, str]:
    """Refuse a structure that sits on the wrong side of the volatility signal.

    This was advisory until it cost us. On the first live session the agent
    bought a put debit spread on the underlying whose implied vol was 7.1
    points ABOVE realized, the most expensive premium on the board. Those three
    premium-selling structures made $34 between them; that one premium-buying
    structure lost $260, which was the entire day's loss.

    The routing rule existed, but only as guidance the model was free to
    ignore. Now it is a gate.
    """
    r = SETTINGS.risk
    premium = proposal.vol_premium

    if proposal.sleeve == "carry":
        # A risk reversal is short put volatility and long call volatility at
        # the same time, so the level of implied vol is not what decides
        # whether it is a good trade: the shape is. It sells the expensive side
        # of the skew to fund the cheap side, which holds whether the surface
        # as a whole is rich or cheap. Routing it on a single premium reading
        # would refuse it for a reason that does not apply to it.
        return True, "carry sells the skew, not the level; premium routing does not apply"

    if premium is None:
        return True, "no volatility reading available; not blocking on it"

    if not proposal.is_credit and premium > r.max_premium_to_buy_convexity:
        return False, (
            f"buying premium on {proposal.underlying} while implied sits {premium:+.1%} "
            f"above realized; convexity is expensive here"
        )
    if proposal.is_credit and premium < r.min_premium_to_sell_convexity:
        return False, (
            f"selling premium on {proposal.underlying} while implied sits {premium:+.1%} "
            f"below realized; the market is paying too little for the movement"
        )
    return True, f"volatility premium {premium:+.1%} supports this side of the trade"


def _calendar_ok(proposal: Proposal) -> tuple[bool, str]:
    return calendar_gate.check(
        sleeve=proposal.sleeve,
        underlying=proposal.underlying,
        expiry=proposal.expiry,
        is_credit=proposal.is_credit,
    )


def _budget_ok(snap: PortfolioSnapshot) -> tuple[bool, str]:
    r = SETTINGS.risk
    if snap.trades_today >= r.max_new_trades_per_day:
        return False, f"already opened {snap.trades_today} structures today"
    if snap.open_structures >= r.max_open_structures:
        return False, f"{snap.open_structures} structures already open"
    if snap.failed_today >= r.max_failed_entries_per_day:
        return False, (
            f"{snap.failed_today} entries failed to fill today; "
            "the book is not being priced where it can trade"
        )
    return True, (
        f"{snap.trades_today}/{r.max_new_trades_per_day} trades today, "
        f"{snap.open_structures}/{r.max_open_structures} open, "
        f"{snap.failed_today}/{r.max_failed_entries_per_day} failed entries"
    )


#: The gates, in the order they run. Each is independently testable and each
#: failure is journalled by name, so any refusal can be traced to one rule.
GATES: list[tuple[str, str]] = [
    ("G1", "kill switches"),
    ("G2", "daily and concurrent trade budget"),
    ("G3", "defined risk, no naked shorts"),
    ("G4", "leg liquidity"),
    ("G5", "credit floor and debit cap"),
    ("G6", "scheduled event blackout"),
    ("G7", "volatility side"),
    ("G8", "position sizing"),
]


def evaluate(proposal: Proposal, snap: PortfolioSnapshot) -> Verdict:
    """Run every gate in order. The first refusal ends it."""
    reasons: list[str] = []

    halted, halt_reason = kill_switch(snap)
    if halted:
        return Verdict.reject(f"G1 kill switch: {halt_reason}")
    reasons.append("G1 kill switches clear")

    ok, why = _budget_ok(snap)
    if not ok:
        return Verdict.reject(f"G2 budget: {why}")
    reasons.append(f"G2 {why}")

    ok, why = _is_defined_risk(proposal)
    if not ok:
        return Verdict.reject(f"G3 not defined risk: {why}")
    reasons.append(f"G3 {why}")

    ok, why = _liquidity_ok(proposal)
    if not ok:
        return Verdict.reject(f"G4 liquidity: {why}")
    reasons.append(f"G4 {why}")

    ok, why = _pricing_ok(proposal)
    if not ok:
        return Verdict.reject(f"G5 pricing: {why}")
    reasons.append(f"G5 {why}")

    ok, why = _calendar_ok(proposal)
    if not ok:
        return Verdict.reject(f"G6 event blackout: {why}")
    reasons.append(f"G6 {why}")

    ok, why = _volatility_side_ok(proposal)
    if not ok:
        return Verdict.reject(f"G7 volatility side: {why}")
    reasons.append(f"G7 {why}")

    qty, notes = size_position(proposal, snap)
    reasons.extend(f"G8 {n}" for n in notes)
    if qty < 1:
        return Verdict(approved=False, reasons=reasons + ["G8 sized to zero contracts"], qty=0)

    return Verdict(approved=True, reasons=reasons, qty=qty)


def vertical_max_loss(width: float, net_price: float) -> float:
    """Max loss per spread in dollars for a defined-risk vertical."""
    if net_price > 0:  # credit spread
        return (width - net_price) * CONTRACT_MULTIPLIER
    return abs(net_price) * CONTRACT_MULTIPLIER


def vertical_max_gain(width: float, net_price: float) -> float:
    if net_price > 0:
        return net_price * CONTRACT_MULTIPLIER
    return (width - abs(net_price)) * CONTRACT_MULTIPLIER
