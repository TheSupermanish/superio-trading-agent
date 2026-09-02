"""Carry sleeve tests.

This sleeve is the only one that holds a position for weeks, and almost
everything else in the engine assumes a position lives for a day. Each test
here pins a place where that assumption would have quietly broken it.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from engine import calendar_gate, manager, risk
from engine.config import SETTINGS
from engine.types import Leg, Proposal

MULT = 100


def _reversal(net_price: float, put_width: float = 5.0, call_width: float = 15.0) -> Proposal:
    """A risk reversal: short put spread financing a wider long call spread."""
    expiry = (date.today() + timedelta(days=45)).isoformat()
    legs = [
        Leg(symbol="SPY_P740", side="sell", strike=740, expiry=expiry, is_call=False,
            mid=4.0, bid=3.9, ask=4.1, delta=-0.22),
        Leg(symbol="SPY_P735", side="buy", strike=735, expiry=expiry, is_call=False,
            mid=3.0, bid=2.9, ask=3.1, delta=-0.17),
        Leg(symbol="SPY_C780", side="buy", strike=780, expiry=expiry, is_call=True,
            mid=6.0, bid=5.9, ask=6.1, delta=0.32),
        Leg(symbol="SPY_C795", side="sell", strike=795, expiry=expiry, is_call=True,
            mid=2.0, bid=1.9, ask=2.1, delta=0.18),
    ]
    return Proposal(
        sleeve="carry",
        underlying="SPY",
        kind="risk_reversal",
        legs=legs,
        net_price=net_price,
        net_price_mid=net_price,
        width=put_width,
        max_loss_per_unit=(put_width - net_price) * MULT,
        max_gain_per_unit=(call_width + net_price) * MULT,
        thesis="test",
        tags=[],
    )


def _structure(net_price: float, dte: int = 45, qty: int = 2) -> dict:
    proposal = _reversal(net_price)
    expiry = (date.today() + timedelta(days=dte)).isoformat()
    return {
        "id": 1,
        "sleeve": "carry",
        "kind": "risk_reversal",
        "underlying": "SPY",
        "qty": qty,
        "net_price": net_price,
        "max_loss": proposal.max_loss_per_unit * qty,
        "max_gain": proposal.max_gain_per_unit * qty,
        "legs": [{**leg.__dict__, "expiry": expiry} for leg in proposal.legs],
    }


# --- the structure itself --------------------------------------------------

def test_a_crash_costs_the_put_width_and_no_more():
    """The whole point of financing with a spread rather than a naked put."""
    proposal = _reversal(net_price=0.50, put_width=5.0, call_width=15.0)
    assert proposal.max_loss_per_unit == 450.0, proposal.max_loss_per_unit
    ok, why = risk._is_defined_risk(proposal)
    assert ok, why


def test_every_short_leg_is_covered_in_its_own_expiry_and_type():
    """G3 has to hold on a four legged package, not just a two legged one."""
    ok, why = risk._is_defined_risk(_reversal(net_price=0.50))
    assert ok, why


def test_upside_is_a_multiple_of_risk_not_a_fraction_of_width():
    """This is the reason the sleeve exists.

    A credit spread's best possible outcome is the credit it was sold for, a
    fraction of the width it risks. A financed risk reversal risks the put
    width and can make the call width, which is deliberately wider.
    """
    proposal = _reversal(net_price=0.50, put_width=5.0, call_width=15.0)
    payoff = proposal.max_gain_per_unit / proposal.max_loss_per_unit
    assert payoff > 3.0, payoff


# --- marking ---------------------------------------------------------------

def test_a_carry_position_is_not_marked_as_a_credit_spread():
    """The bug this sleeve would have died of.

    Every other structure is dispatched on the sign of its entry price. A risk
    reversal can open for a small credit, and run through the credit branch a
    ten cent credit would be judged against 55% of ten cents: hit on the first
    tick, closed for nothing, sleeve useless. It must be marked against the
    risk that was underwritten instead.
    """
    structure = _structure(net_price=0.10)
    # Barely moved: six cents of the ten cent credit would be 60% "captured"
    # under the core rule, comfortably past the 55% target.
    mark = manager.mark_structure(
        structure, {leg["symbol"]: 0.0 for leg in structure["legs"]}
    )
    assert mark is not None
    assert mark.action == "hold", (mark.action, mark.rationale)


def test_carry_takes_profit_on_a_fraction_of_maximum_gain():
    structure = _structure(net_price=0.50, qty=2)
    max_gain_per_unit = structure["max_gain"] / (MULT * 2)
    # A package that has gained is cheaper to close, so the cost to close
    # falls below the entry price by the gain.
    target = SETTINGS.strategy.carry_profit_target
    mark = _mark_at(structure, 0.50 - max_gain_per_unit * (target + 0.05))
    assert mark.action == "take_profit", (mark.action, mark.rationale)


def test_carry_stops_at_a_multiple_of_the_risk_underwritten():
    structure = _structure(net_price=0.50, qty=2)
    max_loss_per_unit = structure["max_loss"] / (MULT * 2)
    stop = SETTINGS.strategy.carry_stop_fraction
    mark = _mark_at(structure, 0.50 + max_loss_per_unit * (stop + 0.05))
    assert mark.action == "stop_loss", (mark.action, mark.rationale)


def test_carry_exits_before_the_gamma_window_rather_than_at_expiry():
    """A multi-week thesis has no business being open on expiry day."""
    floor = SETTINGS.strategy.carry_min_hold_dte
    mark = _mark_at(_structure(net_price=0.50, dte=floor - 1), 0.45)
    assert mark.action == "time_exit", (mark.action, mark.rationale)

    still_running = _mark_at(_structure(net_price=0.50, dte=floor + 10), 0.45)
    assert still_running.action == "hold", still_running.rationale


def test_a_carry_position_holds_through_an_ordinary_drawdown():
    """The tactical debit rule cuts at 75% of value lost.

    Applied to a five week position that is down in week one, it would close
    everything before the thesis had any time to work, which is the failure
    mode that makes people say a strategy "does not work".
    """
    structure = _structure(net_price=0.50, qty=2)
    max_loss_per_unit = structure["max_loss"] / (MULT * 2)
    # Down 60% of the risk budget: painful, and well inside the stop.
    mark = _mark_at(structure, 0.50 + max_loss_per_unit * 0.60)
    assert mark.action == "hold", (mark.action, mark.rationale)


def _mark_at(structure: dict, cost_to_close: float) -> manager.Mark:
    """Mark the structure as though closing it cost `cost_to_close`.

    `_structure_value` sums sold legs positively and bought legs negatively, so
    loading the whole value onto one sold leg reproduces any net price.
    """
    mids = {leg["symbol"]: 0.0 for leg in structure["legs"]}
    sold = next(leg["symbol"] for leg in structure["legs"] if leg["side"] == "sell")
    mids[sold] = cost_to_close
    mark = manager.mark_structure(structure, mids)
    assert mark is not None
    return mark


# --- gates -----------------------------------------------------------------

def test_the_event_blackout_does_not_refuse_the_whole_sleeve():
    """A 45 day structure spans every catalyst on the calendar.

    The blackout exists to stop the agent writing short-dated premium directly
    into a known event. Applied to a multi-week position it would refuse the
    sleeve outright rather than protect it, because holding through events is
    what the position is for.
    """
    expiry = date.today() + timedelta(days=45)
    allowed, why = calendar_gate.check(
        sleeve="carry", underlying="SPY", expiry=expiry, is_credit=True
    )
    assert allowed, why

    # And the protection the gate does exist for is untouched.
    core_allowed, _ = calendar_gate.check(
        sleeve="core",
        underlying="*",
        expiry=expiry,
        is_credit=True,
    )
    assert not core_allowed, "the blackout must still apply to short premium"


def test_the_volatility_router_does_not_apply_to_carry():
    """G7 routes on the level of implied vol; carry trades the shape.

    A risk reversal is short put vol and long call vol at once, so a single
    premium reading cannot say which side of it is right. Routed as a credit
    structure it would be refused in exactly the cheap-vol regimes where a
    financed long position is most attractive.
    """
    proposal = replace(_reversal(net_price=0.50), vol_premium=-0.05)
    ok, why = risk._volatility_side_ok(proposal)
    assert ok, why

    # A short-dated credit spread in the same regime is still refused.
    core = replace(
        _reversal(net_price=0.50), sleeve="core", kind="put_credit_spread",
        vol_premium=-0.05,
    )
    core_ok, _ = risk._volatility_side_ok(core)
    assert not core_ok, "cheap vol must still block selling premium"


def test_carry_is_sized_against_its_own_sleeve_cap():
    """Carry must not be able to spend the convex sleeve's budget."""
    snap = risk.PortfolioSnapshot(
        equity=100_000.0, last_equity=100_000.0, cash=100_000.0,
        buying_power=200_000.0, open_risk=0.0, peak_equity=100_000.0,
        open_structures=0, trades_today=0,
    )
    qty, notes = risk.size_position(_reversal(net_price=0.50), snap)
    joined = " | ".join(notes)
    assert "carry sleeve room" in joined, joined
    assert "convex sleeve room" not in joined, joined
    assert qty >= 0


