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

    core_risk_share: float = 0.55
    convex_risk_share: float = 0.15
    #: The long-horizon sleeve. Everything else this agent trades is a one to
    #: seven day position, which means the book has no exposure at all to the
    #: one edge in equities that is actually well documented: the equity risk
    #: premium. You are not paid it by being flat overnight. This sleeve is
    #: how the agent holds a directional position for weeks instead of hours.
    carry_risk_share: float = 0.30

    max_risk_per_trade_pct: float = 0.0075   # <= 0.75% of equity on one structure
    #: Carry gets its own, larger per-trade cap. At 0.75% a carry position
    #: cannot be expressed at all below a six-figure account: the narrowest
    #: sensible risk reversal on SPY risks about 630 dollars a contract, so a
    #: 0.75% cap on a 50,000 book sizes it to zero and the sleeve silently
    #: never trades. That is a unit problem, not a risk decision: a structure
    #: held for five weeks is one position rather than a scalp, and the
    #: aggregate is still bounded by max_carry_open_risk_pct.
    max_carry_risk_per_trade_pct: float = 0.0125
    max_open_risk_pct: float = 0.06          # <= 6% of equity at risk at once
    # The convex sleeve gets a real allocation rather than a token one. With
    # implied vol below realized on the whole universe, premium selling is being
    # paid too little for the movement the tape is actually delivering, so the
    # long-gamma side is where the edge is this week.
    max_convex_open_risk_pct: float = 0.03
    #: Carry gets the largest sleeve cap because it is the slowest. A 45 day
    #: structure occupies its budget for weeks, so a cap the size of the
    #: tactical sleeves' would let two positions consume the sleeve for the
    #: whole month.
    max_carry_open_risk_pct: float = 0.035
    max_risk_per_underlying_pct: float = 0.025

    daily_loss_kill_pct: float = 0.03        # flatten and stand down for the day
    total_drawdown_kill_pct: float = 0.08    # stand down for the rest of the event

    max_new_trades_per_day: int = 8
    max_open_structures: int = 10

    #: Entries that never filled do not spend a trade slot, because they never
    #: became a position. They are bounded here instead, so a session priced
    #: where nothing fills gives up rather than churning orders all day.
    max_failed_entries_per_day: int = 12

    min_credit_to_width: float = 0.18        # never sell a spread for pennies
    max_debit_to_width: float = 0.45         # never overpay for convexity

    #: The barbell, made structural. Buying convexity when implied vol already
    #: sits well above realized is paying a premium for the thing you are
    #: trying to buy cheaply; selling premium when implied sits below realized
    #: is underwriting risk for less than it costs. Inside this band either
    #: sleeve is allowed, because the signal is not clean enough to insist.
    max_premium_to_buy_convexity: float = 0.02
    min_premium_to_sell_convexity: float = -0.02

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
    # Not zero. Our own research turned up the one category with hard evidence
    # against it: across a large sample of retail option trades, 0DTE trades
    # underperformed others by 4.7 percentage points, and 0DTE DEBIT trades --
    # exactly what the convex sleeve buys -- lost an average of $8.05 per
    # contract, while 0DTE credit trades made $4.55. Buying same-day premium is
    # the worst-documented trade available to us, so the sleeve starts at one
    # day out and keeps the convexity without the lottery ticket.
    convex_min_dte: int = 1
    convex_max_dte: int = 3
    convex_long_delta: float = 0.35
    convex_widths: tuple[float, ...] = (3.0, 5.0, 8.0)
    convex_profit_target: float = 1.20       # take profit at 120% of debit paid

    # Carry sleeve: long-horizon bullish risk reversals. Sell a put spread to
    # finance a wider call spread, same expiry, five to nine weeks out.
    #
    # This is the only structure the agent trades that is an investment rather
    # than a trade. It is long delta, so it is paid for holding equity risk; it
    # is defined risk on both sides, so a crash costs the put spread's width
    # and not the account; and its upside is a multiple of its risk rather than
    # the fraction of width that a credit spread is capped at.
    #
    # The skew does the financing. Index puts trade at a higher implied
    # volatility than equidistant calls, so selling the put spread and buying
    # the call spread means selling the expensive side of the surface to fund
    # the cheap side. That is true regardless of the overall level of implied
    # volatility, which is why this sleeve is not routed by the vol premium.
    carry_min_dte: int = 25
    carry_max_dte: int = 65
    #: A 30 delta short put, roughly a 70% chance of expiring worthless. At 22
    #: delta the put spread barely finances anything: measured on a live SPY
    #: chain, moving the short put from 22 to 30 delta cut the net cost of the
    #: package by nine percent of its own risk and improved the payoff ratio
    #: from 2.4x to 2.6x, because the extra premium comes off the debit while
    #: the maximum loss is set by the width.
    carry_put_delta: float = 0.30
    carry_call_delta: float = 0.32       # the long call we are buying
    #: Narrow, and that is the finding rather than an oversight. The maximum
    #: loss is the put width less the financing, so a wider put spread adds
    #: risk far faster than it adds credit: on the same chain, going from a
    #: three wide to a five wide put spread raised the risk from 621 to 783
    #: dollars a contract and dropped the payoff from 2.7x to 2.2x. A wide
    #: short put spread is a worse trade wearing the same name.
    carry_put_widths: tuple[float, ...] = (3.0, 5.0)
    carry_call_widths: tuple[float, ...] = (10.0, 15.0, 20.0)
    #: Never pay more than this fraction of the call width in net debit. Above
    #: it the structure stops being financed and turns into an outright long
    #: call spread wearing a short put spread as a hat.
    carry_max_net_debit_to_call_width: float = 0.30
    #: Take profit at a full multiple of risk rather than a fraction of it. The
    #: point of holding for weeks is to be paid more than a credit spread pays
    #: in a day; exiting at 20% would forfeit exactly the thing being bought.
    carry_profit_target: float = 0.60    # 60% of max gain
    #: Cut at 1.2x the risk budgeted. Wider than the tactical sleeves on
    #: purpose: a five week position that is stopped out on its first bad week
    #: was never really a five week position.
    carry_stop_fraction: float = 1.20
    #: Close with this many days left regardless. Gamma rises sharply into the
    #: last fortnight and the thesis was about weeks, not the final days.
    carry_min_hold_dte: int = 10

    # Liquidity gates applied to every leg before it can be traded.
    max_bid_ask_pct: float = 0.12
    min_open_interest: int = 50
    min_leg_price: float = 0.05


