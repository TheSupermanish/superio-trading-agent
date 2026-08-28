"""Convex sleeve: short-dated debit verticals bought for the right tail.

Sized to a hard cap by the risk officer. The whole sleeve going to zero is an
acceptable, budgeted outcome; that is the price of the payoff asymmetry.
"""

from __future__ import annotations

from engine.config import SETTINGS
from engine.marketdata import Contract, get_chain, nearest_by_delta
from engine.risk import vertical_max_gain, vertical_max_loss
from engine.strategies.common import find_strike, group_by_expiry, to_leg, tradable
from engine.types import Proposal


def _build(
    underlying: str, contracts: list[Contract], is_call: bool, width: float, target_delta: float
) -> tuple[Proposal, float] | None:
    long = nearest_by_delta([c for c in contracts if tradable(c)], target_delta, is_call=is_call)
    if long is None or long.delta is None:
        return None

    short_strike = long.strike + width if is_call else long.strike - width
    short = find_strike(contracts, short_strike, is_call)
    if short is None or short.symbol == long.symbol or not tradable(short):
        return None

    actual_width = abs(short.strike - long.strike)
    if actual_width <= 0:
        return None
    if (is_call and short.strike <= long.strike) or (not is_call and short.strike >= long.strike):
        return None

    debit = long.mid - short.mid
    if debit <= 0:
        return None

    max_loss = vertical_max_loss(actual_width, -debit)
    max_gain = vertical_max_gain(actual_width, -debit)
    payoff_ratio = max_gain / max_loss if max_loss else 0.0

    kind = "call_debit_spread" if is_call else "put_debit_spread"
    proposal = Proposal(
        sleeve="convex",
        underlying=underlying,
        kind=kind,
        legs=[to_leg(long, "buy"), to_leg(short, "sell")],
        net_price=-debit,
        width=actual_width,
        max_loss_per_unit=max_loss,
        max_gain_per_unit=max_gain,
        thesis=(
            f"Pay {debit:.2f} for the {long.strike:g}/{short.strike:g} "
            f"{'call' if is_call else 'put'} spread expiring {long.expiry}: "
            f"{payoff_ratio:.1f}x payoff if the move continues."
        ),
        tags=[f"dte:{long.dte}", f"payoff:{payoff_ratio:.1f}x", f"iv:{long.iv:.2f}"],
    )
    return proposal, payoff_ratio


def build_debit_spread(underlying: str, is_call: bool) -> Proposal | None:
    """Best convex vertical across eligible expiries and widths, scored on payoff ratio.

    A wider spread costs more but pays more; the risk officer caps the debit as
    a fraction of width, so the search naturally settles on the widest
    structure that is still cheap enough to be worth owning.
    """
    p = SETTINGS.strategy
    chain = get_chain(
        underlying,
        p.convex_min_dte,
        p.convex_max_dte,
        kind="call" if is_call else "put",
    )
    if not chain:
        return None

    cap = SETTINGS.risk.max_debit_to_width
    best: tuple[Proposal, float] | None = None
    for _expiry, contracts in group_by_expiry(chain).items():
        for width in p.convex_widths:
            built = _build(
                underlying,
                contracts,
                is_call=is_call,
                width=width,
                target_delta=p.convex_long_delta,
            )
            if built is None:
                continue
            if abs(built[0].net_price) / built[0].width > cap:
                continue
            if best is None or built[1] > best[1]:
                best = built

    return best[0] if best else None