def test_the_best_possible_outcome_is_reported_as_a_gain():
    """A sign error here would not throw, it would invert the sleeve.

    `current` is the cost to close, so a winning package is cheaper, or pays
    more, to close than it cost to open. Reversed, the maximum gain reads as
    the maximum loss: every winner stops out and every loser is held to
    expiry, and the sleeve loses money while every gate still passes.
    """
    structure = _structure(net_price=0.50, qty=2)
    max_gain_total = structure["max_gain"]
    per_unit = max_gain_total / (MULT * 2)

    # Fully rallied: the call spread is worth its width and closing pays us.
    best = _mark_at(structure, 0.50 - per_unit)
    assert best.unrealized_pnl > 0, best.unrealized_pnl
    assert abs(best.unrealized_pnl - max_gain_total) < 1.0, best.unrealized_pnl
    assert best.action == "take_profit", best.action

    # Fully crashed: the put spread is at its width and closing costs us.
    max_loss_total = structure["max_loss"]
    worst = _mark_at(structure, 0.50 + max_loss_total / (MULT * 2))
    assert worst.unrealized_pnl < 0, worst.unrealized_pnl
    assert abs(worst.unrealized_pnl + max_loss_total) < 1.0, worst.unrealized_pnl


def test_a_control_arm_holds_only_the_sleeves_it_is_a_control_for():
    """Adding a sleeve to the base config must not leak into the control arms.

    When carry was added to BASE_RISK, every preset that predated it inherited
    a 3.5% carry budget. The "income only" arm quietly held multi-week risk
    reversals, and all seven non-carry variants reported the identical carry
    P&L to the dollar. The comparison the three accounts exist to make was
    gone, the backtest still ran, and nothing failed.

    So each arm is pinned to the sleeves it is supposed to isolate. A new
    sleeve added to BASE_RISK in future breaks this test rather than the
    experiment.
    """
    from engine.config import VARIANTS

    # variant -> the sleeves it is allowed to hold
    EXPECTED = {
        "barbell": {"core", "convex", "carry"},
        "levered": {"core", "convex", "carry"},
        "carry_led": {"core", "convex", "carry"},
        "convex_tilt": {"core", "convex"},
        "vrp_router": {"core", "convex"},
        "income_only": {"core"},
        "fat_credit": {"core"},
        "long_gamma": {"convex"},
    }
    assert set(EXPECTED) == set(VARIANTS), (
        f"a variant was added or renamed without saying which sleeves it holds: "
        f"{set(VARIANTS) ^ set(EXPECTED)}"
    )

    for name, allowed in EXPECTED.items():
        risk_cfg, _strategy = VARIANTS[name]
        caps = {
            "convex": risk_cfg.max_convex_open_risk_pct,
            "carry": risk_cfg.max_carry_open_risk_pct,
        }
        for sleeve, cap in caps.items():
            if sleeve in allowed:
                assert cap > 0, f"{name} is meant to hold {sleeve} but its cap is {cap}"
            else:
                assert cap == 0, (
                    f"{name} is a control arm without {sleeve}, but its cap is {cap}"
                )
        # Core has no cap of its own; its share is what turns it off.
        if "core" not in allowed:
            assert risk_cfg.core_risk_share == 0, name


