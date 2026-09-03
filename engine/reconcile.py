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

from engine import adopt, alpaca_cli, state
from engine.config import SETTINGS

log = logging.getLogger(__name__)


@dataclass
class Reconciliation:
    broker_symbols: set[str] = field(default_factory=set)
    journal_symbols: set[str] = field(default_factory=set)
    orphans: list[dict[str, Any]] = field(default_factory=list)
    adopted: list[int] = field(default_factory=list)
    closed_out: list[int] = field(default_factory=list)
    confirmed: list[int] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.orphans and not self.closed_out and not self.adopted

    def summary(self) -> str:
        return (
            f"{len(self.confirmed)} structures confirmed, {len(self.closed_out)} marked closed, "
            f"{len(self.adopted)} adopted, {len(self.orphans)} orphan positions"
        )


def run(auto_close_orphans: bool = False, adopt_orphans: bool = True) -> Reconciliation:
    result = Reconciliation()

    if SETTINGS.dry_run:
        # A simulated book has no counterpart at the broker. Reconciling it
        # against the live position list would find none of its legs and close
        # every one of them as vanished, which is how a diary book ends the day
        # with an empty journal and no P&L to compare against anything.
        return result

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
        symbols = {leg["symbol"] for leg in structure["legs"]}
        # Pending entries still own their symbols. A fast fill can reach the
        # position endpoint before the fill walker promotes the journal row;
        # failing to claim it here makes adoption duplicate our own trade.
        result.journal_symbols |= symbols

        # Only structures confirmed filled can be reconciled flat. A `pending`
        # structure has a working entry order and no position yet, which looks
        # identical to a vanished one from the position book alone. Closing it
        # here would abandon a live order and leave the journal claiming a
        # trade that is still trying to happen.
        if structure["status"] not in {"open", "closing"}:
            continue

        present = symbols & result.broker_symbols
        if not present and structure["status"] == "closing":
            # The fill walker owns completion and has the order price needed
            # for real P&L. Never close this at zero or adopt it again.
            result.journal_symbols |= symbols
            continue
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

    if result.orphans and adopt_orphans and not auto_close_orphans:
        # A position the journal cannot see is a position the exit rules cannot
        # act on: no profit target, no stop, and no flatten before assignment.
        # Adopting what can be shown to be defined-risk is strictly safer than
        # leaving it invisible, and anything that cannot be shown stays an
        # orphan and keeps being reported.
        adopted = adopt.adopt(result.orphans)
        if adopted:
            result.adopted = adopted
            result.orphans = [
                position
                for position in result.orphans
                if position["symbol"] not in _journal_symbols()
            ]

    if not result.clean:
        log.warning("reconciliation: %s", result.summary())
    return result


def _journal_symbols() -> set[str]:
    return {
        leg["symbol"]
        for structure in state.live_structures()
        for leg in structure["legs"]
    }
