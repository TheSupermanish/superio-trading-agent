"""Reconciliation between the broker and the journal.

The journal is what the agent believes. The broker is what is true. They drift:
an order fills after the loop moved on, a leg is closed manually, a structure is
assigned. Left alone, that drift means risk limits are computed against a book
that does not exist.

This runs at the start of every pass. It never opens anything. Its only power
is to correct the journal and to shout about positions nobody claims.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from engine import alpaca_cli, state

log = logging.getLogger(__name__)


@dataclass
class Reconciliation:
    broker_symbols: set[str] = field(default_factory=set)
    journal_symbols: set[str] = field(default_factory=set)
    orphans: list[dict[str, Any]] = field(default_factory=list)
    closed_out: list[int] = field(default_factory=list)
    confirmed: list[int] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.orphans and not self.closed_out

    def summary(self) -> str:
        return (
            f"{len(self.confirmed)} structures confirmed, {len(self.closed_out)} marked closed, "
            f"{len(self.orphans)} orphan positions"
        )


def run(auto_close_orphans: bool = False) -> Reconciliation:
    result = Reconciliation()

    try:
        positions = alpaca_cli.positions()
    except alpaca_cli.AlpacaCliError as exc:
        log.warning("could not read positions: %s", exc)
        return result

    option_positions = {
        p["symbol"]: p for p in positions if str(p.get("asset_class")) == "us_option"
    }
    result.broker_symbols = set(option_positions)

    for structure in state.live_structures():
        # Only structures confirmed filled can be reconciled flat. A `pending`
        # structure has a working entry order and no position yet, which looks
        # identical to a vanished one from the position book alone. Closing it
        # here would abandon a live order and leave the journal claiming a
        # trade that is still trying to happen.
        if structure["status"] != "open":
            continue
        symbols = {leg["symbol"] for leg in structure["legs"]}
        result.journal_symbols |= symbols

        present = symbols & result.broker_symbols
        if not present:
            # Confirmed filled earlier, no legs now: it really is gone.
            # The journal thinks this is open and the broker has none of it.
            log.info(
                "structure #%s has no legs at the broker; marking closed",
                structure["id"],
            )
            state.close_structure(int(structure["id"]), 0.0, "reconciled_flat")
            result.closed_out.append(int(structure["id"]))
        else:
            result.confirmed.append(int(structure["id"]))
            missing = symbols - present
            if missing:
                state.log_event(
                    "partial_position",
                    f"structure {structure['id']} is missing legs {sorted(missing)}",
                    level="warning",
                    data={"structure_id": structure["id"], "missing": sorted(missing)},
                )

    for symbol in sorted(result.broker_symbols - result.journal_symbols):
        position = option_positions[symbol]
        result.orphans.append(position)
        state.log_event(
            "orphan_position",
            f"{symbol} is held at the broker but no journal entry claims it",
            level="warning",
            data=position,
        )
        if auto_close_orphans:
            try:
                alpaca_cli.close_position(symbol)
                log.warning("closed orphan position %s", symbol)
            except alpaca_cli.AlpacaCliError as exc:
                log.error("could not close orphan %s: %s", symbol, exc)

    if not result.clean:
        log.warning("reconciliation: %s", result.summary())
    return result
