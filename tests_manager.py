"""Exit-manager tests.

The manager decides when a position is closed: profit target, stop, or the
flatten that runs before Alpaca's assignment window. None of it fires unless a
position is open and moving, which is the hardest state to reproduce on demand
and therefore the easiest place for a bug to survive until it costs money.

Every exit rule is checked here against hand-priced structures, including the
arithmetic of unrealised P&L, which is what the dashboard and the write-up
both report.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from engine import manager
from engine.config import SETTINGS

ET = ZoneInfo("America/New_York")
P = SETTINGS.strategy


def credit_structure(entry_credit: float, qty: int = 2, dte: int = 3) -> dict:
    """Short put spread: sell the 500, buy the 495."""
    expiry = (datetime.now(ET).date() + timedelta(days=dte)).isoformat()
    return {
        "id": 1, "qty": qty, "sleeve": "core", "underlying": "SPY",
        "kind": "put_credit_spread", "status": "open",
        "net_price": entry_credit,
        "max_gain": entry_credit * 100 * qty,
        "legs": [
            {"symbol": "SHORT", "side": "sell", "ratio_qty": 1, "strike": 500,
             "expiry": expiry, "is_call": False},
            {"symbol": "LONG", "side": "buy", "ratio_qty": 1, "strike": 495,
             "expiry": expiry, "is_call": False},
        ],
    }


def debit_structure(entry_debit: float, qty: int = 2, dte: int = 3) -> dict:
    """Long call spread: buy the 500, sell the 505."""
    expiry = (datetime.now(ET).date() + timedelta(days=dte)).isoformat()
    return {
        "id": 2, "qty": qty, "sleeve": "convex", "underlying": "QQQ",
        "kind": "call_debit_spread", "status": "open",
        "net_price": -entry_debit,
        "max_gain": (5 - entry_debit) * 100 * qty,
        "legs": [
            {"symbol": "LONG", "side": "buy", "ratio_qty": 1, "strike": 500,
             "expiry": expiry, "is_call": True},
            {"symbol": "SHORT", "side": "sell", "ratio_qty": 1, "strike": 505,
             "expiry": expiry, "is_call": True},
        ],
    }


def mids(short: float, long_: float) -> dict[str, float]:
    return {"SHORT": short, "LONG": long_}


# --- credit structures -----------------------------------------------------

def test_credit_take_profit_at_the_target():
    s = credit_structure(1.00)
    # Bought back for 0.40 -> 60% of the credit captured, target is 55%.
    m = manager.mark_structure(s, mids(short=0.50, long_=0.10))
    assert m.action == "take_profit", (m.action, m.rationale)
    assert abs(m.unrealized_pnl - (1.00 - 0.40) * 100 * 2) < 1e-6, m.unrealized_pnl


def test_credit_holds_below_the_target():
    s = credit_structure(1.00)
    # Costs 0.70 to close: only 30% captured.
    m = manager.mark_structure(s, mids(short=0.80, long_=0.10))
    assert m.action == "hold", (m.action, m.rationale)


def test_credit_stops_out_at_the_multiple():
    s = credit_structure(1.00)
    # Costs 2.10 to close, above the 2.0x stop.
    m = manager.mark_structure(s, mids(short=2.30, long_=0.20))
    assert m.action == "stop_loss", (m.action, m.rationale)
    assert m.unrealized_pnl < 0, m.unrealized_pnl


def test_credit_loss_never_exceeds_the_width():
    """A defined-risk spread cannot lose more than width less credit."""
    s = credit_structure(1.00)
    m = manager.mark_structure(s, mids(short=5.00, long_=0.00))  # fully in the money
    worst = -(5.0 - 1.00) * 100 * 2
    assert m.unrealized_pnl >= worst - 1e-6, (m.unrealized_pnl, worst)


# --- debit structures ------------------------------------------------------

def test_debit_take_profit_at_the_target():
    s = debit_structure(1.00)
    # Worth 2.30 against 1.00 paid: up 130%, target is 120%.
    m = manager.mark_structure(s, mids(short=0.20, long_=2.50))
    assert m.action == "take_profit", (m.action, m.rationale)
    assert m.unrealized_pnl > 0, m.unrealized_pnl


def test_debit_stops_when_it_has_bled_out():
    s = debit_structure(1.00)
    # Worth 0.20 against 1.00 paid: 80% of the premium is gone.
    m = manager.mark_structure(s, mids(short=0.05, long_=0.25))
    assert m.action == "stop_loss", (m.action, m.rationale)


def test_debit_holds_in_the_middle():
    s = debit_structure(1.00)
    m = manager.mark_structure(s, mids(short=0.30, long_=1.60))  # worth 1.30
    assert m.action == "hold", (m.action, m.rationale)


def test_debit_loss_never_exceeds_the_premium_paid():
    s = debit_structure(1.00)
    m = manager.mark_structure(s, mids(short=0.00, long_=0.00))  # worthless
    assert abs(m.unrealized_pnl - (-1.00 * 100 * 2)) < 1e-6, m.unrealized_pnl


# --- the assignment flatten ------------------------------------------------

def test_expiry_day_holds_before_the_flatten_window():
    s = credit_structure(1.00, dte=0)
    m = manager.mark_structure(s, mids(short=0.80, long_=0.10))  # would otherwise hold
    if manager.past_flatten_time():
        assert m.action == "time_stop"
    else:
        assert m.action == "hold", (m.action, m.rationale)


def test_flatten_window_boundary():
    """15:00 ET is the line: Alpaca starts assigning at 15:30."""
    assert not manager.past_flatten_time(datetime(2026, 9, 4, 14, 59, tzinfo=ET))
    assert manager.past_flatten_time(datetime(2026, 9, 4, 15, 0, tzinfo=ET))
    assert manager.past_flatten_time(datetime(2026, 9, 4, 15, 31, tzinfo=ET))
    assert manager.FLATTEN_BEFORE_ET < manager.ASSIGNMENT_WINDOW_ET, \
        "we must flatten before Alpaca starts assigning"


def test_profit_target_beats_the_time_stop():
    """A winner on expiry day is taken as a win, not logged as a time stop."""
    s = credit_structure(1.00, dte=0)
    m = manager.mark_structure(s, mids(short=0.50, long_=0.10))
    assert m.action == "take_profit", (m.action, m.rationale)


def test_missing_quote_returns_no_mark():
    """A structure we cannot price must not be acted on."""
    s = credit_structure(1.00)
    assert manager.mark_structure(s, {"SHORT": 0.50}) is None


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
