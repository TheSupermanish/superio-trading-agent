"""Shared trade types passed between the strategist, the risk officer, and the executor."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

Sleeve = Literal["core", "convex"]
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

    `net_price` is per spread and signed from our point of view: positive means
    we collect a credit, negative means we pay a debit.
    """

    sleeve: Sleeve
    underlying: str
    kind: str
    legs: list[Leg]
    net_price: float
    width: float
    max_loss_per_unit: float
    max_gain_per_unit: float
    thesis: str = ""
    qty: int = 0
    tags: list[str] = field(default_factory=list)

    @property
    def is_credit(self) -> bool:
        return self.net_price > 0

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
            "width": self.width,
            "max_loss_per_unit": round(self.max_loss_per_unit, 2),
            "max_gain_per_unit": round(self.max_gain_per_unit, 2),
            "qty": self.qty,
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
