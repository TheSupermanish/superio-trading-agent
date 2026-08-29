"""Configuration, profiles, and risk parameters.

Two things are configurable per process:

* `ALPACA_PROFILE` selects which paper account to trade. Each profile has its
  own credentials and its own SQLite journal, so competing strategy variants
  can run side by side without contaminating each other's P&L.
* `STRATEGY_VARIANT` selects a named preset of risk limits and strategy
  parameters. That is the only thing that differs between the accounts.

Every gate the risk officer enforces is a number in this file, so the one-page
write-up can point at a single source of truth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from datetime import datetime, timezone

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
# The Gemini CLI keeps its Vertex project here. Reading it means the agent uses
# the same credentials the user already has working, with nothing to copy.
load_dotenv(Path.home() / ".gemini" / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RiskLimits:
    """Hard limits. The LLM proposes trades; these numbers decide."""

    core_risk_share: float = 0.85
    convex_risk_share: float = 0.15

    max_risk_per_trade_pct: float = 0.0075   # <= 0.75% of equity on one structure
    max_open_risk_pct: float = 0.06          # <= 6% of equity at risk at once
    # The convex sleeve gets a real allocation rather than a token one. With
    # implied vol below realized on the whole universe, premium selling is being
    # paid too little for the movement the tape is actually delivering, so the
    # long-gamma side is where the edge is this week.
    max_convex_open_risk_pct: float = 0.03
    max_risk_per_underlying_pct: float = 0.025

    daily_loss_kill_pct: float = 0.03        # flatten and stand down for the day
    total_drawdown_kill_pct: float = 0.08    # stand down for the rest of the event

    max_new_trades_per_day: int = 8
    max_open_structures: int = 10

    min_credit_to_width: float = 0.18        # never sell a spread for pennies
    max_debit_to_width: float = 0.45         # never overpay for convexity

    allow_naked_short: bool = False


@dataclass(frozen=True)
class StrategyParams:
    universe: tuple[str, ...] = ("SPY", "QQQ", "IWM")

    # Core income sleeve: short vertical spreads, delta-selected.
    core_min_dte: int = 1
    core_max_dte: int = 7
    # A 16-delta short yields only about 12% of width at any plausible
    # volatility premium, so pairing it with an 18% credit floor meant the
    # income sleeve could never fire: the two settings contradicted each other.
    # 22 delta clears the floor while staying well out of the money.
    core_short_delta: float = 0.22
    core_delta_tolerance: float = 0.07
    core_widths: tuple[float, ...] = (1.0, 2.0, 3.0, 5.0)
    core_profit_target: float = 0.55         # buy back at 55% of credit captured
    core_stop_multiple: float = 2.0          # cut at 2x the credit received

    # Convex sleeve: short-dated debit structures bought for the right tail.
    convex_min_dte: int = 0
    convex_max_dte: int = 3
    convex_long_delta: float = 0.35
    convex_widths: tuple[float, ...] = (3.0, 5.0, 8.0)
    convex_profit_target: float = 1.20       # take profit at 120% of debit paid

    # Liquidity gates applied to every leg before it can be traded.
    max_bid_ask_pct: float = 0.12
    min_open_interest: int = 50
    min_leg_price: float = 0.05


BASE_RISK = RiskLimits()
BASE_STRATEGY = StrategyParams()

#: Named presets. `barbell` is the competition configuration; the others exist
#: so a second paper account can run a genuine alternative over the same tape.
VARIANTS: dict[str, tuple[RiskLimits, StrategyParams]] = {
    # Most of the risk budget sells defined-risk premium, a capped sleeve buys
    # convexity. This is the configuration submitted for judging.
    "barbell": (BASE_RISK, BASE_STRATEGY),

    # Convexity-led: runs when implied vol sits below realized, which is the
    # regime the scout reported at kick-off.
    "convex_tilt": (
        replace(
            BASE_RISK,
            core_risk_share=0.55,
            convex_risk_share=0.45,
            max_convex_open_risk_pct=0.025,
            max_risk_per_trade_pct=0.006,
            max_debit_to_width=0.40,
        ),
        replace(
            BASE_STRATEGY,
            convex_long_delta=0.40,
            convex_max_dte=5,
            core_short_delta=0.18,
        ),
    ),

    # Pure income control arm: no convex sleeve at all.
    "income_only": (
        replace(BASE_RISK, core_risk_share=1.0, convex_risk_share=0.0,
                max_convex_open_risk_pct=0.0, min_credit_to_width=0.22),
        replace(BASE_STRATEGY, core_short_delta=0.30, core_max_dte=5),
    ),
}


@dataclass(frozen=True)
class Settings:
    profile: str
    variant: str
    api_key: str
    secret_key: str
    paper: bool
    account_id: str
    options_feed: str
    stock_feed: str
    anthropic_api_key: str
    featherless_api_key: str
    vertex_project: str
    vertex_location: str
    expected_billing_account: str
    force_dry_run: bool
    live_from: datetime | None
    db_path: Path
    risk: RiskLimits
    strategy: StrategyParams

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.secret_key)

    @property
    def dry_run(self) -> bool:
        """Orders are simulated unless we are past the arming time.

        `DRY_RUN=true` pins the agent to simulation permanently. Otherwise the
        agent arms itself at `LIVE_FROM`, which lets a supervisor be started
        days early and begin trading on its own at the intended moment instead
        of depending on someone being at a keyboard.
        """
        if self.force_dry_run:
            return True
        if self.live_from is None:
            return False
        return datetime.now(timezone.utc) < self.live_from

    def describe(self) -> str:
        if self.dry_run:
            mode = "DRY RUN"
            if not self.force_dry_run and self.live_from:
                mode += f" until {self.live_from.astimezone().strftime('%a %d %b %H:%M %Z')}"
        else:
            mode = "LIVE PAPER"
        return f"profile={self.profile} variant={self.variant} mode={mode}"


def _parse_live_from(raw: str | None) -> datetime | None:
    """Parse the arming time. An unparseable value keeps the agent simulated."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _profile_value(profile: str, suffix: str, fallback: str = "") -> str:
    key = f"ALPACA_{profile.upper().replace('-', '_')}_{suffix}"
    return os.getenv(key) or os.getenv(f"ALPACA_{suffix}") or fallback


