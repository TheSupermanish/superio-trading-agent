"""Order execution.

Orders go out through the official Alpaca CLI, so every trade the agent makes
is reproducible as a shell command a judge can paste into a terminal. The exact
command and the raw JSON response are written to the journal next to the
proposal and the risk verdict that produced them.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from engine import alpaca_cli, state
from engine.config import SETTINGS
from engine.types import CONTRACT_MULTIPLIER, Proposal

log = logging.getLogger(__name__)

TICK = 0.01

#: Alpaca prices a multi-leg order as a single net price. A net credit is sent
#: as a negative limit price and a net debit as a positive one, matching the
#: sign convention of the position's cash flow.
CREDIT_IS_NEGATIVE = True


@dataclass
class ExecutionResult:
    submitted: bool
    structure_id: int | None
    client_order_id: str
    broker_order_id: str | None
    limit_price: float
    command: str
    response: dict[str, Any] | None
    error: str | None = None


def round_to_tick(price: float) -> float:
    return round(round(price / TICK) * TICK, 2)


def net_limit_price(proposal: Proposal, slippage: float = 0.0) -> float:
    """Net limit price for the package, signed the way Alpaca expects.

    `slippage` shades the price against us: for a credit we accept less, for a
    debit we pay more. It is how the repricing ladder walks toward a fill.
    """
    magnitude = abs(proposal.net_price)
    if proposal.is_credit:
        magnitude = max(magnitude * (1 - slippage), TICK)
        price = round_to_tick(magnitude)
        return -price if CREDIT_IS_NEGATIVE else price
    magnitude = magnitude * (1 + slippage)
    return round_to_tick(magnitude)


def build_payload(proposal: Proposal, qty: int, slippage: float = 0.0) -> dict[str, Any]:
    """The exact JSON body posted to /v2/orders."""
    return {
        "order_class": "mleg",
        "qty": str(qty),
        "type": "limit",
        "time_in_force": "day",
        "limit_price": str(net_limit_price(proposal, slippage)),
        "legs": [
            {
                "symbol": leg.symbol,
                "side": leg.side,
                "ratio_qty": str(leg.ratio_qty),
                "position_intent": leg.position_intent,
            }
            for leg in proposal.legs
        ],
    }


def build_close_payload(
    legs: list[dict[str, Any]], qty: int, limit_price: float
) -> dict[str, Any]:
    """Mirror of an opening order: sides inverted, intents flipped to close."""
    inverted = []
    for leg in legs:
        side = "buy" if leg["side"] == "sell" else "sell"
        intent = "buy_to_close" if side == "buy" else "sell_to_close"
        inverted.append(
            {
                "symbol": leg["symbol"],
                "side": side,
                "ratio_qty": str(leg.get("ratio_qty", 1)),
                "position_intent": intent,
            }
        )
    return {
        "order_class": "mleg",
        "qty": str(qty),
        "type": "limit",
        "time_in_force": "day",
        "limit_price": str(round_to_tick(limit_price)),
        "legs": inverted,
    }


def open_position(proposal: Proposal, qty: int, thesis_extra: str = "") -> ExecutionResult:
    """Journal the structure, then send the order (unless running dry)."""
    client_order_id = f"superio-{SETTINGS.profile}-{uuid.uuid4().hex[:12]}"
    payload = build_payload(proposal, qty)
    payload["client_order_id"] = client_order_id
    command = alpaca_cli.command_string(
        ["api", "POST", "/v2/orders"], json.dumps(payload, separators=(",", ":"))
    )

    structure_id = state.open_structure(
        sleeve=proposal.sleeve,
        underlying=proposal.underlying,
        kind=proposal.kind,
        legs=[leg.as_dict() for leg in proposal.legs],
        qty=qty,
        net_price=proposal.net_price,
        max_loss=proposal.max_loss_per_unit * qty,
        max_gain=proposal.max_gain_per_unit * qty,
        client_order_id=client_order_id,
        thesis=(proposal.thesis + (" " + thesis_extra if thesis_extra else "")).strip(),
    )

    if SETTINGS.dry_run:
        state.record_order(
            intent="open",
            payload=payload,
            status="dry_run",
            structure_id=structure_id,
            client_order_id=client_order_id,
        )
        state.set_structure_status(structure_id, "dry_run")
        log.info("DRY RUN would submit: %s", command)
        return ExecutionResult(
            submitted=False,
            structure_id=structure_id,
            client_order_id=client_order_id,
            broker_order_id=None,
            limit_price=float(payload["limit_price"]),
            command=command,
            response=None,
        )

    try:
        response = alpaca_cli.submit_order(payload)
    except alpaca_cli.AlpacaCliError as exc:
        state.record_order(
            intent="open",
            payload=payload,
            status="rejected",
            structure_id=structure_id,
            client_order_id=client_order_id,
            error=str(exc),
        )
        state.set_structure_status(structure_id, "rejected")
        log.warning("order rejected: %s", exc)
        return ExecutionResult(
            submitted=False,
            structure_id=structure_id,
            client_order_id=client_order_id,
            broker_order_id=None,
            limit_price=float(payload["limit_price"]),
            command=command,
            response=None,
            error=str(exc),
        )

    broker_order_id = str(response.get("id")) if isinstance(response, dict) else None
    state.record_order(
        intent="open",
        payload=payload,
        status=str(response.get("status", "accepted")) if isinstance(response, dict) else "accepted",
        structure_id=structure_id,
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
    )
    state.set_structure_status(structure_id, "open")
    return ExecutionResult(
        submitted=True,
        structure_id=structure_id,
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
        limit_price=float(payload["limit_price"]),
        command=command,
        response=response if isinstance(response, dict) else None,
    )


def close_structure(
    structure: dict[str, Any], limit_price: float, reason: str
) -> ExecutionResult:
    """Close an open structure as a package at `limit_price` (signed like an open)."""
    qty = int(structure["qty"])
    payload = build_close_payload(structure["legs"], qty, limit_price)
    client_order_id = f"superio-{SETTINGS.profile}-close-{uuid.uuid4().hex[:10]}"
    payload["client_order_id"] = client_order_id
    command = alpaca_cli.command_string(
        ["api", "POST", "/v2/orders"], json.dumps(payload, separators=(",", ":"))
    )

    if SETTINGS.dry_run:
        state.record_order(
            intent=f"close:{reason}",
            payload=payload,
            status="dry_run",
            structure_id=structure["id"],
            client_order_id=client_order_id,
        )
        log.info("DRY RUN would close #%s (%s): %s", structure["id"], reason, command)
        return ExecutionResult(
            submitted=False,
            structure_id=structure["id"],
            client_order_id=client_order_id,
            broker_order_id=None,
            limit_price=float(payload["limit_price"]),
            command=command,
            response=None,
        )

    try:
        response = alpaca_cli.submit_order(payload)
    except alpaca_cli.AlpacaCliError as exc:
        state.record_order(
            intent=f"close:{reason}",
            payload=payload,
            status="rejected",
            structure_id=structure["id"],
            client_order_id=client_order_id,
            error=str(exc),
        )
        return ExecutionResult(
            submitted=False,
            structure_id=structure["id"],
            client_order_id=client_order_id,
            broker_order_id=None,
            limit_price=float(payload["limit_price"]),
            command=command,
            response=None,
            error=str(exc),
        )

    state.record_order(
        intent=f"close:{reason}",
        payload=payload,
        status="accepted",
        structure_id=structure["id"],
        client_order_id=client_order_id,
        broker_order_id=str(response.get("id")) if isinstance(response, dict) else None,
    )
    return ExecutionResult(
        submitted=True,
        structure_id=structure["id"],
        client_order_id=client_order_id,
        broker_order_id=str(response.get("id")) if isinstance(response, dict) else None,
        limit_price=float(payload["limit_price"]),
        command=command,
        response=response if isinstance(response, dict) else None,
    )


def wait_for_fill(client_order_id: str, timeout: float = 45.0) -> dict[str, Any] | None:
    """Poll the broker until the order is terminal or the timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        order = alpaca_cli.order_by_client_id(client_order_id)
        if order and order.get("status") in {"filled", "canceled", "rejected", "expired"}:
            return order
        time.sleep(3)
    return alpaca_cli.order_by_client_id(client_order_id)
