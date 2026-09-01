"""Risk officer unit tests: the gates that must never regress."""

from datetime import date, timedelta

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
    assert qty == 1, qty  # 0.75% of 100k = 750, // 400 -> 1


def test_daily_kill_switch_blocks_entry():
    p = prop("put_credit_spread",
             [leg("A", "sell", 500, False, 2.0), leg("B", "buy", 495, False, 1.0)],
             1.0, 5, 400, 100)
    v = evaluate(p, snap(equity=96_000, last_equity=100_000))
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
    assert qty == 1, (qty, notes)


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
    """A liquid leg expiring tomorrow, clear of the week's catalysts."""
    l = leg(symbol, side, strike, is_call, mid)
    object.__setattr__(l, "expiry", date.today() + timedelta(days=1))
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


def test_will_not_sell_premium_when_it_is_underpriced():
    a = _near("A", "sell", 500, False, 2.00)
    b = _near("B", "buy", 495, False, 0.90)
    p = prop("put_credit_spread", [a, b], net=1.10, width=5, max_loss=390, max_gain=110)
    p.vol_premium = -0.05
    v = evaluate(p, snap())
    assert not v.approved and "below realized" in v.reasons[0], v.reasons


def test_no_volatility_reading_does_not_block():
    """A missing signal must not become an accidental trading halt."""
    a = _near("A", "sell", 500, False, 2.00)
    b = _near("B", "buy", 495, False, 0.90)
    p = prop("put_credit_spread", [a, b], net=1.10, width=5, max_loss=390, max_gain=110)
    p.vol_premium = None
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

    saved = {k: os.environ.get(k) for k in ("STRATEGY_VARIANT", "DRY_RUN", "LIVE_FROM")}
    try:
        os.environ["DRY_RUN"] = "false"
        os.environ["LIVE_FROM"] = "2020-01-01T00:00:00Z"  # long past: would arm
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
