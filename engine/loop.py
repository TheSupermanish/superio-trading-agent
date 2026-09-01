"""The trading loop.

One pass does five things in order: mark the account, check the kill switches,
manage what is already open, look for new structures, and journal everything.

Entry selection is deterministic here. The LLM layer sits on top of this loop
and shapes the bias and the narrative; it never gets to move a risk limit.
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import asdict
from typing import Any

from engine import (
    alpaca_cli,
    executor,
    fills,
    manager,
    mcp_research,
    preflight,
    premarket,
    reconcile,
    report,
    risk,
    state,
)
from engine.agents import agent
from engine.config import SETTINGS
from engine.regime import Regime, read as read_regime
from engine.strategies import build_credit_spread, build_debit_spread, build_iron_condor
from engine.types import Proposal

log = logging.getLogger("superio")


def market_open() -> tuple[bool, dict[str, Any]]:
    clock = alpaca_cli.clock()
    return bool(clock.get("is_open")), clock


def account_snapshot() -> risk.PortfolioSnapshot:
    account = alpaca_cli.account()
    if SETTINGS.diary:
        # A diary book reads the live chain through the main account's keys,
        # but it must not be sized against that account's money. Sizing a
        # 50,000 book off a 101,000 balance would make every diary number a
        # fifth too large and the whole comparison meaningless. So the balance
        # is the diary's own: its stake plus whatever it has closed.
        account = _diary_account()
    snap = risk.snapshot_from_account(account)
    state.record_equity(
        equity_value=snap.equity,
        cash=snap.cash,
        buying_power=snap.buying_power,
        open_risk=snap.open_risk,
        day_pnl=snap.day_pnl,
    )
    return snap


def _diary_account(db_path: Any = None) -> dict[str, Any]:
    """A synthetic balance sheet for a book with no broker account.

    Equity is the diary stake plus realized P&L. `last_equity` is that same
    figure less today's realized P&L, so the daily kill switch measures the
    diary's own day rather than the live account's.
    """
    from engine.config import DIARY_EQUITY

    with state.db(db_path) as conn:
        realized = float(conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS p FROM structures"
            " WHERE status = 'closed'"
        ).fetchone()["p"])
        today = float(conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS p FROM structures"
            " WHERE status = 'closed' AND date(closed_at) = date('now')"
        ).fetchone()["p"])

    equity = DIARY_EQUITY + realized
    return {
        "equity": equity,
        "last_equity": equity - today,
        "cash": equity,
        "buying_power": equity,
    }


def score(proposal: Proposal) -> float:
    """Comparable quality score across sleeves.

    Credit structures score on premium collected per unit of width. Debit
    structures score on payoff ratio, scaled down so a lottery ticket does not
    automatically outrank a solid premium sale.
    """
    if proposal.is_credit:
        return proposal.net_price / proposal.width
    payoff = proposal.max_gain_per_unit / proposal.max_loss_per_unit
    return payoff * 0.10


def candidates_for(regime: Regime) -> list[Proposal]:
    """Structures worth considering for one underlying, given the regime.

    The volatility premium decides which sleeve leads. When implied vol sits
    below realized vol, selling premium is being paid too little for the
    movement actually happening, so convexity leads instead.
    """
    out: list[Proposal] = []
    premium = regime.vol_premium
    iv_is_rich = premium is not None and premium > 0
    convex_enabled = SETTINGS.risk.max_convex_open_risk_pct > 0

    if iv_is_rich or regime.bias == "neutral":
        if regime.bias == "neutral":
            condor = build_iron_condor(regime.underlying)
            if condor:
                out.append(condor)
        if regime.bias in {"bullish", "neutral"}:
            put_side = build_credit_spread(regime.underlying, is_call=False)
            if put_side:
                out.append(put_side)
        if regime.bias in {"bearish", "neutral"}:
            call_side = build_credit_spread(regime.underlying, is_call=True)
            if call_side:
                out.append(call_side)
    else:
        # Cheap options: lead with convexity in the direction of the trend, but
        # still take a credit spread if one clears the credit-to-width floor.
        if convex_enabled and regime.bias in {"bullish", "neutral"}:
            debit = build_debit_spread(regime.underlying, is_call=True)
            if debit:
                out.append(debit)
        if convex_enabled and regime.bias in {"bearish", "neutral"}:
            debit = build_debit_spread(regime.underlying, is_call=False)
            if debit:
                out.append(debit)
        credit = build_credit_spread(regime.underlying, is_call=(regime.bias == "bearish"))
        if credit:
            out.append(credit)

    if not convex_enabled:
        out = [p for p in out if p.sleeve != "convex"]

    return sorted(out, key=score, reverse=True)


def scan_and_trade(snap: risk.PortfolioSnapshot) -> list[dict[str, Any]]:
    """One agent pass across the universe.

    The agent investigates with tools and returns references to structures the
    risk officer has already approved and sized. This function's only job is to
    execute what it chose; it cannot be handed anything ungated, because
    `propose_structure` is the only way a structure comes into existence and it
    runs the gates before returning.
    """
    results: list[dict[str, Any]] = []

    outcome = agent.run(snap)
    state.log_decision(
        agent="strategist",
        proposal={
            "action": outcome.action,
            "confidence": outcome.confidence,
            "tool_calls": outcome.tool_calls,
            "chosen": [p.as_dict() for p in outcome.chosen],
            "market_brief": outcome.brief,
        },
        verdict="trade" if outcome.trading else "stand_aside",
        reasons=[
            outcome.reasoning,
            f"source: {outcome.source}",
            f"{len(outcome.tool_calls)} tool calls over {outcome.steps} steps",
        ],
    )

    if not outcome.trading:
        log.info("agent stood aside: %s", outcome.reasoning or "no reason given")
        return results

    log.info(
        "agent chose %s (confidence %.2f): %s",
        ", ".join(f"{p.underlying} {p.kind}" for p in outcome.chosen),
        outcome.confidence,
        outcome.reasoning,
    )

    for proposal in outcome.chosen:
        # Re-check against the budget as it stands now: the agent may have
        # picked two structures that individually fit and together do not.
        verdict = risk.evaluate(proposal, snap)
        if not verdict.approved:
            log.info(
                "%s %s no longer fits the budget: %s",
                proposal.underlying,
                proposal.kind,
                verdict.reasons[-1] if verdict.reasons else "unknown",
            )
            continue
        qty = min(proposal.qty or verdict.qty, verdict.qty)

        result = executor.open_position(proposal, qty, thesis_extra=outcome.reasoning)
        log.info(
            "%s %s x%s @ %s -> %s",
            proposal.underlying,
            proposal.kind,
            qty,
            result.limit_price,
            "submitted" if result.submitted else (result.error or "dry run"),
        )
        results.append(
            {
                "underlying": proposal.underlying,
                "kind": proposal.kind,
                "qty": qty,
                "limit_price": result.limit_price,
                "command": result.command,
                "submitted": result.submitted,
                "error": result.error,
                "agent_source": outcome.source,
            }
        )
        snap.open_risk += proposal.max_loss_per_unit * qty
        snap.trades_today += 1
        snap.open_structures += 1

    return results


def _publish() -> None:
    """Refresh the snapshot the dashboard reads. Never fatal."""
    try:
        report.write()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not write snapshot: %s", exc)


def run_once(ignore_market_hours: bool = False) -> dict[str, Any]:
    is_open, clock = market_open()
    if not is_open and not ignore_market_hours:
        # Closed does not mean idle. Recompute the volatility signal, read the
        # news, and rehearse the structures we would open, so the next live
        # pass starts from a considered position instead of a cold one.
        log.info("market closed, next open %s", clock.get("next_open"))
        plan = None
        try:
            snap = account_snapshot()
            plan = premarket.study(snap)
        except Exception as exc:  # noqa: BLE001 - study must never break the loop
            log.warning("closed-market study failed: %s", exc)
        _publish()
        return {
            "traded": False,
            "reason": "market closed",
            "clock": clock,
            "plan": plan,
        }

    # What the broker holds is the truth; correct the journal before sizing
    # anything against it.
    recon = reconcile.run()
    if not recon.clean:
        state.log_event("reconciliation", recon.summary(), level="warning")

    snap = account_snapshot()
    halted, reason = risk.kill_switch(snap)
    log.info(
        "equity %.2f | day P&L %.2f (%.2f%%) | open risk %.0f | structures %d",
        snap.equity,
        snap.day_pnl,
        snap.day_pnl_pct * 100,
        snap.open_risk,
        snap.open_structures,
    )

    if halted:
        log.warning("KILL SWITCH: %s -- flattening", reason)
        state.log_event("kill_switch", reason or "", level="warning", data=asdict(snap))
        marks = manager.manage(force_flatten=True)
        _publish()
        return {"traded": False, "reason": reason, "marks": [m.__dict__ for m in marks]}

    # Chase our own resting orders toward the touch before opening anything new.
    walked = fills.walk()
    for action in walked:
        log.info("fill walker: %s", action)

    marks = manager.manage()

    # If the daily budget is gone there is nothing to decide, so do not build
    # candidates or call a model just to have every one of them refused by G2.
    # Left alone this writes hundreds of identical rejections into the journal
    # and makes the gate report read as though the agent spends its life
    # blocked by its own budget.
    remaining = SETTINGS.risk.max_new_trades_per_day - snap.trades_today
    if remaining <= 0:
        log.info("daily trade budget used (%d), managing only", snap.trades_today)
        _publish()
        return {
            "traded": False,
            "reason": "daily trade budget used",
            "equity": snap.equity,
            "marks": [m.__dict__ for m in marks],
        }

    opened = scan_and_trade(snap)
    _publish()

    return {
        "traded": bool(opened),
        "equity": snap.equity,
        "day_pnl": snap.day_pnl,
        "open_risk": snap.open_risk,
        "marks": [m.__dict__ for m in marks],
        "opened": opened,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Superio autonomous options trading loop")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument(
        "--interval", type=int, default=300, help="seconds between passes (default 300)"
    )
    parser.add_argument(
        "--ignore-market-hours",
        action="store_true",
        help="run the pass even when the market is closed (for dry-run rehearsal)",
    )
    parser.add_argument(
        "--require-competition-balance",
        action="store_true",
        help="fail preflight unless the account equity is exactly $100,000",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("starting superio | %s", SETTINGS.describe())
    state.log_event("loop_start", SETTINGS.describe())

    checks = preflight.run(require_competition_balance=args.require_competition_balance)
    # Through the logger, not print: stdout is block-buffered when it is not a
    # terminal, so under systemd the preflight report would sit unflushed in a
    # buffer while the log filled with everything that came after it.
    for check in checks:
        log.info("preflight %s", check)
    if not preflight.passed(checks):
        log.error("preflight failed, refusing to trade")
        state.log_event("preflight_failed", preflight.report(checks), level="error")
        raise SystemExit(1)

    if args.once:
        result = run_once(ignore_market_hours=args.ignore_market_hours)
        log.info("pass complete: %s", result.get("reason", "ok"))
        return

    while True:
        try:
            run_once(ignore_market_hours=args.ignore_market_hours)
        except KeyboardInterrupt:
            log.info("interrupted, exiting")
            return
        except Exception as exc:  # noqa: BLE001 - the loop must survive a bad pass
            log.exception("pass failed: %s", exc)
            state.log_event("pass_error", str(exc), level="error")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
