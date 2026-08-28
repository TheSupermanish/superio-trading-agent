"""Scout: deterministic market-regime read.

Produces the numbers the strategist reasons over. Kept free of model calls so
the same regime can be replayed from history when explaining a trade.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from statistics import mean
from typing import Any

from engine.marketdata import daily_bars, get_chain, realized_vol, underlying_price


@dataclass
class Regime:
    underlying: str
    spot: float
    sma20: float | None
    sma50: float | None
    trend: str                 # up | down | chop
    ret_5d: float | None
    realized_vol: float | None
    atm_iv: float | None
    vol_premium: float | None  # atm_iv - realized_vol; positive favours selling
    bias: str                  # bullish | bearish | neutral
    notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return mean(values[-window:])


def atm_iv(underlying: str, spot: float, min_dte: int = 1, max_dte: int = 10) -> float | None:
    """Average IV of the two strikes straddling spot on the nearest expiry."""
    chain = get_chain(underlying, min_dte, max_dte, strike_window_pct=0.03, spot=spot)
    if not chain:
        return None
    nearest_expiry = min(c.expiry for c in chain)
    front = [c for c in chain if c.expiry == nearest_expiry and c.iv]
    if not front:
        return None
    front.sort(key=lambda c: abs(c.strike - spot))
    picks = front[:4]
    return sum(c.iv for c in picks) / len(picks)


def read(underlying: str) -> Regime:
    spot = underlying_price(underlying)
    bars = daily_bars(underlying, days=60)
    closes = [b["close"] for b in bars]

    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    ret_5d = (closes[-1] / closes[-6] - 1) if len(closes) >= 6 else None
    rv = realized_vol(underlying)
    iv = atm_iv(underlying, spot)
    premium = (iv - rv) if (iv is not None and rv is not None) else None

    notes: list[str] = []
    trend = "chop"
    if sma20 and sma50:
        if spot > sma20 > sma50:
            trend = "up"
        elif spot < sma20 < sma50:
            trend = "down"
        notes.append(f"spot {spot:.2f} vs sma20 {sma20:.2f} vs sma50 {sma50:.2f}")

    bias = "neutral"
    if trend == "up" and (ret_5d or 0) > -0.01:
        bias = "bullish"
    elif trend == "down" and (ret_5d or 0) < 0.01:
        bias = "bearish"

    if premium is not None:
        notes.append(
            f"atm iv {iv:.1%} vs realized {rv:.1%} -> {'premium' if premium > 0 else 'discount'} "
            f"{premium:+.1%}"
        )
        if premium <= 0:
            notes.append("options are cheap relative to realized movement; favour buying convexity")

    return Regime(
        underlying=underlying,
        spot=spot,
        sma20=sma20,
        sma50=sma50,
        trend=trend,
        ret_5d=ret_5d,
        realized_vol=rv,
        atm_iv=iv,
        vol_premium=premium,
        bias=bias,
        notes=notes,
    )
