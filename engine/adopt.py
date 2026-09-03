"""Rebuilding the journal from the broker.

Reconciliation already says the broker is what is true, but until now it could
only complain about positions the journal did not claim. That is fine when the
journal is merely stale. It is not fine when the journal is gone: a machine
dies, the agent is brought up somewhere else, and every open structure is
invisible to the exit rules. Nothing closes at its profit target, nothing stops
out, and nothing is flattened before assignment, because as far as the manager
is concerned the book is empty.

So orphan legs are paired back into structures and adopted. Everything needed
is on the position itself: the strike and expiry are in the OCC symbol, the
direction is the sign of the quantity, and `avg_entry_price` gives the fill, so
the package's entry price is recoverable exactly rather than guessed.

Only structures this module can prove are defined-risk get adopted. A short leg
with no matching long is left as an orphan and reported, because inventing a
journal entry for a naked short would tell the risk officer a comfortable lie
about what the account is holding.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from itertools import permutations
from typing import Any

from engine import state
from engine.types import CONTRACT_MULTIPLIER

log = logging.getLogger(__name__)


def parse_occ(symbol: str) -> tuple[str, date, bool, float] | None:
    """(underlying, expiry, is_call, strike) from an OCC option symbol.

    `SPY260903C00768000` is SPY, 2026-09-03, call, strike 768. The last 15
    characters are fixed width; everything before them is the root.
    """
    if len(symbol) < 16:
        return None
    root, tail = symbol[:-15], symbol[-15:]
    try:
        expiry = datetime.strptime(tail[:6], "%y%m%d").date()
        strike = int(tail[7:]) / 1000.0
    except ValueError:
        return None
    kind = tail[6].upper()
    if kind not in {"C", "P"}:
        return None
    return root, expiry, kind == "C", strike


def _classify(legs: list[dict[str, Any]]) -> str:
    """Name the structure from its legs, the way the builders would have."""
    calls = [leg for leg in legs if leg["is_call"]]
    puts = [leg for leg in legs if not leg["is_call"]]
    if calls and puts:
        return "iron_condor" if len(legs) == 4 else "combination"

    side = calls or puts
    short = next((leg for leg in side if leg["side"] == "sell"), None)
    long = next((leg for leg in side if leg["side"] == "buy"), None)
    if short is None or long is None:
        return "vertical"

    is_call = bool(side[0]["is_call"])
    # A short strike nearer the money than its cover is a credit spread.
    if is_call:
        credit = short["strike"] < long["strike"]
    else:
        credit = short["strike"] > long["strike"]
    kind = "call" if is_call else "put"
    return f"{kind}_{'credit' if credit else 'debit'}_spread"


def _pair(positions: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Turn one underlying-and-expiry group into a defined-risk structure.

    Returns None when the group cannot be shown to be covered, which leaves
    every leg in it reported as an orphan rather than adopted.
    """
    legs: list[dict[str, Any]] = []
    for position in positions:
        parsed = parse_occ(str(position["symbol"]))
        if parsed is None:
            return None
        _root, expiry, is_call, strike = parsed
        qty = int(position["qty"])
        if qty == 0:
            return None
        legs.append({
            "symbol": position["symbol"],
            "side": "sell" if qty < 0 else "buy",
            "strike": strike,
            "expiry": expiry.isoformat(),
            "is_call": is_call,
            "ratio_qty": 1,
            "mid": abs(float(position.get("current_price") or 0.0)),
            "delta": None,
            "qty": abs(qty),
            "entry": abs(float(position.get("avg_entry_price") or 0.0)),
        })

    # Every short must be covered by a long of the same type, in the same
    # quantity, or the package is not the defined-risk thing it looks like.
    for is_call in (True, False):
        side = [leg for leg in legs if leg["is_call"] is is_call]
        if not side:
            continue
        if len(side) != 2:
            # More than two strikes on one side is a butterfly, a ladder, or
            # two overlapping spreads the broker has netted. The maximum loss
            # is not the outer width and this module will not guess at it.
            return None
        shorts = sum(leg["qty"] for leg in side if leg["side"] == "sell")
        longs = sum(leg["qty"] for leg in side if leg["side"] == "buy")
        if shorts != longs or shorts == 0:
            return None

    qty = min(leg["qty"] for leg in legs)
    if qty <= 0 or any(leg["qty"] != qty for leg in legs):
        return None

    # Credit-positive, matching the convention everywhere else.
    net_price = sum(
        leg["entry"] if leg["side"] == "sell" else -leg["entry"] for leg in legs
    )

    # Worst case is the widest uncovered gap on either side, less what was
    # received. Computed per option type and taken as the larger, which is what
    # an iron condor's maximum loss is.
    worst = 0.0
    for is_call in (True, False):
        side = [leg for leg in legs if leg["is_call"] is is_call]
        if len(side) < 2:
            continue
        worst = max(worst, abs(max(l["strike"] for l in side) - min(l["strike"] for l in side)))

    kind = _classify(legs)
    if "credit" in kind or kind == "iron_condor":
        # A credit-shaped payoff opened for a debit has no profitable terminal
        # region. That means these legs were paired incorrectly.
        if net_price <= 0:
            return None
        max_loss = (worst - net_price) * CONTRACT_MULTIPLIER * qty
        max_gain = net_price * CONTRACT_MULTIPLIER * qty
    elif "debit" in kind:
        if net_price >= 0:
            return None
        max_loss = abs(net_price) * CONTRACT_MULTIPLIER * qty
        max_gain = (worst - abs(net_price)) * CONTRACT_MULTIPLIER * qty
    else:
        return None
    if max_loss <= 0 or max_gain <= 0:
        return None

    underlying = parse_occ(str(positions[0]["symbol"]))[0]
    return {
        "underlying": underlying,
        "kind": kind,
        "legs": [
            {k: v for k, v in leg.items() if k not in {"qty", "entry"}} for leg in legs
        ],
        "qty": qty,
        "net_price": round(net_price, 2),
        "max_loss": round(max_loss, 2),
        "max_gain": round(max_gain, 2),
    }


