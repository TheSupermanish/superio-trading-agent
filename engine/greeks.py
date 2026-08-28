"""Black-Scholes pricing, implied vol, and greeks.

Alpaca's free `indicative` options feed does not always return greeks or IV.
When a snapshot is missing them, we solve for implied vol from the mid price
and compute the greeks locally, so the delta-selection logic never depends on
a paid data plan.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.optimize import brentq
from scipy.stats import norm

RISK_FREE_RATE = 0.043
TRADING_DAYS = 252.0


@dataclass(frozen=True)
class GreekSet:
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float


def _d1_d2(spot: float, strike: float, t: float, vol: float, rate: float) -> tuple[float, float]:
    if t <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        raise ValueError("invalid Black-Scholes inputs")
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))
    return d1, d1 - vol * math.sqrt(t)


def bs_price(
    spot: float, strike: float, t: float, vol: float, is_call: bool, rate: float = RISK_FREE_RATE
) -> float:
    if t <= 0:
        intrinsic = spot - strike if is_call else strike - spot
        return max(intrinsic, 0.0)
    d1, d2 = _d1_d2(spot, strike, t, vol, rate)
    disc = math.exp(-rate * t)
    if is_call:
        return spot * norm.cdf(d1) - strike * disc * norm.cdf(d2)
    return strike * disc * norm.cdf(-d2) - spot * norm.cdf(-d1)


def implied_vol(
    price: float,
    spot: float,
    strike: float,
    t: float,
    is_call: bool,
    rate: float = RISK_FREE_RATE,
) -> float | None:
    """Solve for the volatility that reprices the option at `price`."""
    if price <= 0 or t <= 0:
        return None
    intrinsic = max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)
    if price < intrinsic - 1e-6:
        return None

    def objective(vol: float) -> float:
        return bs_price(spot, strike, t, vol, is_call, rate) - price

    try:
        return float(brentq(objective, 1e-4, 6.0, maxiter=100, xtol=1e-6))
    except (ValueError, RuntimeError):
        return None


def greeks(
    spot: float,
    strike: float,
    t: float,
    vol: float,
    is_call: bool,
    rate: float = RISK_FREE_RATE,
) -> GreekSet:
    """Per-share greeks. Theta is quoted per calendar day."""
    if t <= 0 or vol <= 0:
        intrinsic_delta = (1.0 if spot > strike else 0.0) if is_call else (
            -1.0 if spot < strike else 0.0
        )
        return GreekSet(delta=intrinsic_delta, gamma=0.0, theta=0.0, vega=0.0, iv=vol)

    d1, d2 = _d1_d2(spot, strike, t, vol, rate)
    pdf = norm.pdf(d1)
    sqrt_t = math.sqrt(t)
    disc = math.exp(-rate * t)

    delta = norm.cdf(d1) if is_call else norm.cdf(d1) - 1.0
    gamma = pdf / (spot * vol * sqrt_t)
    vega = spot * pdf * sqrt_t / 100.0

    term1 = -(spot * pdf * vol) / (2 * sqrt_t)
    if is_call:
        theta = (term1 - rate * strike * disc * norm.cdf(d2)) / 365.0
    else:
        theta = (term1 + rate * strike * disc * norm.cdf(-d2)) / 365.0

    return GreekSet(delta=delta, gamma=gamma, theta=theta, vega=vega, iv=vol)


def year_fraction(days: float) -> float:
    """Calendar days to year fraction, floored so 0DTE stays numerically sane."""
    return max(days, 0.25) / 365.0
