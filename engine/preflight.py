"""Startup checks.

Run before the first order of a session. Everything here has bitten a real
Alpaca options bot: credentials that authenticate but lack options approval,
a paper account whose balance was never set, a chain endpoint returning empty
because the feed is wrong.

A fatal check failing stops the loop. A warning is reported and the loop runs.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Any

from engine import alpaca_cli, billing_guard, state
from engine.config import SETTINGS

log = logging.getLogger(__name__)

COMPETITION_BALANCE = 100_000.0
REQUIRED_OPTIONS_LEVEL = 3


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fatal: bool = False

    def __str__(self) -> str:
        mark = "ok  " if self.ok else ("FAIL" if self.fatal else "warn")
        return f"[{mark}] {self.name}: {self.detail}"


def _revision() -> str:
    """Short git revision, so a stale deployment is visible at a glance."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=str(SETTINGS.db_path.parent.parent),
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return "unknown"


def run(require_competition_balance: bool = False) -> list[Check]:
    checks: list[Check] = []
    checks.append(Check("revision", True, f"running {_revision()}"))

    if not alpaca_cli.available():
        return [
            Check(
                "alpaca CLI",
                False,
                "not on PATH; install with `brew install alpacahq/tap/cli`",
                fatal=True,
            )
        ]
    checks.append(Check("alpaca CLI", True, str(alpaca_cli.cli_path())))

    if not SETTINGS.configured:
        return checks + [
            Check("credentials", False, f"no keys for profile {SETTINGS.profile}", fatal=True)
        ]

    try:
        account: dict[str, Any] = alpaca_cli.account()
    except alpaca_cli.AlpacaCliError as exc:
        return checks + [Check("account", False, str(exc), fatal=True)]

    status = str(account.get("status"))
    checks.append(
        Check("account status", status == "ACTIVE", f"{account.get('account_number')} {status}",
              fatal=status != "ACTIVE")
    )

    level = int(account.get("options_trading_level") or 0)
    checks.append(
        Check(
            "options level",
            level >= REQUIRED_OPTIONS_LEVEL,
            f"level {level} (spreads need {REQUIRED_OPTIONS_LEVEL})",
            fatal=level < REQUIRED_OPTIONS_LEVEL,
        )
    )

    if not SETTINGS.paper:
        checks.append(
            Check("paper mode", False, "ALPACA_PAPER is false; this agent is paper-only",
                  fatal=True)
        )
    else:
        checks.append(Check("paper mode", True, "paper trading"))

    equity = float(account.get("equity") or 0)
    balance_ok = abs(equity - COMPETITION_BALANCE) < 1.0
    checks.append(
        Check(
            "starting balance",
            balance_ok or not require_competition_balance,
            f"equity {equity:,.2f} (the competition account must start at "
            f"{COMPETITION_BALANCE:,.0f})",
            fatal=require_competition_balance and not balance_ok,
        )
    )

    try:
        from engine.marketdata import get_chain, underlying_price

        symbol = SETTINGS.strategy.universe[0]
        spot = underlying_price(symbol)
        chain = get_chain(symbol, 1, 7, spot=spot)
        with_greeks = sum(1 for c in chain if c.delta is not None)
        checks.append(
            Check(
                "market data",
                bool(chain),
                f"{symbol} at {spot:.2f}, {len(chain)} contracts, {with_greeks} with greeks "
                f"({SETTINGS.options_feed} feed)",
                fatal=not chain,
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("market data", False, str(exc), fatal=True))

    try:
        state.log_event("preflight", "journal write check")
        checks.append(Check("journal", True, str(SETTINGS.db_path)))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("journal", False, str(exc), fatal=True))

    if SETTINGS.vertex_project:
        checks.append(
            Check(
                "reasoning",
                True,
                f"Vertex project {SETTINGS.vertex_project} ({SETTINGS.vertex_location}), "
                f"application-default credentials, no API key stored",
            )
        )
        status = billing_guard.check()
        checks.append(
            Check("billing account", status.ok, status.detail, fatal=not status.ok)
        )
    else:
        checks.append(
            Check("reasoning", True, "no model configured; running fully deterministic")
        )

    checks.append(
        Check(
            "execution mode",
            True,
            "DRY RUN, no orders will be sent" if SETTINGS.dry_run else "LIVE on paper money",
        )
    )
    return checks


def passed(checks: list[Check]) -> bool:
    return not any(c.fatal and not c.ok for c in checks)


def report(checks: list[Check]) -> str:
    return "\n".join(str(c) for c in checks)
