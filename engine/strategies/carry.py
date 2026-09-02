"""Carry sleeve: long-horizon bullish risk reversals.

Everything else this agent trades lives for a day or a week. That leaves the
book with no exposure to the one edge in equities with decades of evidence
behind it, the equity risk premium, because you are not paid for holding equity
risk while you are flat. This sleeve is how the agent holds a directional
position for weeks.

The structure is a risk reversal: sell an out-of-the-money put spread, use the
credit to buy a wider out-of-the-money call spread, same expiry, five to nine
weeks out.

Three properties make it worth having.

It is long delta, so it is paid for the exposure rather than for predicting a
move. It is defined risk on both sides, so a crash costs the put spread's width
and nothing more, which is what keeps G3 satisfied and the account intact. And
its upside is a multiple of its risk, unlike a credit spread whose best
possible outcome is the fraction of width it was sold for.

The financing comes from the skew. Index puts trade at a higher implied
volatility than calls the same distance away, so selling the put spread and
buying the call spread sells the expensive side of the surface to fund the
cheap side. That holds whatever the overall level of implied volatility is
doing, which is why this sleeve is not routed by the variance risk premium the
way the other two are.
"""

from __future__ import annotations

from engine.config import SETTINGS
from engine.marketdata import Contract, get_chain, nearest_by_delta
from engine.strategies.common import find_strike, group_by_expiry, to_leg, tradable
from engine.types import CONTRACT_MULTIPLIER, Proposal


def _vertical(
    contracts: list[Contract],
    anchor: Contract,
    width: float,
    is_call: bool,
) -> Contract | None:
    """The other leg of a vertical `width` away from `anchor`, further OTM."""
    target = anchor.strike + width if is_call else anchor.strike - width
    other = find_strike(contracts, target, is_call)
    if other is None or other.symbol == anchor.symbol or not tradable(other):
        return None
    if is_call and other.strike <= anchor.strike:
        return None
    if not is_call and other.strike >= anchor.strike:
        return None
    return other


def _build(
    underlying: str,
    puts: list[Contract],
    calls: list[Contract],
    put_width: float,
    call_width: float,
) -> tuple[Proposal, float] | None:
    p = SETTINGS.strategy

    short_put = nearest_by_delta(
        [c for c in puts if tradable(c)], p.carry_put_delta, is_call=False
    )
    long_call = nearest_by_delta(
        [c for c in calls if tradable(c)], p.carry_call_delta, is_call=True
    )
    if short_put is None or long_call is None:
        return None

    long_put = _vertical(puts, short_put, put_width, is_call=False)
    short_call = _vertical(calls, long_call, call_width, is_call=True)
    if long_put is None or short_call is None:
        return None

    actual_put_width = abs(short_put.strike - long_put.strike)
    actual_call_width = abs(short_call.strike - long_call.strike)
    if actual_put_width <= 0 or actual_call_width <= 0:
        return None

    # Priced at the touch, as everywhere else: we sell at the bid and buy at
    # the ask. Sizing off the mid would understate the real maximum loss.
    put_credit = short_put.bid - long_put.ask
    call_debit = long_call.ask - short_call.bid
    if call_debit <= 0:
        return None

    put_credit_mid = short_put.mid - long_put.mid
    call_debit_mid = long_call.mid - short_call.mid

    #: Positive means the package is a net credit to us, matching the sign
    #: convention the executor and the manager use everywhere else.
    net_price = put_credit - call_debit
    net_price_mid = put_credit_mid - call_debit_mid

    # A crash takes the put spread to its full width and leaves the call spread
    # worthless, so the loss is the width plus whatever we paid, or minus
    # whatever we were paid. A rally is the mirror image on the call side.
    #
    # Both are converted to per-contract dollars, because that is what
    # max_loss_per_unit means everywhere else in the engine: the vertical
    # builders get it from vertical_max_loss, which multiplies. Leaving these
    # in points made the risk officer read 6.31 dollars of risk where there
    # were 631, and it sized 118 contracts into a 750 dollar per-trade cap.
    max_loss_points = actual_put_width - net_price
    max_gain_points = actual_call_width + net_price
    if max_loss_points <= 0 or max_gain_points <= 0:
        return None
    max_loss = max_loss_points * CONTRACT_MULTIPLIER
    max_gain = max_gain_points * CONTRACT_MULTIPLIER

    if net_price < 0:
        paid = -net_price
        if paid / actual_call_width > p.carry_max_net_debit_to_call_width:
            return None

    payoff_ratio = max_gain / max_loss
    legs = [
        to_leg(short_put, "sell"),
        to_leg(long_put, "buy"),
        to_leg(long_call, "buy"),
        to_leg(short_call, "sell"),
    ]

    funding = (
        f"a {net_price:.2f} credit"
        if net_price >= 0
        else f"{-net_price:.2f} net"
    )
    proposal = Proposal(
        sleeve="carry",
        underlying=underlying,
        kind="risk_reversal",
        legs=legs,
        net_price=net_price,
        net_price_mid=net_price_mid,
        # The put spread's width is what is actually at risk, so that is the
        # width the risk officer should size and gate against.
        width=actual_put_width,
        max_loss_per_unit=max_loss,
        max_gain_per_unit=max_gain,
        thesis=(
            f"Long {underlying} for {long_call.dte} days for {funding}: sell the "
            f"{short_put.strike:g}/{long_put.strike:g} put spread at "
            f"{abs(short_put.delta or 0):.2f} delta to finance the "
            f"{long_call.strike:g}/{short_call.strike:g} call spread. "
            f"Risk {max_loss:,.0f} to make {max_gain:,.0f}, {payoff_ratio:.1f}x, "
            f"and the skew pays for the call."
        ),
        tags=[
            f"dte:{long_call.dte}",
            f"payoff:{payoff_ratio:.1f}x",
            f"put_width:{actual_put_width:g}",
            f"call_width:{actual_call_width:g}",
        ],
    )
    return proposal, payoff_ratio


def build_risk_reversal(underlying: str) -> Proposal | None:
    """Best financed bullish structure across eligible expiries and widths.

    Scored on payoff ratio, so the search settles on the widest call spread the
    put spread can still pay for. A wider call spread is strictly better for
    the same risk as long as the financing holds, and the debit cap is what
    stops the search from drifting into an unfinanced long call spread.
    """
    p = SETTINGS.strategy
    puts = get_chain(underlying, p.carry_min_dte, p.carry_max_dte, kind="put")
    calls = get_chain(underlying, p.carry_min_dte, p.carry_max_dte, kind="call")
    if not puts or not calls:
        return None

    puts_by_expiry = group_by_expiry(puts)
    calls_by_expiry = group_by_expiry(calls)

    best: tuple[Proposal, float] | None = None
    for expiry, expiry_puts in puts_by_expiry.items():
        expiry_calls = calls_by_expiry.get(expiry)
        if not expiry_calls:
            # Both sides must share an expiry or the position is a calendar,
            # which is a different trade with a different risk profile and no
            # defined loss that G3 could verify.
            continue
        for put_width in p.carry_put_widths:
            for call_width in p.carry_call_widths:
                built = _build(
                    underlying, expiry_puts, expiry_calls, put_width, call_width
                )
                if built is None:
                    continue
                if best is None or built[1] > best[1]:
                    best = built

    return best[0] if best else None
