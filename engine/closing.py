"""Closing multi-leg positions.

Two paths, tried in order.

1. Close the whole spread as one inverted `mleg` package at a net limit price.
   Verified working against the paper API: a bought SPY 773/778 call spread was
   opened at a net 1.05 debit and closed at a net 1.05 credit in a single
   order. This is preferred because the legs settle together at a known net
   price, so there is no window where the position is half closed.

   Some Alpaca users report this path rejecting with "mleg uncovered short
   contracts not allowed, please use single leg order". It did not reproduce
   here, but the fallback exists because being unable to exit is the one
   failure this system must not have.

2. Fall back to one order per leg. Leg ordering matters and is not cosmetic:
   short legs are bought back FIRST. Selling the long leg first would leave a
   naked short option in the account for as long as the second order takes to
   fill, which is exactly the exposure the risk officer exists to prevent.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from engine import alpaca_cli, state
from engine.config import SETTINGS

log = logging.getLogger(__name__)

TICK = 0.01
MARKETABLE_PAD = 0.02  # cross the spread by 2% to get out


def _round_tick(price: float) -> float:
    return max(round(round(price / TICK) * TICK, 2), TICK)


def _leg_close_payload(leg: dict[str, Any], qty: int, price: float) -> dict[str, Any]:
    """Single-leg closing order, priced to be marketable."""
    side = "buy" if leg["side"] == "sell" else "sell"
    intent = "buy_to_close" if side == "buy" else "sell_to_close"
    return {
        "symbol": leg["symbol"],
        "qty": str(qty * int(leg.get("ratio_qty", 1))),
        "side": side,
        "type": "limit",
        "time_in_force": "day",
        "limit_price": str(_round_tick(price)),
        "position_intent": intent,
        "client_order_id": f"superio-{SETTINGS.profile}-leg-{uuid.uuid4().hex[:10]}",
    }


def order_legs_for_closing(legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Short legs first, so the account is never briefly naked short."""
    shorts = [leg for leg in legs if leg["side"] == "sell"]
    longs = [leg for leg in legs if leg["side"] == "buy"]
    return shorts + longs


def close_leg_by_leg(
    structure: dict[str, Any], mids: dict[str, float], reason: str
) -> list[dict[str, Any]]:
    """Close every leg with a marketable limit order, shorts first.

    Returns one result record per leg. A leg that will not fill on a limit is
    liquidated at market rather than left dangling, because a half-closed
    spread is a worse position than either a fully open or fully closed one.
    """
    qty = int(structure["qty"])
    results: list[dict[str, Any]] = []

    for leg in order_legs_for_closing(structure["legs"]):
        mid = mids.get(leg["symbol"])
        if mid is None:
            price = TICK
        elif leg["side"] == "sell":
            price = mid * (1 + MARKETABLE_PAD)   # buying it back, bid up
        else:
            price = mid * (1 - MARKETABLE_PAD)   # selling it out, offer down

        payload = _leg_close_payload(leg, qty, price)
        command = alpaca_cli.command_string(
            ["api", "POST", "/v2/orders"], json.dumps(payload, separators=(",", ":"))
        )

        if SETTINGS.dry_run:
            state.record_order(
                intent=f"close_leg:{reason}",
                payload=payload,
                status="dry_run",
                structure_id=structure["id"],
                client_order_id=payload["client_order_id"],
            )
            results.append({"symbol": leg["symbol"], "status": "dry_run", "command": command})
            continue

        try:
            response = alpaca_cli.submit_order(payload)
            state.record_order(
                intent=f"close_leg:{reason}",
                payload=payload,
                status="accepted",
                structure_id=structure["id"],
                client_order_id=payload["client_order_id"],
                broker_order_id=str(response.get("id")) if isinstance(response, dict) else None,
            )
            results.append({"symbol": leg["symbol"], "status": "accepted", "command": command})
        except alpaca_cli.AlpacaCliError as exc:
            log.warning("leg close rejected for %s: %s -- liquidating at market", leg["symbol"], exc)
            state.record_order(
                intent=f"close_leg:{reason}",
                payload=payload,
                status="rejected",
                structure_id=structure["id"],
                client_order_id=payload["client_order_id"],
                error=str(exc),
            )
            try:
                alpaca_cli.close_position(leg["symbol"])
                results.append({"symbol": leg["symbol"], "status": "liquidated"})
            except alpaca_cli.AlpacaCliError as liq_exc:
                log.error("could not liquidate %s: %s", leg["symbol"], liq_exc)
                results.append(
                    {"symbol": leg["symbol"], "status": "failed", "error": str(liq_exc)}
                )
                if leg["side"] == "sell":
                    # A short leg we cannot buy back is the one failure that
                    # must stop everything. Closing the long legs from here
                    # would strip the cover off a short option and turn a
                    # defined-risk spread into unlimited exposure. Better to
                    # hold the whole structure and shout.
                    log.error(
                        "ABORTING close of structure %s: short leg %s would not close, "
                        "so its cover stays on",
                        structure["id"],
                        leg["symbol"],
                    )
                    state.log_event(
                        "close_aborted",
                        f"short leg {leg['symbol']} would not close; long legs left in place "
                        f"to avoid a naked short",
                        level="error",
                        data={"structure_id": structure["id"], "leg": leg["symbol"]},
                    )
                    return results

    return results


