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
import shutil
import subprocess
from typing import Any

from engine.config import SETTINGS


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


def run(args: list[str], stdin: str | None = None, timeout: int = 60) -> Any:
    """Run an `alpaca` subcommand and return parsed JSON."""
    binary = cli_path()
    if binary is None:
        raise AlpacaCliError("alpaca CLI not installed (brew install alpacahq/tap/cli)")

    proc = subprocess.run(
        [binary, *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=_env(),
        timeout=timeout,
    )
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
    return run(["account", "get"])


def clock() -> dict[str, Any]:
    return run(["clock"])


def positions() -> list[dict[str, Any]]:
    return run(["position", "list"]) or []


def orders(status: str = "open") -> list[dict[str, Any]]:
    return run(["order", "list", "--status", status]) or []


def order_by_client_id(client_order_id: str) -> dict[str, Any] | None:
    try:
        return run(["api", "GET", f"/v2/orders:by_client_order_id?client_order_id={client_order_id}"])
    except AlpacaCliError:
        return None


# --- Writes ----------------------------------------------------------------

def submit_order(payload: dict[str, Any]) -> dict[str, Any]:
    """POST /v2/orders. Handles single-leg and multi-leg (order_class=mleg)."""
    body = json.dumps(payload, separators=(",", ":"))
    return run(["api", "POST", "/v2/orders"], stdin=body)


def cancel_order(order_id: str) -> Any:
    return run(["order", "cancel", order_id])


def close_position(symbol: str, qty: str | None = None) -> Any:
    args = ["position", "close", symbol]
    if qty:
        args += ["--qty", qty]
    return run(args)


def doctor() -> Any:
    return run(["doctor"])
