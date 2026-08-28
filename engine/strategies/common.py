"""Shared helpers for turning a chain into a defined-risk vertical."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from engine.config import SETTINGS
from engine.marketdata import Contract, nearest_by_delta
from engine.types import Leg


def group_by_expiry(chain: list[Contract]) -> dict[date, list[Contract]]:
    buckets: dict[date, list[Contract]] = defaultdict(list)
    for c in chain:
        buckets[c.expiry].append(c)
    return dict(buckets)


def tradable(contract: Contract) -> bool:
    p = SETTINGS.strategy
    return (
        contract.mid >= p.min_leg_price
        and contract.spread_pct <= p.max_bid_ask_pct
        and contract.bid > 0
    )


def find_strike(
    contracts: list[Contract], target_strike: float, is_call: bool
) -> Contract | None:
    pool = [c for c in contracts if c.is_call == is_call]
    if not pool:
        return None
    return min(pool, key=lambda c: abs(c.strike - target_strike))


def to_leg(contract: Contract, side: str) -> Leg:
    return Leg(
        symbol=contract.symbol,
        side=side,  # type: ignore[arg-type]
        strike=contract.strike,
        expiry=contract.expiry,
        is_call=contract.is_call,
        mid=contract.mid,
        bid=contract.bid,
        ask=contract.ask,
        delta=contract.delta,
    )


def pick_short_leg(
    contracts: list[Contract], target_delta: float, is_call: bool, tolerance: float
) -> Contract | None:
    candidate = nearest_by_delta(
        [c for c in contracts if tradable(c)], target_delta, is_call=is_call
    )
    if candidate is None or candidate.delta is None:
        return None
    if abs(abs(candidate.delta) - target_delta) > tolerance:
        return None
    return candidate