#: How long a closed structure keeps its claim on the legs it was holding. Long
#: enough for a close to settle, short enough that a genuinely orphaned leg is
#: picked up on the next pass rather than the next session.
SETTLE_WINDOW = timedelta(minutes=45)


def _recently_closed_symbols() -> set[str]:
    """Legs belonging to a structure closed inside the settle window."""
    cutoff = (datetime.now(timezone.utc) - SETTLE_WINDOW).isoformat()
    with state.db() as conn:
        rows = conn.execute(
            "SELECT legs FROM structures WHERE status = 'closed' AND closed_at >= ?",
            (cutoff,),
        ).fetchall()

    out: set[str] = set()
    for row in rows:
        try:
            for leg in json.loads(row["legs"] or "[]"):
                out.add(str(leg.get("symbol")))
        except (TypeError, ValueError):
            continue
    return out


def _structures_in(positions: list[dict[str, Any]]) -> list[dict[str, Any] | None]:
    """Split one underlying-and-expiry group into the structures it contains.

    A group is not always one structure. The broker reports net positions, so
    a twelve lot iron condor and a three lot call spread in the same expiry
    arrive as fifteen short calls beside twelve short puts, and asking for a
    single package out of that fails on the quantity mismatch. It is still two
    perfectly ordinary verticals.

    So the calls and puts are tried together first, which is what an iron
    condor is, and each side is adopted on its own when they do not pair. The
    resulting journal may not match the entries that opened the positions, but
    it describes the same risk, which is what the exit rules and the risk
    officer actually need.
    """
    combined = _pair(positions)
    if combined is not None:
        return [combined]

    out: list[dict[str, Any] | None] = []
    for is_call in (True, False):
        side = [
            position
            for position in positions
            if (parse_occ(str(position["symbol"])) or (None, None, None, None))[2]
            is is_call
        ]
        if side:
            paired = _split_same_type_verticals(side)
            if paired is None:
                out.append(_pair(side))
            else:
                out.extend(_pair(pair) for pair in paired)
    return out


