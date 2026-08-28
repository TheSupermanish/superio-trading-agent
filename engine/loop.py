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

from engine import alpaca_cli, executor, manager, preflight, reconcile, risk, state
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
    snap = risk.snapshot_from_account(account)
    state.record_equity(
        equity_value=snap.equity,
        cash=snap.cash,
        buying_power=snap.buying_power,
        open_risk=snap.open_risk,
        day_pnl=snap.day_pnl,
    )
    return snap


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
    """One entry pass across the universe. At most one new structure per name."""
    results: list[dict[str, Any]] = []

    for underlying in SETTINGS.strategy.universe:
        try:
            regime = read_regime(underlying)
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the loop
            log.warning("regime read failed for %s: %s", underlying, exc)
            continue

        state.log_decision(
            agent="scout",
            proposal=regime.as_dict(),
            verdict="observed",
            reasons=regime.notes,
            underlying=underlying,
        )

        opened = False
        for proposal in candidates_for(regime):
            verdict = risk.evaluate(proposal, snap)
            state.log_decision(
                agent="risk_officer",
                proposal=proposal.as_dict(),
                verdict="approved" if verdict.approved else "rejected",
                reasons=verdict.reasons,
                sleeve=proposal.sleeve,
                underlying=underlying,
            )
            if not verdict.approved:
                log.info(
                    "%s %s rejected: %s", underlying, proposal.kind, verdict.reasons[-1]
                )
                continue

            proposal.qty = verdict.qty
            result = executor.open_position(
                proposal, verdict.qty, thesis_extra=f"Regime: {regime.trend}/{regime.bias}."
            )
            log.info(
                "%s %s x%s @ %s -> %s",
                underlying,
                proposal.kind,
                verdict.qty,
                result.limit_price,
                "submitted" if result.submitted else (result.error or "dry run"),
            )
            results.append(
                {
                    "underlying": underlying,
                    "kind": proposal.kind,
                    "qty": verdict.qty,
                    "limit_price": result.limit_price,
                    "command": result.command,
                    "submitted": result.submitted,
                    "error": result.error,
                }
            )
            snap.open_risk += proposal.max_loss_per_unit * verdict.qty
            snap.trades_today += 1
            snap.open_structures += 1
            opened = True
            break  # one new structure per underlying per pass

        if not opened:
            log.info("%s: no structure cleared the gates this pass", underlying)

    return results


def run_once(ignore_market_hours: bool = False) -> dict[str, Any]:
    is_open, clock = market_open()
    if not is_open and not ignore_market_hours:
        log.info("market closed, next open %s", clock.get("next_open"))
        return {"traded": False, "reason": "market closed", "clock": clock}

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
        return {"traded": False, "reason": reason, "marks": [m.__dict__ for m in marks]}

    marks = manager.manage()
    opened = scan_and_trade(snap)

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
    print(preflight.report(checks))
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