BASE_RISK = RiskLimits()
BASE_STRATEGY = StrategyParams()

#: Named presets. `barbell` is the competition configuration; the others exist
#: so a second paper account can run a genuine alternative over the same tape.
#:
#: A control arm has to say what it excludes, not assume it. When the carry
#: sleeve was added to BASE_RISK every preset that had not heard of it
#: inherited a 3.5% carry budget, so the "income only" arm quietly held
#: multi-week risk reversals and every variant reported the identical carry
#: P&L to the dollar. The comparison the three accounts exist to make was gone
#: and nothing failed. Each arm below now zeroes the sleeves it is a control
#: for.
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
            carry_risk_share=0.0,
            max_carry_open_risk_pct=0.0,
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
                carry_risk_share=0.0, max_carry_open_risk_pct=0.0,
                max_convex_open_risk_pct=0.0, min_credit_to_width=0.22),
        replace(BASE_STRATEGY, core_short_delta=0.30, core_max_dte=5),
    ),

    # --- Diary variants -----------------------------------------------------
    # Below here nothing is wired to a broker account. They exist so the
    # backtest can rank ideas we are not willing to spend a live account on,
    # and so the diary book can show what they would have done on the same
    # tape the live accounts traded. Promoting one to a live account is a
    # config change and nothing else.

    # Deploy twice the risk of the judged preset. The barbell's problem is not
    # its win rate, it is its ceiling: credit selling caps the upside at the
    # credit taken, so at 6% deployed the best possible week is about 2% of
    # equity. This asks the only honest question about that ceiling, which is
    # whether the drawdown scales worse than the return does.
    "levered": (
        replace(
            BASE_RISK,
            max_risk_per_trade_pct=0.015,
            max_carry_risk_per_trade_pct=0.025,
            max_open_risk_pct=0.12,
            max_convex_open_risk_pct=0.06,
            max_carry_open_risk_pct=0.07,
            max_risk_per_underlying_pct=0.05,
            max_new_trades_per_day=12,
            max_open_structures=16,
        ),
        BASE_STRATEGY,
    ),

    # Let the variance risk premium pick the side for the whole book instead of
    # splitting it into two fixed sleeves. G7 already refuses the wrong side
    # per trade; this makes the routing the entire strategy, so the backtest
    # can say whether the sleeve split earns its complexity.
    "vrp_router": (
        replace(
            BASE_RISK,
            core_risk_share=0.5,
            convex_risk_share=0.5,
            carry_risk_share=0.0,
            max_carry_open_risk_pct=0.0,
            max_convex_open_risk_pct=0.06,
            max_premium_to_buy_convexity=0.0,
            min_premium_to_sell_convexity=0.0,
        ),
        replace(BASE_STRATEGY, core_short_delta=0.25, convex_long_delta=0.40),
    ),

    # Sell closer to the money for a much fatter credit, and accept the lower
    # win rate that comes with it. Tests whether the 18% credit floor is
    # leaving money on the table or protecting us from it.
    "fat_credit": (
        replace(
            BASE_RISK,
            core_risk_share=1.0,
            convex_risk_share=0.0,
            carry_risk_share=0.0,
            max_carry_open_risk_pct=0.0,
            max_convex_open_risk_pct=0.0,
            min_credit_to_width=0.30,
        ),
        replace(
            BASE_STRATEGY,
            core_short_delta=0.38,
            core_widths=(1.0, 2.0, 3.0),
            core_profit_target=0.50,
            core_stop_multiple=1.6,
        ),
    ),

    # Convexity with no income sleeve at all, and a wide enough allocation to
    # actually express it. The mirror image of income_only, and the only
    # preset that can produce a payoff larger than its own risk budget.
    # Carry-led: the long-horizon sleeve gets most of the budget. The question
    # this asks is the one the other six presets cannot: whether holding a
    # financed long position for weeks beats trading the same underlyings for
    # hours. Every other variant is a way of being flat overnight.
    "carry_led": (
        replace(
            BASE_RISK,
            core_risk_share=0.20,
            convex_risk_share=0.10,
            carry_risk_share=0.70,
            max_carry_open_risk_pct=0.045,
            max_carry_risk_per_trade_pct=0.015,
            max_convex_open_risk_pct=0.01,
            max_risk_per_trade_pct=0.010,
            max_risk_per_underlying_pct=0.035,
        ),
        replace(BASE_STRATEGY, carry_call_widths=(10.0, 15.0, 20.0, 25.0)),
    ),

    "long_gamma": (
        replace(
            BASE_RISK,
            core_risk_share=0.0,
            convex_risk_share=1.0,
            carry_risk_share=0.0,
            max_carry_open_risk_pct=0.0,
            max_convex_open_risk_pct=0.05,
            max_open_risk_pct=0.05,
            max_debit_to_width=0.38,
        ),
        replace(
            BASE_STRATEGY,
            convex_long_delta=0.42,
            convex_max_dte=5,
            convex_widths=(3.0, 5.0, 8.0, 10.0),
            convex_profit_target=1.50,
        ),
    ),
}