def is_uncovered_short_reject(error: str) -> bool:
    """Alpaca's package-close rejection, which is what sends us down the leg path."""
    lowered = error.lower()
    return "uncovered short" in lowered or "single leg order" in lowered


def _package_close_payload(
    structure: dict[str, Any], limit_price: float
) -> dict[str, Any]:
    """Inverted mleg order: same legs, opposite sides, closing intents.

    Alpaca signs a net limit price from the position's cash-flow point of view:
    a debit is positive, a credit is negative. Closing a spread we bought is a
    sale, so the limit is negative; closing one we sold is a purchase, so it is
    positive.
    """
    inverted = []
    for leg in order_legs_for_closing(structure["legs"]):
        side = "buy" if leg["side"] == "sell" else "sell"
        inverted.append(
            {
                "symbol": leg["symbol"],
                "side": side,
                "ratio_qty": str(leg.get("ratio_qty", 1)),
                "position_intent": "buy_to_close" if side == "buy" else "sell_to_close",
            }
        )
    return {
        "order_class": "mleg",
        "qty": str(int(structure["qty"])),
        "type": "limit",
        "time_in_force": "day",
        "limit_price": str(round(limit_price, 2)),
        "legs": inverted,
        "client_order_id": f"superio-{SETTINGS.profile}-pkg-{uuid.uuid4().hex[:10]}",
    }


def close_package(
    structure: dict[str, Any], net_price: float, reason: str
) -> tuple[bool, str]:
    """Try to close the spread in one order. Returns (succeeded, detail)."""
    # `net_price` is what it costs to close RIGHT NOW, and its sign is what
    # decides the limit: pay a debit and the limit is positive, with marketable
    # meaning offer MORE; receive a credit and the limit is negative, with
    # marketable meaning accept LESS. Padding one way for both cases makes half
    # the exits unfillable, which is the worst possible moment to be stubborn
    # about a penny.
    #
    # This used to dispatch on the entry price instead, which is right for
    # every vertical and wrong for a risk reversal. A short spread always costs
    # something to buy back and a long spread is always worth something to sell,
    # so a vertical's cost to close never changes sign. A risk reversal is short
    # one spread and long another, so it does: a carry position opened for a
    # debit that then goes against us costs money to close, and dispatching on
    # the entry would send an order to SELL it for a credit. That order can
    # never fill, and the one time it matters is the crash the stop exists for.
    if net_price > 0:
        limit = abs(net_price) * (1 + MARKETABLE_PAD)
    else:
        limit = -abs(net_price) * (1 - MARKETABLE_PAD)

    payload = _package_close_payload(structure, limit)
    command = alpaca_cli.command_string(
        ["api", "POST", "/v2/orders"], json.dumps(payload, separators=(",", ":"))
    )

    if SETTINGS.dry_run:
        state.record_order(
            intent=f"close_package:{reason}",
            payload=payload,
            status="dry_run",
            structure_id=structure["id"],
            client_order_id=payload["client_order_id"],
        )
        return True, f"dry run: {command}"

    try:
        response = alpaca_cli.submit_order(payload)
    except alpaca_cli.AlpacaCliError as exc:
        state.record_order(
            intent=f"close_package:{reason}",
            payload=payload,
            status="rejected",
            structure_id=structure["id"],
            client_order_id=payload["client_order_id"],
            error=str(exc),
        )
        return False, str(exc)

    state.record_order(
        intent=f"close_package:{reason}",
        payload=payload,
        status="accepted",
        structure_id=structure["id"],
        client_order_id=payload["client_order_id"],
        broker_order_id=str(response.get("id")) if isinstance(response, dict) else None,
    )
    return True, f"package close accepted at net {limit:.2f}"


def close_structure(
    structure: dict[str, Any], mids: dict[str, float], net_price: float, reason: str
) -> dict[str, Any]:
    """Close a structure, preferring the single-order path.

    Falls back to leg-by-leg only when the package is refused, so the normal
    case never has a half-closed window.
    """
    ok, detail = close_package(structure, net_price, reason)
    if ok:
        return {"path": "package", "ok": True, "detail": detail, "legs": []}

    # Quantity-reserved, buying-power and transient rejects do not mean mleg is
    # unsupported. Falling back on every rejection can submit a second exit
    # against a working first exit and can dismantle the spread leg by leg.
    if not is_uncovered_short_reject(detail):
        return {"path": "package", "ok": False, "detail": detail, "legs": []}

    log.warning(
        "package close unsupported for #%s (%s), falling back to legs",
        structure["id"], detail,
    )
    leg_results = close_leg_by_leg(structure, mids, reason)
    failed = [r for r in leg_results if r.get("status") == "failed"]
    return {
        "path": "leg_by_leg",
        "ok": not failed,
        "detail": detail,
        "legs": leg_results,
    }
