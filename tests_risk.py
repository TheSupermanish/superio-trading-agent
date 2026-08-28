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


def prop(kind, legs, net, width, max_loss, max_gain, sleeve="core"):
    return Proposal(sleeve=sleeve, underlying="SPY", kind=kind, legs=legs, net_price=net,
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
    assert not v.approved and "halted" in v.reasons[0]


def test_thin_credit_is_rejected():
    # Liquid legs, but the credit is only 8% of the width.
    a = leg("A", "sell", 500, False, 2.00)
    b = leg("B", "buy", 495, False, 1.60)
    v = evaluate(prop("put_credit_spread", [a, b], 0.40, 5, 460, 40), snap())
    assert not v.approved, "thin credit should be rejected"
    assert "credit" in v.reasons[0], v.reasons


def test_illiquid_leg_is_rejected():
    a = leg("A", "sell", 500, False, 2.00)
    b = leg("B", "buy", 495, False, 0.20)  # 0.04 spread on a 0.20 mid = 20%
    v = evaluate(prop("put_credit_spread", [a, b], 1.80, 5, 320, 180), snap())
    assert not v.approved and "liquidity" in v.reasons[0], v.reasons


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
