"""Adoption tests.

Rebuilding the book from the broker is the path that runs when the journal is
gone, which is exactly when nobody is watching closely. Every test here pins a
case where adopting the wrong thing would be worse than adopting nothing.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import json

from engine import adopt, state


def _pos(symbol: str, qty: int, entry: float, price: float = 1.0) -> dict:
    return {
        "symbol": symbol, "qty": str(qty), "avg_entry_price": str(entry),
        "current_price": str(price), "asset_class": "us_option",
    }


def _occ(root: str, days: int, kind: str, strike: float) -> str:
    expiry = (date.today() + timedelta(days=days)).strftime("%y%m%d")
    return f"{root}{expiry}{kind}{int(strike * 1000):08d}"


def _fresh_db() -> Path:
    tmp = Path(tempfile.mkdtemp()) / "adopt.db"
    state.init_db(tmp)
    state.SETTINGS.__class__.db_path = property(lambda self: tmp)
    return tmp


# --- symbol parsing --------------------------------------------------------

def test_occ_symbols_parse():
    assert adopt.parse_occ("SPY260903C00768000") == ("SPY", date(2026, 9, 3), True, 768.0)
    assert adopt.parse_occ("IWM260902P00290000") == ("IWM", date(2026, 9, 2), False, 290.0)


def test_a_symbol_that_is_not_an_option_is_refused():
    """An equity ticker must never be parsed into a strike."""
    assert adopt.parse_occ("SPY") is None
    assert adopt.parse_occ("") is None
    assert adopt.parse_occ("SPY260903X00768000") is None, "X is not a call or a put"


# --- what may be adopted ---------------------------------------------------

def test_a_covered_credit_spread_is_adopted_with_its_real_entry_price():
    """The entry price is recoverable, so it must not be guessed.

    avg_entry_price is on every position. Reconstructing the package price from
    it means the exit rules measure against what was actually paid rather than
    a mark, which is the difference between a profit target that means
    something and one that fires at random.
    """
    _fresh_db()
    short, long = _occ("SPY", 5, "C", 770), _occ("SPY", 5, "C", 775)
    created = adopt.adopt([_pos(short, -3, 1.20), _pos(long, 3, 0.40)])

    assert len(created) == 1, created
    rows = state.live_structures()
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "call_credit_spread", row["kind"]
    assert row["qty"] == 3
    assert abs(row["net_price"] - 0.80) < 1e-6, row["net_price"]
    # Five wide, sold for 0.80, three contracts.
    assert abs(row["max_loss"] - (5 - 0.80) * 100 * 3) < 1e-6, row["max_loss"]
    assert row["status"] == "open"


def test_a_debit_spread_is_named_and_priced_as_one():
    _fresh_db()
    long, short = _occ("SPY", 4, "P", 760), _occ("SPY", 4, "P", 750)
    adopt.adopt([_pos(long, 2, 3.00), _pos(short, -2, 1.00)])

    row = state.live_structures()[0]
    assert row["kind"] == "put_debit_spread", row["kind"]
    assert abs(row["net_price"] + 2.00) < 1e-6, row["net_price"]


def test_a_four_legged_package_is_adopted_as_a_condor():
    _fresh_db()
    legs = [
        _pos(_occ("SPY", 6, "P", 750), 5, 1.00),
        _pos(_occ("SPY", 6, "P", 755), -5, 1.80),
        _pos(_occ("SPY", 6, "C", 775), -5, 1.70),
        _pos(_occ("SPY", 6, "C", 780), 5, 0.90),
    ]
    adopt.adopt(legs)
    row = state.live_structures()[0]
    assert row["kind"] == "iron_condor", row["kind"]
    assert row["qty"] == 5


def test_a_long_dated_package_is_charged_to_the_carry_sleeve():
    """Sleeve is inferred from what the position is, not from what opened it."""
    _fresh_db()
    adopt.adopt([
        _pos(_occ("SPY", 45, "C", 770), 1, 6.00),
        _pos(_occ("SPY", 45, "C", 790), -1, 2.00),
    ])
    assert state.live_structures()[0]["sleeve"] == "carry"


# --- what may not ----------------------------------------------------------

def test_an_uncovered_short_is_never_adopted():
    """The one case where adopting nothing is the safe answer.

    Journaling a naked short would tell the risk officer the account holds a
    defined-risk structure when it does not, and every sizing decision after
    that would be made against a comfortable lie. It stays an orphan and stays
    reported.
    """
    _fresh_db()
    created = adopt.adopt([_pos(_occ("SPY", 5, "C", 770), -3, 1.20)])
    assert created == [], created
    assert state.live_structures() == []


def test_mismatched_leg_counts_are_not_adopted():
    """Three short against one long is not a spread, whatever it looks like."""
    _fresh_db()
    created = adopt.adopt([
        _pos(_occ("SPY", 5, "C", 770), -3, 1.20),
        _pos(_occ("SPY", 5, "C", 775), 1, 0.40),
    ])
    assert created == [], created


def test_legs_in_different_expiries_are_not_one_structure():
    """A calendar has no defined loss this module could verify."""
    _fresh_db()
    created = adopt.adopt([
        _pos(_occ("SPY", 5, "C", 770), -2, 1.20),
        _pos(_occ("SPY", 30, "C", 770), 2, 4.00),
    ])
    assert created == [], created
    assert state.live_structures() == []


def test_an_adopted_structure_charges_the_risk_budget():
    """Adoption exists so the book stops being invisible, including to sizing."""
    _fresh_db()
    adopt.adopt([
        _pos(_occ("SPY", 5, "C", 770), -3, 1.20),
        _pos(_occ("SPY", 5, "C", 775), 3, 0.40),
    ])
    assert state.open_risk_total() == (5 - 0.80) * 100 * 3, state.open_risk_total()


def test_two_structures_netted_into_one_expiry_are_both_adopted():
    """The broker reports net positions, not the packages that created them.

    A twelve lot iron condor and a three lot call spread in the same expiry
    arrive as fifteen short calls beside twelve short puts. Asking for a single
    structure out of that fails on the quantity mismatch, and the whole expiry
    was left unadopted: on the judged account that was four live legs the exit
    rules could not see, on the day the nearer of them expired.
    """
    _fresh_db()
    created = adopt.adopt([
        _pos(_occ("SPY", 5, "C", 768), -15, 0.90),
        _pos(_occ("SPY", 5, "C", 769), 15, 0.60),
        _pos(_occ("SPY", 5, "P", 755), 12, 0.80),
        _pos(_occ("SPY", 5, "P", 756), -12, 1.10),
    ])

    assert len(created) == 2, created
    rows = sorted(state.live_structures(), key=lambda r: r["qty"])
    assert [r["kind"] for r in rows] == ["put_credit_spread", "call_credit_spread"], \
        [r["kind"] for r in rows]
    assert [r["qty"] for r in rows] == [12, 15], [r["qty"] for r in rows]


def test_two_same_type_verticals_netted_together_are_adopted():
    """Two put spreads must not become one invisible four-leg orphan."""
    _fresh_db()
    created = adopt.adopt([
        _pos(_occ("IWM", 1, "P", 284), 4, 0.23),
        _pos(_occ("IWM", 1, "P", 285), -4, 0.14),
        _pos(_occ("IWM", 1, "P", 286), -4, 0.38),
        _pos(_occ("IWM", 1, "P", 293), 4, 1.46),
    ])
    assert len(created) == 2, created
    assert len(state.live_structures()) == 2


def test_a_butterfly_is_left_alone_rather_than_mispriced():
    """Three strikes on one side is not a vertical and its loss is not the width.

    Adopting it as one would understate or overstate the maximum loss, and the
    risk officer sizes everything else against that number.
    """
    _fresh_db()
    created = adopt.adopt([
        _pos(_occ("SPY", 5, "C", 765), 1, 3.00),
        _pos(_occ("SPY", 5, "C", 770), -2, 1.50),
        _pos(_occ("SPY", 5, "C", 775), 1, 0.60),
    ])
    assert created == [], created
    assert state.live_structures() == []


def test_a_position_being_closed_is_not_adopted_back():
    """The bug that spent an afternoon failing to close the same spread.

    A structure closes at its profit target, the order is sent, and the legs
    are still at the broker until it settles. The structure is now marked
    closed, so it no longer claims them. Reconciliation sees unclaimed legs and
    adopts them, recreating the position that was just exited. The new entry
    then tries to close, and cannot, because the original close order holds the
    quantity: every pass logs an error and nothing resolves.
    """
    _fresh_db()
    short, long = _occ("SPY", 1, "P", 756), _occ("SPY", 1, "P", 755)

    # A structure closed a minute ago, still holding those legs.
    with state.db() as conn:
        conn.execute(
            "INSERT INTO structures (opened_at, closed_at, sleeve, underlying, kind,"
            " legs, qty, net_price, max_loss, max_gain, status, realized_pnl,"
            " close_reason) VALUES (?, ?, 'core', 'SPY', 'put_credit_spread', ?, 12,"
            " 0.10, 1080, 120, 'closed', 72.0, 'take_profit')",
            (
                state.utcnow(),
                state.utcnow(),
                json.dumps([
                    {"symbol": short, "side": "sell", "strike": 756, "expiry": "x", "is_call": False},
                    {"symbol": long, "side": "buy", "strike": 755, "expiry": "x", "is_call": True},
                ]),
            ),
        )

    created = adopt.adopt([_pos(short, -12, 1.10), _pos(long, 12, 1.00)])
    assert created == [], "the legs of a just-closed structure must not be adopted"


def test_a_leg_committed_to_a_working_order_is_not_adopted():
    """Quantity held by an order means something is already acting on it.

    Adopting it puts two journal entries on one position, and the second can
    never close because the first holds the quantity.
    """
    _fresh_db()
    short, long = _occ("SPY", 4, "C", 770), _occ("SPY", 4, "C", 775)
    legs = [_pos(short, -3, 1.20), _pos(long, 3, 0.40)]
    legs[0]["qty_available"] = "0"

    created = adopt.adopt(legs)
    assert created == [], created
    assert state.live_structures() == []


def test_a_settled_orphan_is_still_adopted():
    """The guards must not stop adoption doing its job.

    A leg with its full quantity available and no recent close behind it is
    exactly what adoption exists for.
    """
    _fresh_db()
    short, long = _occ("SPY", 4, "C", 770), _occ("SPY", 4, "C", 775)
    legs = [_pos(short, -3, 1.20), _pos(long, 3, 0.40)]
    for leg in legs:
        leg["qty_available"] = leg["qty"]

    created = adopt.adopt(legs)
    assert len(created) == 1, created


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