def load_settings(profile: str | None = None, variant: str | None = None) -> Settings:
    profile = profile or os.getenv("ALPACA_PROFILE", "main")
    variant = variant or os.getenv("STRATEGY_VARIANT", "barbell")
    if variant not in VARIANTS:
        raise ValueError(f"unknown STRATEGY_VARIANT {variant!r}; known: {sorted(VARIANTS)}")
    risk, strategy = VARIANTS[variant]

    return Settings(
        profile=profile,
        variant=variant,
        api_key=_profile_value(profile, "API_KEY"),
        secret_key=_profile_value(profile, "SECRET_KEY"),
        paper=_env_bool("ALPACA_PAPER", True),
        account_id=_profile_value(profile, "ACCOUNT_ID"),
        options_feed=os.getenv("ALPACA_OPTIONS_FEED", "indicative"),
        stock_feed=os.getenv("ALPACA_STOCK_FEED", "iex"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        featherless_api_key=os.getenv("FEATHERLESS_API_KEY", ""),
        vertex_project=os.getenv("GOOGLE_CLOUD_PROJECT", ""),
        vertex_location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
        expected_billing_account=os.getenv("EXPECTED_BILLING_ACCOUNT", ""),
        force_dry_run=_env_bool("DRY_RUN", True),
        live_from=_parse_live_from(os.getenv("LIVE_FROM")),
        db_path=ROOT / "data" / f"superio-{profile}.db",
        risk=risk,
        strategy=strategy,
    )


SETTINGS = load_settings()