def _split_same_type_verticals(
    positions: list[dict[str, Any]],
) -> list[list[dict[str, Any]]] | None:
    """Pair netted same-type legs into the narrowest plausible verticals."""
    shorts = [p for p in positions if int(p.get("qty") or 0) < 0]
    longs = [p for p in positions if int(p.get("qty") or 0) > 0]
    if len(shorts) < 2 or len(shorts) != len(longs) or len(shorts) > 6:
        return None
    if any(abs(int(s["qty"])) != abs(int(l["qty"])) for s in shorts for l in longs):
        return None

    candidates: list[tuple[float, tuple[dict[str, Any], ...]]] = []
    for ordered in permutations(longs):
        pairs = [[short, long] for short, long in zip(shorts, ordered)]
        built = [_pair(pair) for pair in pairs]
        if all(item is not None for item in built):
            candidates.append((sum(float(item["max_loss"]) for item in built if item), ordered))
    if not candidates:
        return None
    _risk, assignment = min(candidates, key=lambda item: item[0])
    return [[short, long] for short, long in zip(shorts, assignment)]


def adopt(orphans: list[dict[str, Any]]) -> list[int]:
    """Journal every orphan group that can be shown to be defined-risk.

    Returns the ids created. Anything not adopted stays an orphan and keeps
    being reported, which is the honest outcome: the agent is holding something
    it cannot describe, and a person should look at it.
    """
    claimed = _recently_closed_symbols()

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for position in orphans:
        symbol = str(position.get("symbol", ""))
        parsed = parse_occ(symbol)
        if parsed is None:
            continue

        # A leg whose quantity is committed to a working order is already being
        # acted on by something. Adopting it means two journal entries fighting
        # over one position, and the second one can never close because the
        # first one holds the quantity.
        qty = position.get("qty")
        available = position.get("qty_available")
        if available is not None and str(available) != str(qty):
            log.info("not adopting %s: quantity committed to a working order", symbol)
            continue

        # A structure closed moments ago still has legs at the broker until the
        # close settles, and it no longer claims them because it is closed.
        # Adopting them recreates the position that was just exited. This is
        # exactly how a take-profit on a SPY put spread came back as a second
        # structure that then spent the afternoon failing to close.
        if symbol in claimed:
            log.info("not adopting %s: a structure closed recently held it", symbol)
            continue

        root, expiry, _is_call, _strike = parsed
        groups[(root, expiry.isoformat())].append(position)

    created: list[int] = []
    for (underlying, expiry), positions in sorted(groups.items()):
        for built in _structures_in(positions):
            if built is None:
                log.warning(
                    "cannot adopt %s %s: %d legs do not form a covered structure",
                    underlying, expiry, len(positions),
                )
                continue

            # Sleeve is inferred, not known. A credit package is income, a
            # debit package is convexity, and anything five weeks out is
            # carry. Getting it wrong only changes which sleeve budget it
            # charges, and charging the wrong budget is far better than the
            # position being invisible.
            days = (date.fromisoformat(expiry) - datetime.now().date()).days
            if days >= 21:
                sleeve = "carry"
            elif built["net_price"] > 0:
                sleeve = "core"
            else:
                sleeve = "convex"

            with state.db() as conn:
                cursor = conn.execute(
                    "INSERT INTO structures (opened_at, sleeve, underlying, kind,"
                    " legs, qty, net_price, net_price_mid, max_loss, max_gain,"
                    " status, thesis) VALUES (?,?,?,?,?,?,?,?,?,?,'open',?)",
                    (
                        state.utcnow(), sleeve, built["underlying"], built["kind"],
                        json.dumps(built["legs"]), built["qty"], built["net_price"],
                        built["net_price"], built["max_loss"], built["max_gain"],
                        "adopted from the broker: this position was open with no "
                        "journal entry claiming it, so the exit rules could not "
                        "see it",
                    ),
                )
                structure_id = int(cursor.lastrowid)

            created.append(structure_id)
            state.log_event(
                "adopted",
                f"adopted {built['underlying']} {built['kind']} x{built['qty']} "
                f"expiring {expiry} into the journal as #{structure_id}",
                level="warning",
                data=built,
            )
            log.warning("adopted structure #%s: %s", structure_id, built["kind"])

    return created
