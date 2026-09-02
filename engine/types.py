"""Shared trade types passed between the strategist, the risk officer, and the executor."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

Sleeve = Literal["core", "convex", "carry"]
Side = Literal["buy", "sell"]

CONTRACT_MULTIPLIER = 100


@dataclass
class Leg:
    symbol: str
    side: Side
    strike: float
    expiry: date
    is_call: bool
    mid: float
    bid: float
    ask: float
    delta: float | None = None
    ratio_qty: int = 1

    @property
    def position_intent(self) -> str:
        return "buy_to_open" if self.side == "buy" else "sell_to_open"

    @property
    def spread_pct(self) -> float:
        if self.mid <= 0:
            return 1.0
        return (self.ask - self.bid) / self.mid

    @property
    def touch(self) -> float:
        """Price paid or received when crossing the spread on this leg."""
        return self.ask if self.side == "buy" else self.bid

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "strike": self.strike,
            "expiry": self.expiry.isoformat(),
            "is_call": self.is_call,
            "mid": round(self.mid, 4),
            "bid": self.bid,
            "ask": self.ask,
            "delta": round(self.delta, 4) if self.delta is not None else None,
            "ratio_qty": self.ratio_qty,
        }


@dataclass
class Proposal:
    """One defined-risk structure the agent wants to open.

    Two prices, both per spread and signed from our point of view: positive
    means we collect a credit, negative means we pay a debit.

    * `net_price` is the CONSERVATIVE price, computed by crossing the spread on
      every leg. Every risk calculation uses this one. Alpaca's paper engine
      fills at the touch rather than the mid, so sizing off mid prices would
      quietly understate the real maximum loss.
    * `net_price_mid` is where the package theoretically trades. It is the
      opening limit price, because there is no reason to pay the touch before
      trying for the mid.
    """

    sleeve: Sleeve
    underlying: str
    kind: str
    legs: list[Leg]
    net_price: float
    net_price_mid: float
    width: float
    max_loss_per_unit: float
    max_gain_per_unit: float
    thesis: str = ""
    qty: int = 0
    #: Implied minus realized vol for the underlying at build time. Positive
    #: means premium is expensive, which is a reason to sell it and a reason
    #: not to buy it.
    vol_premium: float | None = None
    tags: list[str] = field(default_factory=list)

    @property
    def is_credit(self) -> bool:
        return self.net_price > 0

    @property
    def pricing_width(self) -> float:
        """The width the price should be judged against.

        For a vertical this is just the strike width, and the arithmetic below
        reduces to exactly that. For a package with two different widths it is
        not: a risk reversal risks the put width and can win the call width, so
        judging its debit against the put width asks whether the premium paid
        is a sensible fraction of a number the premium did not buy. On a live
        SPY chain that read as a debit of 110% of width and refused every
        structure the sleeve could build.

        Stated properly, the denominator is what the structure can pay out
        gross: the maximum gain plus whatever was paid to get it, or the
        maximum loss plus whatever was received for taking it.
        """
        # max_loss_per_unit and max_gain_per_unit are per-contract dollars;
        # net_price is per-share points. Mixing them silently turned a 1.2
        # credit on a 5 wide spread into 0.3% of width and refused the whole
        # income sleeve, so the conversion is explicit here.
        if self.is_credit:
            return self.max_loss_per_unit / CONTRACT_MULTIPLIER + self.net_price
        return self.max_gain_per_unit / CONTRACT_MULTIPLIER + abs(self.net_price)

    @property
    def slippage_budget(self) -> float:
        """Distance between the hopeful price and the price we sized against."""
        return abs(self.net_price_mid - self.net_price)

    @property
    def max_loss_total(self) -> float:
        return self.max_loss_per_unit * max(self.qty, 1)

    @property
    def expiry(self) -> date:
        return min(leg.expiry for leg in self.legs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sleeve": self.sleeve,
            "underlying": self.underlying,
            "kind": self.kind,
            "legs": [leg.as_dict() for leg in self.legs],
            "net_price": round(self.net_price, 4),
            "net_price_mid": round(self.net_price_mid, 4),
            "width": self.width,
            "max_loss_per_unit": round(self.max_loss_per_unit, 2),
            "max_gain_per_unit": round(self.max_gain_per_unit, 2),
            "qty": self.qty,
            "vol_premium": round(self.vol_premium, 4) if self.vol_premium is not None else None,
            "thesis": self.thesis,
            "tags": self.tags,
        }


@dataclass
class Verdict:
    approved: bool
    reasons: list[str]
    qty: int = 0

    @classmethod
    def reject(cls, reason: str) -> "Verdict":
        return cls(approved=False, reasons=[reason], qty=0)
