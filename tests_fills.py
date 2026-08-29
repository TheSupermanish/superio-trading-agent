"""Fill-ladder tests.

This module decides whether a resting order gets improved, abandoned, or left
alone. It runs rarely and only in conditions that are awkward to reproduce
live, which is exactly the combination that hides bugs until the day it
matters. Every branch is exercised here against a faked broker.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine import fills, state

TMP = Path(tempfile.mkdtemp()) / "test.db"


def _reset() -> None:
    if TMP.exists():
        TMP.unlink()
    state._INITIALISED.discard(TMP)
    state.init_db(TMP)


def _seed(net_price: float, net_mid: float, status: str = "pending") -> int:
    """One structure with a known entry price and a working order."""
    legs = [
        {"symbol": "SPY260904C00780000", "side": "buy", "ratio_qty": 1},
        {"symbol": "SPY260904C00785000", "side": "sell", "ratio_qty": 1},
    ]
    with state.db(TMP) as conn:
        cur = conn.execute(
            "INSERT INTO structures (opened_at, sleeve, underlying, kind, legs, qty,"
            " net_price, net_price_mid, max_loss, max_gain, status, client_order_id, thesis)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (state.utcnow(), "convex", "SPY", "call_debit_spread", json.dumps(legs), 3,
             net_price, net_mid, 300.0, 200.0, status, "coid-1", ""),
        )
        return int(cur.lastrowid)


def _order(status: str, age_seconds: int, limit_price: str = "1.00") -> dict:
    created = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return {
        "id": "order-1",
        "status": status,
        "limit_price": limit_price,
        "created_at": created.isoformat().replace("+00:00", "Z"),
    }


class FakeBroker:
    def __init__(self, order: dict | None) -> None:
        self.order = order
        self.cancelled: list[str] = []
        self.submitted: list[dict] = []

    def order_by_client_id(self, coid: str):
        return self.order

    def cancel_order(self, oid: str):
        self.cancelled.append(oid)

    def submit_order(self, payload: dict):
        self.submitted.append(payload)
        return {"id": "order-2"}


def _set_dry_run(value: bool) -> None:
    fills.SETTINGS.__class__.dry_run = property(lambda self: value)


def _install(monkey: FakeBroker, dry_run: bool = True) -> None:
    _set_dry_run(dry_run)
    fills.alpaca_cli.order_by_client_id = monkey.order_by_client_id
    fills.alpaca_cli.cancel_order = monkey.cancel_order
    fills.alpaca_cli.submit_order = monkey.submit_order
    fills.state.SETTINGS.__class__.db_path = property(lambda self: TMP)


# --- tests -----------------------------------------------------------------

def test_young_order_is_left_alone():
    _reset(); _seed(-1.01, -0.99)
    broker = FakeBroker(_order("new", age_seconds=5)); _install(broker, dry_run=False)
    actions = fills.walk()
    assert actions == [], actions
    assert not broker.cancelled, "a fresh order must not be cancelled"


def test_stale_order_is_repriced_toward_the_touch():
    _reset(); sid = _seed(-1.01, -0.99)
    broker = FakeBroker(_order("new", age_seconds=fills.MAX_REST_SECONDS + 30))
    _install(broker, dry_run=False)
    actions = fills.walk()
    assert any(a["action"] == "reprice" for a in actions), actions
    assert broker.cancelled == ["order-1"], "must cancel before resubmitting"
    assert broker.submitted, "must resubmit"
    new_price = float(broker.submitted[0]["limit_price"])
    # Debit structure: paying more than the mid, never more than the touch.
    assert 0.99 <= new_price <= 1.01, new_price


def test_dry_run_never_cancels_a_live_order():
    """Simulating must not touch the broker at all.

    Cancelling for real and then returning before the resubmit would orphan a
    working order, which is precisely what a dry run promises not to do.
    """
    _reset(); _seed(-1.01, -0.99)
    broker = FakeBroker(_order("new", age_seconds=fills.MAX_REST_SECONDS + 30))
    _install(broker, dry_run=True)
    fills.walk()
    assert not broker.cancelled, "dry run cancelled a live order"
    assert not broker.submitted, "dry run sent an order"


def test_ladder_never_pays_worse_than_the_underwritten_price():
    """The touch is the ceiling: risk sized the trade against exactly that."""
    _reset(); sid = _seed(-1.01, -0.99)
    for rung in fills.LADDER:
        price = abs(fills._ladder_price(
            {"net_price": -1.01, "net_price_mid": -0.99}, rung))
        assert price <= 1.01 + 1e-9, f"rung {rung} paid {price}, above the touch"


def test_credit_structure_keeps_its_sign():
    _reset()
    price = fills._ladder_price({"net_price": 0.40, "net_price_mid": 0.44}, 1.0)
    assert price < 0, "a credit must be sent as a negative net limit"
    assert abs(price) >= 0.40 - 1e-9, "must not accept less than the underwritten credit"


def test_exhausted_ladder_abandons_the_order():
    _reset(); sid = _seed(-1.01, -0.99)
    # Record as many prior attempts as the ladder has rungs, plus the original.
    with state.db(TMP) as conn:
        for _ in range(len(fills.LADDER) + 1):
            conn.execute(
                "INSERT INTO orders (ts, structure_id, intent, payload, status)"
                " VALUES (?,?,?,?,?)",
                (state.utcnow(), sid, "open", "{}", "accepted"),
            )
    broker = FakeBroker(_order("new", age_seconds=fills.MAX_REST_SECONDS + 30))
    _install(broker, dry_run=False)
    actions = fills.walk()
    assert any(a["action"] == "abandoned" for a in actions), actions
    assert broker.cancelled == ["order-1"]
    assert not broker.submitted, "must not resubmit after exhausting the ladder"
    with state.db(TMP) as conn:
        row = conn.execute("SELECT status FROM structures WHERE id=?", (sid,)).fetchone()
    assert row["status"] == "rejected", row["status"]


def test_filled_order_promotes_the_structure():
    _reset(); sid = _seed(-1.01, -0.99)
    broker = FakeBroker(_order("filled", age_seconds=200)); _install(broker, dry_run=False)
    actions = fills.walk()
    assert any(a["action"] == "filled" for a in actions), actions
    with state.db(TMP) as conn:
        row = conn.execute("SELECT status FROM structures WHERE id=?", (sid,)).fetchone()
    assert row["status"] == "open", row["status"]


def test_dead_order_marks_the_structure_rejected():
    _reset(); sid = _seed(-1.01, -0.99)
    broker = FakeBroker(_order("canceled", age_seconds=200)); _install(broker, dry_run=False)
    fills.walk()
    with state.db(TMP) as conn:
        row = conn.execute("SELECT status FROM structures WHERE id=?", (sid,)).fetchone()
    assert row["status"] == "rejected", row["status"]


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
