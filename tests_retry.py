"""Transient-failure retry tests.

Six Alpaca clock calls timed out in one day and two killed a whole trading
pass. The retry that fixes it had never once fired in production, which makes
it exactly the kind of code that looks fine until the morning it matters.

These force every branch: retry a transient failure, refuse to retry a
permanent one, and never retry a write.
"""

from __future__ import annotations

import sys
import time

from engine import alpaca_cli


class FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def install(sequence: list[FakeProc]) -> dict:
    """Replace the subprocess call with a scripted sequence of results."""
    state = {"calls": 0, "sleeps": []}
    remaining = list(sequence)

    def fake_run(*_a, **_k):
        state["calls"] += 1
        return remaining.pop(0) if remaining else FakeProc(0, "{}")

    alpaca_cli.subprocess.run = fake_run
    alpaca_cli.time.sleep = lambda s: state["sleeps"].append(s)
    alpaca_cli.cli_path = lambda: "/usr/local/bin/alpaca"
    return state


TIMEOUT = FakeProc(1, "", '{"error":"could not reach: context deadline exceeded"}')
DENIED = FakeProc(1, "", '{"error":"insufficient buying power"}')
OK = FakeProc(0, '{"is_open": true}')


def test_transient_failure_is_retried_then_succeeds():
    state = install([TIMEOUT, TIMEOUT, OK])
    result = alpaca_cli.run(["clock"], retries=3)
    assert result == {"is_open": True}, result
    assert state["calls"] == 3, state["calls"]
    assert len(state["sleeps"]) == 2, state["sleeps"]
    assert state["sleeps"] == sorted(state["sleeps"]), "backoff must not shrink"


def test_permanent_failure_is_not_retried():
    """Insufficient buying power will still be true in two seconds."""
    state = install([DENIED, OK])
    try:
        alpaca_cli.run(["account", "get"], retries=3)
        raise AssertionError("should have raised")
    except alpaca_cli.AlpacaCliError:
        pass
    assert state["calls"] == 1, f"retried a permanent error {state['calls']} times"


def test_retries_are_bounded():
    state = install([TIMEOUT] * 10)
    try:
        alpaca_cli.run(["clock"], retries=2)
        raise AssertionError("should have raised")
    except alpaca_cli.AlpacaCliError:
        pass
    assert state["calls"] == 3, state["calls"]  # initial try plus two retries


def test_writes_never_retry():
    """Repeating an order after an ambiguous failure can trade twice."""
    state = install([TIMEOUT, OK])
    try:
        alpaca_cli.submit_order({"symbol": "SPY", "qty": "1"})
        raise AssertionError("should have raised")
    except alpaca_cli.AlpacaCliError:
        pass
    assert state["calls"] == 1, f"order submission retried {state['calls']} times"


def test_a_hung_cli_is_not_silent():
    """A process that never returns must raise, not hang the loop forever."""
    def hang(*_a, **_k):
        raise alpaca_cli.subprocess.TimeoutExpired(cmd="alpaca", timeout=60)

    alpaca_cli.subprocess.run = hang
    alpaca_cli.cli_path = lambda: "/usr/local/bin/alpaca"
    try:
        alpaca_cli.run(["clock"], retries=0)
        raise AssertionError("should have raised")
    except alpaca_cli.AlpacaCliError as exc:
        assert "timed out" in str(exc), str(exc)


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
