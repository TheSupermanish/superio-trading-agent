"""Getting resting orders filled.

Orders go out at the mid, because there is no reason to pay the touch before
trying for a better price. But Alpaca's paper engine fills against the NBBO,
so an order at the mid only fills if the market comes to it. Left alone, a
perfectly good structure can rest unfilled for the whole session while the
opportunity it was sized for disappears.

So every pass walks its own resting orders toward the touch in steps. The touch
is the ceiling and it is not arbitrary: it is the exact price the risk officer
underwrote when it sized the trade, so nothing this module does can make a
position larger or riskier than the one already approved. It only pays up,
within a budget that was set before the order was ever sent.

An order that will not fill even at the touch is cancelled. A structure that
cannot be entered at a price we underwrote is not a structure we want.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from engine import alpaca_cli, state
from engine.config import SETTINGS
from engine.executor import round_to_tick

log = logging.getLogger(__name__)

#: How long an order may rest before we improve its price.
MAX_REST_SECONDS = 75

#: Fractions of the way from the mid to the touch. The last rung is the touch.
LADDER = (0.5, 1.0)

RESTING = {"new", "accepted", "pending_new", "accepted_for_bidding", "partially_filled"}
DEAD = {"canceled", "expired", "rejected", "done_for_day", "replaced"}


def _age_seconds(order: dict[str, Any]) -> float:
    raw = str(order.get("created_at") or order.get("submitted_at") or "")
    if not raw:
        return 0.0
    try:
        created = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (datetime.now(timezone.utc) - created).total_seconds()


def _attempts_so_far(structure_id: int) -> int:
    with state.db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM orders WHERE structure_id = ? AND intent LIKE 'open%'",
            (structure_id,),
        ).fetchone()
    return int(row["n"])


def _ladder_price(structure: dict[str, Any], rung: float) -> float:
    """Net limit `rung` of the way from the mid toward the touch, signed for Alpaca."""
    touch = float(structure["net_price"])
    mid = float(structure.get("net_price_mid") or touch)
    magnitude = abs(mid) + (abs(touch) - abs(mid)) * rung
    price = round_to_tick(max(magnitude, 0.01))
    # A credit is sent negative, a debit positive.
    return -price if touch > 0 else price


def walk() -> list[dict[str, Any]]:
    """Reprice or cancel every resting order this agent owns."""
    actions: list[dict[str, Any]] = []

    for structure in state.live_structures():
        client_order_id = structure.get("client_order_id")
        if not client_order_id or structure["status"] not in {"pending", "open"}:
            continue

        order = alpaca_cli.order_by_client_id(client_order_id)
        if order is None:
            continue
        status = str(order.get("status", ""))

        if status == "filled":
            if structure["status"] != "open":
                state.set_structure_status(int(structure["id"]), "open")
                actions.append({"structure": structure["id"], "action": "filled"})
            continue

        if status in DEAD:
            state.set_structure_status(int(structure["id"]), "rejected")
            state.log_event(
                "order_dead",
                f"structure {structure['id']} order ended as {status}",
                level="warning",
                data={"structure_id": structure["id"], "status": status},
            )
            actions.append({"structure": structure["id"], "action": status})
            continue

        if status not in RESTING or _age_seconds(order) < MAX_REST_SECONDS:
            continue

        rung_index = max(0, _attempts_so_far(int(structure["id"])) - 1)
        if rung_index >= len(LADDER):
            # Already offered the touch and still nothing. Stand down.
            if not SETTINGS.dry_run:
                try:
                    alpaca_cli.cancel_order(str(order.get("id")))
                except alpaca_cli.AlpacaCliError as exc:
                    log.warning("could not cancel %s: %s", order.get("id"), exc)
            state.set_structure_status(int(structure["id"]), "rejected")
            state.log_event(
                "order_abandoned",
                f"structure {structure['id']} would not fill at the underwritten price",
                level="warning",
                data={"structure_id": structure["id"]},
            )
            actions.append({"structure": structure["id"], "action": "abandoned"})
            continue

        new_price = _ladder_price(structure, LADDER[rung_index])
        actions.append(
            {
                "structure": structure["id"],
                "action": "reprice",
                "from": order.get("limit_price"),
                "to": new_price,
            }
        )
        _reprice(structure, order, new_price)

    return actions


def _reprice(structure: dict[str, Any], order: dict[str, Any], price: float) -> None:
    """Cancel and resubmit the same package at a price closer to the touch.

    The cancel is skipped while simulating. Cancelling for real and then
    returning before the resubmit would leave a live order orphaned, which is
    the opposite of what a dry run is supposed to guarantee.
    """
    if not SETTINGS.dry_run:
        try:
            alpaca_cli.cancel_order(str(order.get("id")))
        except alpaca_cli.AlpacaCliError as exc:
            log.warning("could not cancel %s before repricing: %s", order.get("id"), exc)
            return

    payload = {
        "order_class": "mleg",
        "qty": str(int(structure["qty"])),
        "type": "limit",
        "time_in_force": "day",
        "limit_price": str(price),
        "legs": [
            {
                "symbol": leg["symbol"],
                "side": leg["side"],
                "ratio_qty": str(leg.get("ratio_qty", 1)),
                "position_intent": (
                    "buy_to_open" if leg["side"] == "buy" else "sell_to_open"
                ),
            }
            for leg in structure["legs"]
        ],
        "client_order_id": f"{structure['client_order_id']}-r{_attempts_so_far(int(structure['id']))}",
    }

    if SETTINGS.dry_run:
        state.record_order(
            intent="open:reprice",
            payload=payload,
            status="dry_run",
            structure_id=int(structure["id"]),
            client_order_id=payload["client_order_id"],
        )
        return

    try:
        response = alpaca_cli.submit_order(payload)
    except alpaca_cli.AlpacaCliError as exc:
        state.record_order(
            intent="open:reprice",
            payload=payload,
            status="rejected",
            structure_id=int(structure["id"]),
            client_order_id=payload["client_order_id"],
            error=str(exc),
        )
        state.set_structure_status(int(structure["id"]), "rejected")
        log.warning("reprice rejected for structure %s: %s", structure["id"], exc)
        return

    state.record_order(
        intent="open:reprice",
        payload=payload,
        status="accepted",
        structure_id=int(structure["id"]),
        client_order_id=payload["client_order_id"],
        broker_order_id=str(response.get("id")) if isinstance(response, dict) else None,
    )
    # The journal must follow the live order, or the next walk cancels a stale id.
    with state.db() as conn:
        conn.execute(
            "UPDATE structures SET client_order_id = ? WHERE id = ?",
            (payload["client_order_id"], int(structure["id"])),
        )
    log.info("repriced structure %s to %s", structure["id"], price)
