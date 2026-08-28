"""Core income sleeve: short vertical spreads and iron condors.

Sells out-of-the-money premium at a target delta, always buying a wing so the
maximum loss is known at entry and the account can never be short naked.

Wing width is searched rather than fixed. Credit collected per unit of width is
what actually determines whether a short spread is worth taking, and the best
width for a given delta moves with the volatility surface: when implied vol is
cheap, only narrow wings clear the credit-to-width floor.
"""

from __future__ import annotations

from engine.config import SETTINGS
from engine.marketdata import Contract, get_chain
from engine.risk import vertical_max_gain, vertical_max_loss
from engine.strategies.common import find_strike, group_by_expiry, pick_short_leg, to_leg, tradable
from engine.types import Proposal


def _build_vertical(
    underlying: str,
    contracts: list[Contract],
    short: Contract,
    is_call: bool,
    width: float,
) -> tuple[Proposal, float] | None:
    wing_strike = short.strike + width if is_call else short.strike - width
    long = find_strike(contracts, wing_strike, is_call)
    if long is None or long.symbol == short.symbol or not tradable(long):
        return None

    actual_width = abs(long.strike - short.strike)
    if actual_width <= 0:
        return None
    if (is_call and long.strike <= short.strike) or (not is_call and long.strike >= short.strike):
        return None

    credit_mid = short.mid - long.mid
    # Crossing the spread: we sell the short leg at its bid and buy the wing at
    # its ask. This is what a paper fill at the touch actually pays.
    credit = short.bid - long.ask
    if credit <= 0:
        return None

    kind = "call_credit_spread" if is_call else "put_credit_spread"
    ratio = credit / actual_width
    proposal = Proposal(
        sleeve="core",
        underlying=underlying,
        kind=kind,
        legs=[to_leg(short, "sell"), to_leg(long, "buy")],
        net_price=credit,
        net_price_mid=credit_mid,
        width=actual_width,
        max_loss_per_unit=vertical_max_loss(actual_width, credit),
        max_gain_per_unit=vertical_max_gain(actual_width, credit),
        thesis=(
            f"Sell the {short.strike:g} {'call' if is_call else 'put'} at "
            f"{abs(short.delta):.2f} delta expiring {short.expiry}, buy the {long.strike:g} wing. "
            f"Collect {credit:.2f} on {actual_width:g} wide ({ratio:.0%} of width), "
            f"priced at the touch rather than the mid."
        ),
        tags=[
            f"dte:{short.dte}",
            f"delta:{abs(short.delta):.2f}",
            f"iv:{short.iv:.2f}",
            f"width:{actual_width:g}",
        ],
    )
    return proposal, ratio


def build_credit_spread(underlying: str, is_call: bool) -> Proposal | None:
    """Best credit spread across eligible expiries and wing widths.

    Scored on credit per unit of width. Candidates that clear the risk
    officer's credit-to-width floor always beat ones that do not, so a thin
    premium environment produces no trade rather than a bad trade.
    """
    p = SETTINGS.strategy
    chain = get_chain(
        underlying, p.core_min_dte, p.core_max_dte, kind="call" if is_call else "put"
    )
    if not chain:
        return None

    floor = SETTINGS.risk.min_credit_to_width
    best: tuple[Proposal, float] | None = None

    for _expiry, contracts in group_by_expiry(chain).items():
        short = pick_short_leg(contracts, p.core_short_delta, is_call, p.core_delta_tolerance)
        if short is None:
            continue
        for width in p.core_widths:
            built = _build_vertical(underlying, contracts, short, is_call, width)
            if built is None:
                continue
            if best is None or _better(built, best, floor):
                best = built

    return best[0] if best else None


def _better(
    candidate: tuple[Proposal, float], incumbent: tuple[Proposal, float], floor: float
) -> bool:
    cand_passes = candidate[1] >= floor
    inc_passes = incumbent[1] >= floor
    if cand_passes != inc_passes:
        return cand_passes
    return candidate[1] > incumbent[1]


def build_iron_condor(underlying: str) -> Proposal | None:
    """Both wings on the same expiry. Max loss is one side's width less total credit."""
    put_side = build_credit_spread(underlying, is_call=False)
    call_side = build_credit_spread(underlying, is_call=True)
    if put_side is None or call_side is None:
        return None
    if put_side.expiry != call_side.expiry:
        return None

    credit = put_side.net_price + call_side.net_price
    credit_mid = put_side.net_price_mid + call_side.net_price_mid
    width = max(put_side.width, call_side.width)

    return Proposal(
        sleeve="core",
        underlying=underlying,
        kind="iron_condor",
        legs=put_side.legs + call_side.legs,
        net_price=credit,
        net_price_mid=credit_mid,
        width=width,
        max_loss_per_unit=vertical_max_loss(width, credit),
        max_gain_per_unit=vertical_max_gain(width, credit),
        thesis=(
            f"Range-bound {underlying} into {put_side.expiry}: sell both wings for "
            f"{credit:.2f} on {width:g} wide ({credit / width:.0%} of width)."
        ),
        tags=["iron_condor", *put_side.tags],
    )
