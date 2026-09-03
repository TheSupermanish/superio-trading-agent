"""Risk officer unit tests: the gates that must never regress."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta

from engine import calendar_gate, risk
from engine.config import SETTINGS
from engine.risk import PortfolioSnapshot, _is_defined_risk, evaluate, size_position
from engine.types import Leg, Proposal

EXP = date.today() + timedelta(days=3)


def leg(symbol, side, strike, is_call, mid=1.0):
    return Leg(symbol=symbol, side=side, strike=strike, expiry=EXP, is_call=is_call,
               mid=mid, bid=mid - 0.02, ask=mid + 0.02, delta=0.16)


def snap(equity=100_000.0, open_risk=0.0, structures=0, trades=0, last_equity=None):
    return PortfolioSnapshot(equity=equity, last_equity=last_equity or equity, cash=equity,
                             buying_power=equity * 4, open_risk=open_risk, peak_equity=equity,
                             open_structures=structures, trades_today=trades)


def prop(kind, legs, net, width, max_loss, max_gain, sleeve="core", net_mid=None):
    return Proposal(sleeve=sleeve, underlying="SPY", kind=kind, legs=legs, net_price=net,
                    net_price_mid=net if net_mid is None else net_mid,
                    width=width, max_loss_per_unit=max_loss, max_gain_per_unit=max_gain)


def test_credit_spread_is_defined_risk():
    p = prop("put_credit_spread",
             [leg("A", "sell", 500, False, 2.0), leg("B", "buy", 495, False, 1.0)],
             1.0, 5, 400, 100)
    ok, why = _is_defined_risk(p)
    assert ok, why


def test_debit_spread_is_defined_risk():
    # Bull call spread: short leg is ABOVE the long leg. Still defined risk.
    p = prop("call_debit_spread",
             [leg("A", "buy", 500, True, 3.0), leg("B", "sell", 505, True, 1.5)],
             -1.5, 5, 150, 350, sleeve="convex")
    ok, why = _is_defined_risk(p)
    assert ok, why


def test_naked_short_is_rejected():
    p = prop("naked_put", [leg("A", "sell", 500, False, 2.0)], 2.0, 5, 49_800, 200)
    ok, _ = _is_defined_risk(p)
    assert not ok


def test_mismatched_expiry_cover_is_rejected():
    long_leg = leg("B", "buy", 495, False, 1.0)
    object.__setattr__(long_leg, "expiry", EXP + timedelta(days=7))
    p = prop("calendar", [leg("A", "sell", 500, False, 2.0), long_leg], 1.0, 5, 400, 100)
    ok, _ = _is_defined_risk(p)
    assert not ok


def test_sizing_respects_per_trade_cap():
    p = prop("put_credit_spread",
             [leg("A", "sell", 500, False, 2.0), leg("B", "buy", 495, False, 1.0)],
             1.0, 5, 400, 100)
    qty, _ = size_position(p, snap())
    # Derived from the config rather than written in. Hardcoding "1" here meant
    # that raising the per-trade cap broke a test about whether the cap is
    # respected, which is not what it is for.
    cap = 100_000 * SETTINGS.risk.max_risk_per_trade_pct
    assert qty == int(cap // 400), (qty, cap)


def test_daily_kill_switch_blocks_entry():
    p = prop("put_credit_spread",
             [leg("A", "sell", 500, False, 2.0), leg("B", "buy", 495, False, 1.0)],
             1.0, 5, 400, 100)
    # A day one basis point past the switch, whatever the switch is set to.
    down = SETTINGS.risk.daily_loss_kill_pct + 0.001
    v = evaluate(p, snap(equity=100_000 * (1 - down), last_equity=100_000))
    assert not v.approved and "kill switch" in v.reasons[0], v.reasons


def test_thin_credit_is_rejected():
    # Liquid legs, but the credit is only 8% of the width.
    a = leg("A", "sell", 500, False, 2.00)
    b = leg("B", "buy", 495, False, 1.60)
    v = evaluate(prop("put_credit_spread", [a, b], 0.40, 5, 460, 40), snap())
    assert not v.approved, "thin credit should be rejected"
    assert "G5" in v.reasons[0] and "credit" in v.reasons[0], v.reasons


def test_illiquid_leg_is_rejected():
    a = leg("A", "sell", 500, False, 2.00)
    b = leg("B", "buy", 495, False, 0.20)  # 0.04 spread on a 0.20 mid = 20%
    v = evaluate(prop("put_credit_spread", [a, b], 1.80, 5, 320, 180), snap())
    assert not v.approved and "G4" in v.reasons[0], v.reasons


def test_risk_is_sized_off_the_touch_not_the_mid():
    """The conservative price must be the one that drives max loss."""
    p = prop("put_credit_spread",
             [leg("A", "sell", 500, False, 2.0), leg("B", "buy", 495, False, 1.0)],
             net=0.90, width=5, max_loss=410, max_gain=90, net_mid=1.00)
    assert p.net_price < p.net_price_mid, "touch price should be worse than mid"
    assert abs(p.slippage_budget - 0.10) < 1e-9, p.slippage_budget
    qty, notes = size_position(p, snap())
    # Sized on the touch (410), not the mid (400): a cap that divides evenly by
    # the mid must not divide evenly by the touch.
    cap = 100_000 * SETTINGS.risk.max_risk_per_trade_pct
    assert qty == int(cap // 410), (qty, notes)
    assert qty < int(cap // 400) or cap // 410 == cap // 400, \
        "sizing off the mid would have allowed more"


def test_credit_across_a_high_impact_event_is_blocked():
    """No writing premium across the payrolls print."""
    from datetime import date as _date
    from engine.calendar_gate import check
    ok, why = check("core", "SPY", _date(2026, 9, 4), is_credit=True,
                    now=__import__("datetime").datetime(
                        2026, 9, 3, 14, 0,
                        tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York")))
    assert not ok and "employment" in why, why


def test_convex_into_an_event_is_allowed():
    from datetime import date as _date
    from engine.calendar_gate import check
    ok, why = check("convex", "SPY", _date(2026, 9, 4), is_credit=False,
                    now=__import__("datetime").datetime(
                        2026, 9, 3, 14, 0,
                        tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York")))
    assert ok, why



def _near(symbol, side, strike, is_call, mid):
    """A liquid leg isolated from future calendar-gate fixtures."""
    l = leg(symbol, side, strike, is_call, mid)
    # These tests target G7, not the hard-coded competition calendar. Using
    # "tomorrow" made them start failing the day before payrolls even though
    # the volatility behavior had not changed.
    object.__setattr__(l, "expiry", date.today())
    return l


def test_will_not_buy_convexity_when_premium_is_expensive():
    """The trade that cost us the first session.

    A put debit spread was bought on the underlying whose implied vol sat 7.1
    points above realized. The three premium-selling structures made $34
    between them; that one lost $260.
    """
    p = prop("put_debit_spread",
             [_near("A", "buy", 292, False, 1.20), _near("B", "sell", 287, False, 0.60)],
             net=-0.60, width=5, max_loss=60, max_gain=440, sleeve="convex")
    p.vol_premium = 0.0714
    v = evaluate(p, snap())
    assert not v.approved, "bought expensive premium"
    assert "G7" in v.reasons[0] and "above realized" in v.reasons[0], v.reasons


def test_will_buy_convexity_when_premium_is_cheap():
    p = prop("call_debit_spread",
             [_near("A", "buy", 500, True, 3.0), _near("B", "sell", 505, True, 1.5)],
             net=-1.50, width=5, max_loss=150, max_gain=350, sleeve="convex")
    p.vol_premium = -0.03
    v = evaluate(p, snap())
    assert v.approved, v.reasons


@contextmanager
def only_this_gate():
    """Run a proposal past the gates with the event calendar emptied.

    These tests are about G7, the volatility router. They were passing only
    because "today plus three days" happened not to span a hard-coded catalyst,
    and started failing the morning the payrolls print came inside that window:
    G6 refused the structure before G7 was ever consulted, so a test about the
    volatility signal was reporting on the event blackout.

    A test whose result depends on today's date relative to a fixed calendar is
    not testing what it says. G6 has its own tests, with their own fixed dates.
    """
    saved = calendar_gate.EVENTS
    calendar_gate.EVENTS = ()
    calendar_gate._external_cache["at"] = datetime.now(calendar_gate.ET)
    calendar_gate._external_cache["events"] = ()
    try:
        yield
    finally:
        calendar_gate.EVENTS = saved
        calendar_gate._external_cache["at"] = None


def test_will_not_sell_premium_when_it_is_underpriced():
    a = _near("A", "sell", 500, False, 2.00)
    b = _near("B", "buy", 495, False, 0.90)
    p = prop("put_credit_spread", [a, b], net=1.10, width=5, max_loss=390, max_gain=110)
    p.vol_premium = -0.05
    with only_this_gate():
        v = evaluate(p, snap())
    assert not v.approved and "below realized" in v.reasons[0], v.reasons


def test_no_volatility_reading_does_not_block():
    """A missing signal must not become an accidental trading halt."""
    a = _near("A", "sell", 500, False, 2.00)
    b = _near("B", "buy", 495, False, 0.90)
    p = prop("put_credit_spread", [a, b], net=1.10, width=5, max_loss=390, max_gain=110)
    p.vol_premium = None
    with only_this_gate():
        v = evaluate(p, snap())
    assert v.approved, v.reasons


def test_a_diary_variant_can_never_reach_the_broker():
    """Diary presets exist to be measured, not traded.

    The guarantee has to hold against the environment, not alongside it: no
    combination of DRY_RUN and LIVE_FROM may arm a variant that has no account
    behind it. So the check lives in the same property the executor reads.
    """
    import os

    from engine import config

    keys = ("STRATEGY_VARIANT", "DRY_RUN", "LIVE_FROM", "SUPERIO_DB")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        os.environ["DRY_RUN"] = "false"
        os.environ["LIVE_FROM"] = "2020-01-01T00:00:00Z"  # long past: would arm
        # The suite runs against a scratch journal; this check is about where a
        # diary book files itself when nothing is overriding that.
        os.environ.pop("SUPERIO_DB", None)
        for variant in sorted(set(config.VARIANTS) - config.LIVE_VARIANTS):
            os.environ["STRATEGY_VARIANT"] = variant
            settings = config.load_settings()
            assert settings.diary, variant
            assert settings.dry_run, f"{variant} armed itself with no account"
            assert settings.db_path.name == f"diary-{variant}.db", settings.db_path
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_a_live_variant_still_arms_normally():
    """The diary guard must not quietly disarm the accounts we do trade."""
    import os

    from engine import config

    saved = {k: os.environ.get(k) for k in ("STRATEGY_VARIANT", "DRY_RUN", "LIVE_FROM")}
    try:
        os.environ["DRY_RUN"] = "false"
        os.environ["LIVE_FROM"] = "2020-01-01T00:00:00Z"
        for variant in sorted(config.LIVE_VARIANTS):
            os.environ["STRATEGY_VARIANT"] = variant
            settings = config.load_settings()
            assert not settings.diary, variant
            assert not settings.dry_run, f"{variant} refused to arm"
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_a_diary_book_is_sized_off_its_own_stake():
    """A diary book borrows the live account's keys, not its money.

    Sizing a 50,000 book against the live account's balance would inflate
    every diary position and make the comparison it exists for worthless.
    """
    import tempfile
    from pathlib import Path

    from engine import config, loop, state

    tmp = Path(tempfile.mkdtemp()) / "diary.db"
    state.init_db(tmp)
    try:
        account = loop._diary_account(tmp)
        assert account["equity"] == config.DIARY_EQUITY, account
        assert account["last_equity"] == config.DIARY_EQUITY, account

        with state.db(tmp) as conn:
            conn.execute(
                "INSERT INTO structures (opened_at, closed_at, sleeve, underlying,"
                " kind, legs, qty, net_price, max_loss, max_gain, status, realized_pnl)"
                " VALUES (?, date('now'), 'core', 'SPY', 'put_credit_spread', '[]',"
                " 1, 0.3, 70.0, 30.0, 'closed', 250.0)",
                (state.utcnow(),),
            )

        account = loop._diary_account(tmp)
        assert account["equity"] == config.DIARY_EQUITY + 250.0, account
        # Closed today, so the day's P&L is the whole gain.
        assert account["last_equity"] == config.DIARY_EQUITY, account
    finally:
        state._INITIALISED.discard(tmp)


def test_an_entry_that_never_filled_does_not_spend_a_trade_slot():
    """A released entry has no position, no risk and no P&L.

    Counting it spends a slot on a trade that did not happen. Five of them once
    ate five of eight slots and the agent stood down for the afternoon holding
    a book of three, with four fifths of its risk budget free.
    """
    import tempfile
    from pathlib import Path

    from engine import state

    tmp = Path(tempfile.mkdtemp()) / "budget.db"
    state.init_db(tmp)

    def add(status: str) -> None:
        with state.db(tmp) as conn:
            conn.execute(
                "INSERT INTO structures (opened_at, sleeve, underlying, kind, legs,"
                " qty, net_price, max_loss, max_gain, status)"
                " VALUES (?, 'core', 'SPY', 'put_credit_spread', '[]', 1, 0.3, 70, 30, ?)",
                (state.utcnow(), status),
            )

    try:
        for status in ("open", "closed", "pending"):
            add(status)
        for _ in range(5):
            add("rejected")

        counted = state.trades_opened_today(include_simulated=False, db_path=tmp)
        assert counted == 3, counted
        failed = state.failed_entries_today(db_path=tmp)
        assert failed == 5, failed
    finally:
        state._INITIALISED.discard(tmp)


def test_failed_entries_are_still_bounded():
    """Not spending a trade slot is not the same as being free.

    A session priced where nothing fills must give up rather than churn orders
    all day, so G2 refuses once the failed-entry ceiling is reached.
    """
    from engine.config import SETTINGS

    snap = risk.PortfolioSnapshot(
        equity=100_000.0, last_equity=100_000.0, cash=100_000.0,
        buying_power=200_000.0, open_risk=0.0, peak_equity=100_000.0,
        open_structures=0, trades_today=0,
        failed_today=SETTINGS.risk.max_failed_entries_per_day,
    )
    ok, why = risk._budget_ok(snap)
    assert not ok, why
    assert "failed to fill" in why, why

    snap_ok = replace(snap, failed_today=SETTINGS.risk.max_failed_entries_per_day - 1)
    ok, why = risk._budget_ok(snap_ok)
    assert ok, why


def test_the_book_cannot_risk_more_than_the_kill_switch_tolerates():
    """Open risk has to stay inside the drawdown that stands the agent down.

    Every structure's maximum loss is known, so fully deployed the worst case
    is the open-risk cap. If that cap is allowed above the total-drawdown kill
    switch then one gap through every position ends the event, and the kill
    switch fires having protected nothing. This is the invariant that makes
    "should we deploy more" a bounded question rather than an open one.
    """
    from engine.config import VARIANTS

    for name, (risk_cfg, _strategy) in VARIANTS.items():
        assert risk_cfg.max_open_risk_pct < risk_cfg.total_drawdown_kill_pct, (
            f"{name} can hold {risk_cfg.max_open_risk_pct:.1%} of risk against a "
            f"{risk_cfg.total_drawdown_kill_pct:.1%} kill switch"
        )


def test_short_premium_cannot_eat_the_whole_risk_budget():
    """The reason the book was capped at a one percent week.

    Only convex and carry had sleeve caps, so premium selling was bounded by
    nothing but the portfolio total. It is the cheapest structure to build and
    the most often available, so it filled the budget first and the two sleeves
    with real payoffs competed for the remainder. At the credit floor a credit
    spread wins about 0.22 per unit risked, so a book fully deployed in premium
    has a theoretical best week near 1.3% whatever else is true.
    """
    from engine.config import SETTINGS

    r = SETTINGS.risk
    assert 0 < r.max_core_open_risk_pct < r.max_open_risk_pct, r.max_core_open_risk_pct

    headroom = r.max_open_risk_pct - r.max_core_open_risk_pct
    assert headroom > r.max_core_open_risk_pct, (
        "most of the budget must be reachable by the sleeves that pay more than "
        "a fraction of what they risk"
    )


def test_every_sleeve_is_capped_by_name():
    """A sleeve with no cap is a sleeve that can consume the others.

    Sizing looks its cap up by sleeve name, so a sleeve added to the Sleeve
    type without a matching cap is silently unbounded within the portfolio
    total. That is exactly how core came to hold 85% of the budget.
    """
    from typing import get_args

    from engine.config import SETTINGS
    from engine.types import Sleeve

    r = SETTINGS.risk
    for sleeve in get_args(Sleeve):
        assert hasattr(r, f"max_{sleeve}_open_risk_pct"), (
            f"sleeve {sleeve!r} has no max_{sleeve}_open_risk_pct, so sizing "
            f"cannot bound it"
        )


if __name__ == "__main__":
    import sys
    mod = sys.modules[__name__]
    failed = 0
    for name in [n for n in dir(mod) if n.startswith("test_")]:
        try:
            getattr(mod, name)()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {name}: {exc}")
    print(f"\n{failed} failed")
    sys.exit(1 if failed else 0)
