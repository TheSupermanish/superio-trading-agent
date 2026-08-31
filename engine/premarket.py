"""Work the agent does while the market is shut.

A trading loop that only thinks during market hours wastes two thirds of the
week and arrives at the open cold. Between sessions there is still real work:
the volatility signal can be recomputed from the latest closes, the news can be
read, and the structures the agent intends to trade can be built and put
through the risk gates to see which ones would actually survive.

Nothing here can trade. The market is closed, so the broker would reject an
order anyway, but more importantly this path never calls the executor. It
produces a plan and writes it to the journal, and the next live pass starts
from a considered position rather than from nothing.

Throttled to once an hour: the underlying data only changes on a daily close,
so running it every five minutes would burn model calls to recompute the same
answer.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from engine import risk, state
from engine.agents import analyst, llm
from engine.config import SETTINGS
from engine.regime import read as read_regime
from engine.strategies import build_credit_spread, build_debit_spread, build_iron_condor

log = logging.getLogger(__name__)

STUDY_INTERVAL = timedelta(hours=1)

STYLES = {
    "put_credit_spread": lambda s: build_credit_spread(s, is_call=False),
    "call_credit_spread": lambda s: build_credit_spread(s, is_call=True),
    "iron_condor": build_iron_condor,
    "call_debit_spread": lambda s: build_debit_spread(s, is_call=True),
    "put_debit_spread": lambda s: build_debit_spread(s, is_call=False),
}


def last_study() -> dict[str, Any] | None:
    with state.db() as conn:
        row = conn.execute(
            "SELECT ts, data FROM events WHERE kind = 'session_plan'"
            " ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row or not row["data"]:
        return None
    try:
        payload = json.loads(row["data"])
    except json.JSONDecodeError:
        return None
    payload["ts"] = row["ts"]
    return payload


def _due(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    previous = last_study()
    if not previous:
        return True
    try:
        when = datetime.fromisoformat(str(previous["ts"]))
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return now - when >= STUDY_INTERVAL


def study(snapshot: risk.PortfolioSnapshot, force: bool = False) -> dict[str, Any] | None:
    """Recompute the signal and rehearse the next session's structures.

    Returns the plan, or None if one was produced recently enough.
    """
    if not force and not _due():
        return None

    plan: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "equity": round(snapshot.equity, 2),
        "regimes": {},
        "candidates": [],
        "brief": {},
        "note": (
            "Produced while the market was closed. Quotes are last known rather "
            "than live, so these are intentions to re-verify at the open, not orders."
        ),
    }

    for symbol in SETTINGS.strategy.universe:
        try:
            regime = read_regime(symbol)
        except Exception as exc:  # noqa: BLE001
            log.debug("closed-market regime failed for %s: %s", symbol, exc)
            continue

        plan["regimes"][symbol] = {
            "spot": round(regime.spot, 2),
            "trend": regime.trend,
            "bias": regime.bias,
            "realized_vol": round(regime.realized_vol, 4) if regime.realized_vol else None,
            "atm_iv": round(regime.atm_iv, 4) if regime.atm_iv else None,
            "vol_premium": round(regime.vol_premium, 4) if regime.vol_premium is not None else None,
        }

        # Try the same breadth the live agent does. Testing one shape per
        # symbol made the plan report "nothing would clear the gates" while the
        # live agent, which compares several, was finding spreads at 26% of
        # width. A rehearsal that is narrower than the real thing is worse than
        # no rehearsal, because it reports a problem that does not exist.
        cheap = (regime.vol_premium or 0) <= 0
        if cheap:
            styles = ["call_debit_spread", "put_debit_spread", "put_credit_spread"]
        else:
            styles = ["put_credit_spread", "call_credit_spread", "iron_condor"]

        for style in styles:
            builder = STYLES.get(style)
            if builder is None:
                continue
            try:
                proposal = builder(symbol)
            except Exception as exc:  # noqa: BLE001
                log.debug("closed-market build failed %s %s: %s", symbol, style, exc)
                continue
            if proposal is None:
                plan["candidates"].append(
                    {"symbol": symbol, "style": style, "verdict": "unavailable",
                     "reason": "no contracts fit that shape on last known quotes"}
                )
                continue

            verdict = risk.evaluate(proposal, snapshot)
            plan["candidates"].append(
                {
                    "symbol": symbol,
                    "style": style,
                    "sleeve": proposal.sleeve,
                    "expiry": proposal.expiry.isoformat(),
                    "net_price": round(proposal.net_price, 2),
                    "width": proposal.width,
                    "verdict": "would trade" if verdict.approved else "would refuse",
                    "qty": verdict.qty,
                    "max_loss": round(proposal.max_loss_per_unit * max(verdict.qty, 1), 2),
                    "reason": (verdict.reasons[-1] if verdict.reasons else ""),
                }
            )

    if llm.vertex_available():
        try:
            plan["brief"] = analyst.brief(SETTINGS.strategy.universe)
        except Exception as exc:  # noqa: BLE001
            log.debug("closed-market brief failed: %s", exc)

    tradable = [c for c in plan["candidates"] if c["verdict"] == "would trade"]
    summary = (
        f"{len(tradable)} of {len(plan['candidates'])} structures would clear the gates "
        f"at the next open"
    )
    plan["summary"] = summary
    state.log_event("session_plan", summary, data=plan)
    log.info("session plan: %s", summary)
    return plan