#: Variants wired to a real paper account. Everything else in VARIANTS is
#: diary-only: backtested and shadowed, never sent to a broker.
LIVE_VARIANTS: frozenset[str] = frozenset({"barbell", "convex_tilt", "income_only"})

#: Starting equity for the diary book. Deliberately not 100,000, so a diary
#: number can never be mistaken for a live account number at a glance.
DIARY_EQUITY: float = 50_000.0


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
    diary: bool
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
        if self.diary:
            # A diary variant has no broker account and never gets one from an
            # environment variable. Arming is refused here rather than in the
            # executor, so there is no path from a diary preset to an order.
            return True
        if self.force_dry_run:
            return True
        if self.live_from is None:
            # Arming was requested but no arming time survived parsing. A
            # malformed .env once left DRY_RUN holding a whole sentence, and
            # had LIVE_FROM been mangled the same way this would have gone live
            # immediately and silently. Failing towards simulation is the only
            # safe direction for a system that can place orders.
            return True
        return datetime.now(timezone.utc) < self.live_from

    @property
    def arming_misconfigured(self) -> bool:
        """True when live trading was asked for but no arming time was parsed."""
        return not self.force_dry_run and self.live_from is None

    def describe(self) -> str:
        if self.diary:
            return f"profile={self.profile} variant={self.variant} mode=DIARY (no broker)"
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


def _db_path(profile: str, variant: str, diary: bool) -> Path:
    """Where this book keeps its journal.

    SUPERIO_DB overrides it outright, which is how the test suites avoid
    reading the operator's real journal: a rehearsed structure left in it would
    otherwise charge risk against a test's imaginary book.
    """
    override = os.getenv("SUPERIO_DB", "").strip()
    if override:
        return Path(override)
    if diary:
        return ROOT / "data" / f"diary-{variant}.db"
    return ROOT / "data" / f"superio-{profile}.db"


def load_settings(profile: str | None = None, variant: str | None = None) -> Settings:
    profile = profile or os.getenv("ALPACA_PROFILE", "main")
    variant = variant or os.getenv("STRATEGY_VARIANT", "barbell")
    if variant not in VARIANTS:
        raise ValueError(f"unknown STRATEGY_VARIANT {variant!r}; known: {sorted(VARIANTS)}")
    risk, strategy = VARIANTS[variant]

    # Membership of LIVE_VARIANTS is what makes a preset tradeable, not an
    # environment variable. Anything else is a diary book: it reads the same
    # live chain and journals the same decisions, into its own database, and
    # cannot place an order.
    diary = variant not in LIVE_VARIANTS

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
        diary=diary,
        db_path=_db_path(profile, variant, diary),
        risk=risk,
        strategy=strategy,
    )


SETTINGS = load_settings()
