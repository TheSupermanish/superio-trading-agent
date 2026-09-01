"""Thin wrapper around the official Alpaca CLI.

Execution deliberately goes through the CLI rather than the Python SDK. Every
order the agent sends is therefore reproducible as a shell command a judge can
paste into a terminal, and the JSON on stdout is the same JSON we journal.

Multi-leg option orders use `alpaca api POST /v2/orders` because the generated
`order submit` command does not model leg arrays.
"""

from __future__ import annotations

import json
import os
import logging
import shutil
import subprocess
import time
from typing import Any

from engine.config import SETTINGS

log = logging.getLogger(__name__)


class AlpacaCliError(RuntimeError):
    def __init__(self, message: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


def cli_path() -> str | None:
    return shutil.which("alpaca")


def available() -> bool:
    return cli_path() is not None


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["ALPACA_API_KEY"] = SETTINGS.api_key
    env["ALPACA_SECRET_KEY"] = SETTINGS.secret_key
    env["ALPACA_LIVE_TRADE"] = "false" if SETTINGS.paper else "true"
    env["ALPACA_QUIET"] = "true"
    return env


#: Network hiccups reaching Alpaca are routine: six clock calls timed out in a
#: single day here, and two of them killed an entire trading pass. Reads are
#: safe to repeat, so they are retried rather than allowed to abort a pass.
TRANSIENT = (
    "context deadline exceeded",
    "connection reset",
    "connection refused",
    "no such host",
    "timeout",
    "temporary failure",
    "EOF",
    "502",
    "503",
    "504",
)


def _is_transient(detail: dict[str, Any], stderr: str) -> bool:
    haystack = f"{detail.get('error', '')} {stderr}".lower()
    return any(marker.lower() in haystack for marker in TRANSIENT)


def run(
    args: list[str],
    stdin: str | None = None,
    timeout: int = 60,
    retries: int = 0,
) -> Any:
    """Run an `alpaca` subcommand and return parsed JSON.

    `retries` is opt-in and deliberately defaults to zero. Repeating a read
    costs nothing; repeating an order submission after an ambiguous failure can
    place the same trade twice, so writes never retry here.
    """
    binary = cli_path()
    if binary is None:
        raise AlpacaCliError("alpaca CLI not installed (brew install alpacahq/tap/cli)")

    last: AlpacaCliError | None = None
    for attempt in range(retries + 1):
        try:
            return _run_once(binary, args, stdin, timeout)
        except AlpacaCliError as exc:
            last = exc
            if attempt < retries and _is_transient(exc.payload, str(exc)):
                delay = 1.5 * (attempt + 1)
                log.warning(
                    "transient failure on `alpaca %s`, retrying in %.1fs",
                    " ".join(args), delay,
                )
                time.sleep(delay)
                continue
            raise
    raise last if last else AlpacaCliError("unreachable")


def _run_once(binary: str, args: list[str], stdin: str | None, timeout: int) -> Any:
    try:
        proc = subprocess.run(
            [binary, *args],
            input=stdin,
            capture_output=True,
            text=True,
            env=_env(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AlpacaCliError(
            f"alpaca {' '.join(args)} timed out after {timeout}s",
            {"error": "timeout"},
        ) from exc

    if proc.returncode != 0:
        detail: dict[str, Any] = {}
        try:
            detail = json.loads(proc.stderr.strip() or "{}")
        except json.JSONDecodeError:
            detail = {"stderr": proc.stderr.strip()}
        raise AlpacaCliError(
            f"alpaca {' '.join(args)} exited {proc.returncode}: {detail}", detail
        )

    out = proc.stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out


def command_string(args: list[str], stdin: str | None = None) -> str:
    """Human-readable reproduction of a CLI call, for the journal and the demo."""
    rendered = "alpaca " + " ".join(args)
    if stdin:
        return f"echo '{stdin}' | {rendered}"
    return rendered


# --- Reads -----------------------------------------------------------------

def account() -> dict[str, Any]:
    return run(["account", "get"], retries=2)


def clock() -> dict[str, Any]:
    return run(["clock"], retries=3)


def positions() -> list[dict[str, Any]]:
    return run(["position", "list"], retries=2) or []


def orders(status: str = "open") -> list[dict[str, Any]]:
    return run(["order", "list", "--status", status], retries=2) or []


def order_by_client_id(client_order_id: str) -> dict[str, Any] | None:
    try:
        return run(
            ["api", "GET", f"/v2/orders:by_client_order_id?client_order_id={client_order_id}"],
            retries=2,
        )
    except AlpacaCliError:
        return None


# --- Writes ----------------------------------------------------------------

def submit_order(payload: dict[str, Any]) -> dict[str, Any]:
    """POST /v2/orders. Handles single-leg and multi-leg (order_class=mleg)."""
    # No retry: a resubmission after an ambiguous failure can place the same
    # trade twice. The fill walker reconciles against the broker instead.
    body = json.dumps(payload, separators=(",", ":"))
    return run(["api", "POST", "/v2/orders"], stdin=body)


def cancel_order(order_id: str) -> Any:
    """Cancel one working order.

    Same flag-not-positional contract as close_position below. Passed
    positionally the CLI exits 1 with "--order-id required", which the fill
    walker treated as an ordinary cancel failure and gave up on, so every
    resting order stopped being repriced and its structure stayed pending for
    the rest of the session holding risk budget it was not using.
    """
    return run(["order", "cancel", "--order-id", order_id])


def close_position(symbol: str, qty: str | None = None) -> Any:
    """Liquidate one leg at market.

    The CLI takes the symbol as a flag rather than a positional argument, and
    passing it positionally fails with an error rather than doing nothing
    visible, which is exactly the kind of silent no-op that matters most in the
    one code path whose job is to get us out of a position.
    """
    args = ["position", "close", "--symbol-or-asset-id", symbol]
    if qty:
        args += ["--qty", qty]
    return run(args)


def doctor() -> Any:
    return run(["doctor"])