def test_a_losing_carry_position_can_actually_be_closed():
    """The stop-loss has to be fillable in the case it exists for.

    A vertical's cost to close never changes sign: a short spread always costs
    something to buy back, a long spread is always worth something to sell. A
    risk reversal is short one spread and long another, so it does change sign.
    A carry position opened for a debit that then goes against us costs money
    to close, and dispatching the limit on the ENTRY price sends an order to
    sell it for a credit instead. That order can never fill, and the moment it
    matters is the crash the stop exists for.

    Exercised through close_package itself, reading back the limit it actually
    recorded, so the test cannot pass by agreeing with a copy of the logic.
    """
    structure = _structure(net_price=-3.63, qty=1)

    # Rallied: closing pays us, so the package is sold for a credit.
    rallied = _recorded_close_limit(structure, cost_to_close=-15.00)
    assert rallied < 0, f"a package that pays to close must be sold for a credit: {rallied}"

    # Crashed: closing costs us, so it must be bought back for a debit even
    # though the position was opened for one.
    crashed = _recorded_close_limit(structure, cost_to_close=+5.00)
    assert crashed > 0, (
        f"a package that costs money to close must be bought back for a debit, "
        f"got {crashed}"
    )


def _recorded_close_limit(structure: dict, cost_to_close: float) -> float:
    """Run close_package in dry run and return the limit price it journaled."""
    import json
    import tempfile
    from pathlib import Path

    from engine import closing, state

    tmp = Path(tempfile.mkdtemp()) / "close.db"
    state.init_db(tmp)
    saved_dry = closing.SETTINGS.__class__.dry_run
    closing.SETTINGS.__class__.dry_run = property(lambda self: True)
    state.SETTINGS.__class__.db_path = property(lambda self: tmp)
    try:
        ok, _detail = closing.close_package(
            {**structure, "id": 1}, cost_to_close, "stop_loss"
        )
        assert ok, "a dry-run close must report success"
        with state.db(tmp) as conn:
            row = conn.execute(
                "SELECT payload FROM orders ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row is not None, "close_package journaled nothing"
        return float(json.loads(row["payload"])["limit_price"])
    finally:
        closing.SETTINGS.__class__.dry_run = saved_dry
        state._INITIALISED.discard(tmp)


def test_the_close_payload_flips_all_four_legs():
    """Every leg has to reverse, not just the two that opened as shorts."""
    from engine import executor

    structure = _structure(net_price=-3.63, qty=1)
    payload = executor.build_close_payload(structure["legs"], 1, 4.00)

    assert len(payload["legs"]) == 4, payload["legs"]
    assert payload["order_class"] == "mleg"
    opened = {leg["symbol"]: leg["side"] for leg in structure["legs"]}
    for leg in payload["legs"]:
        assert leg["side"] != opened[leg["symbol"]], leg
        assert leg["position_intent"].endswith("_to_close"), leg


if __name__ == "__main__":
    mod = sys.modules[__name__]
    failed = 0
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        try:
            getattr(mod, name)()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failed} failed")
    sys.exit(1 if failed else 0)
