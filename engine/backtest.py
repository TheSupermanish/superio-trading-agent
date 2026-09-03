"""Replay harness.

There is no honest way to backtest this strategy on real option prices from a
free Alpaca plan: historical option bars return "OPRA agreement is not signed",
and the VIX index needs a paid subscription. So rather than invent one implied
volatility number and present the result as fact, this replays real underlying
price history with Black-Scholes-priced synthetic chains and SWEEPS the
variance risk premium.

That reframes the question usefully. Instead of "what would we have made", which
we cannot answer, it asks "at what volatility premium does each sleeve stop
working" -- and the answer is checkable against the regime we are actually in,
where implied sits below realized.

What is real: the price path, the trend, realized volatility, the strategy
logic, the risk gates, the sizing, and the exit rules. All of it is the same
code the live agent runs.

What is modelled: the implied volatility level (realized vol times the swept
premium), a flat surface with no skew or term structure, and execution cost as
a fixed per-leg amount.

Read the output as a regime sensitivity study, not a P&L forecast or a fair
cross-sleeve ranking. A synthetic surface can flatter long and short volatility
in different ways, so modelling error does not magically cancel between them.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import math
from datetime import date, datetime, timedelta
from statistics import NormalDist, mean
from typing import Any

import numpy as np

from engine.config import SETTINGS
from engine.greeks import RISK_FREE_RATE, bs_price, year_fraction
from engine.marketdata import daily_bars

#: Cost per leg per side. We measured a real round trip on paper: patient limit
#: orders cost effectively nothing, market exits cost about six percent of the
#: spread's value. Two cents a leg sits between them and is deliberately
#: pessimistic for penny-wide names.
LEG_COST = 0.02

#: A flat Black-Scholes surface prices out-of-the-money puts far too cheaply,
#: so a delta-selected short put never clears the credit-to-width floor and the
#: income sleeve records zero trades -- an artefact of the model, not a finding
#: about the strategy. Real index options carry a pronounced downside skew.
#:
#: This adds a linear skew in log-moneyness, calibrated to a typical index
#: shape: about four volatility points of extra implied vol five percent below
#: spot, tapering above it. Crude next to a real surface, and stated as such,
#: but it lets the two sleeves be compared on remotely fair terms.
SKEW_SLOPE = 0.80
CALL_SKEW_DAMPING = 0.35


def skewed_iv(atm_iv: float, spot: float, strike: float) -> float:
    """Implied vol for one strike under a simple linear skew in log-moneyness.

    Below spot the curve rises (downside puts are bid up); above spot it falls
    away more gently, which is the usual index shape.
    """
    if spot <= 0 or strike <= 0:
        return atm_iv
    moneyness = math.log(strike / spot)
    if moneyness < 0:
        return max(atm_iv + SKEW_SLOPE * (-moneyness), 0.02)
    return max(atm_iv - SKEW_SLOPE * CALL_SKEW_DAMPING * moneyness, 0.02)


@dataclass
class Trade:
    opened: date
    closed: date | None
    symbol: str
    sleeve: str
    kind: str
    qty: int
    net_price: float
    max_loss_per_unit: float
    pnl: float = 0.0
    reason: str = ""


@dataclass
class Result:
    vrp: float
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[date, float]] = field(default_factory=list)

    @property
    def pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def wins(self) -> list[Trade]:
        return [t for t in self.trades if t.pnl > 0]

    @property
    def losses(self) -> list[Trade]:
        return [t for t in self.trades if t.pnl < 0]

    @property
    def win_rate(self) -> float | None:
        return len(self.wins) / len(self.trades) if self.trades else None

    @property
    def profit_factor(self) -> float | None:
        gross_loss = abs(sum(t.pnl for t in self.losses))
        return (sum(t.pnl for t in self.wins) / gross_loss) if gross_loss else None

    @property
    def max_drawdown(self) -> float:
        peak, worst = 0.0, 0.0
        for _d, value in self.equity_curve:
            peak = max(peak, value)
            if peak:
                worst = max(worst, (peak - value) / peak)
        return worst


def realized_vol(closes: list[float], window: int = 20) -> float | None:
    if len(closes) < window + 1:
        return None
    rets = np.diff(np.log(closes[-(window + 1):]))
    return float(rets.std(ddof=1) * (252 ** 0.5))


def strike_for_delta(
    spot: float, t: float, iv: float, target_delta: float, is_call: bool, step: float = 1.0
) -> float:
    """Nearest listed strike whose Black-Scholes delta matches the target.

    Delta can be inverted analytically. The old brute-force scan called scipy
    for hundreds of strikes per entry and made a two-year variant comparison
    take minutes without producing output.
    """
    if spot <= 0 or t <= 0 or iv <= 0 or not 0 < target_delta < 1:
        return round(spot / step) * step
    probability = target_delta if is_call else 1.0 - target_delta
    d1 = NormalDist().inv_cdf(probability)
    strike = spot * math.exp(
        (RISK_FREE_RATE + 0.5 * iv * iv) * t - d1 * iv * math.sqrt(t)
    )
    return round(strike / step) * step


def _vertical_value(
    spot: float, short_k: float, long_k: float, t: float, iv: float, is_call: bool, credit: bool
) -> float:
    """Net value of the spread, positive meaning it costs that much to close."""
    short_leg = bs_price(spot, short_k, t, skewed_iv(iv, spot, short_k), is_call)
    long_leg = bs_price(spot, long_k, t, skewed_iv(iv, spot, long_k), is_call)
    return (short_leg - long_leg) if credit else (long_leg - short_leg)


def run(
    symbol: str,
    vrp: float,
    years: int = 5,
    equity: float = 100_000.0,
    bars: list[dict[str, Any]] | None = None,
    config: tuple[Any, Any] | None = None,
) -> Result:
    """Replay one symbol at one volatility-premium assumption.

    `config` is an (risk, strategy) pair, so a variant can be replayed without
    mutating global settings.
    """
    r, p = config if config else (SETTINGS.risk, SETTINGS.strategy)
    bars = bars if bars is not None else daily_bars(symbol, days=252 * years)
    closes = [b["close"] for b in bars]
    dates = [datetime.fromisoformat(b["ts"]).date() for b in bars]

    result = Result(vrp=vrp)
    cash = equity
    i = 60
    while i < len(bars) - 1:
        window = closes[: i + 1]
        rv = realized_vol(window)
        if rv is None or rv <= 0:
            i += 1
            continue

        iv = rv * vrp
        spot = closes[i]
        sma20 = mean(window[-20:])
        sma50 = mean(window[-50:]) if len(window) >= 50 else sma20
        uptrend = spot > sma20 > sma50
        downtrend = spot < sma20 < sma50

        # Same routing rule as the live agent: the volatility premium picks the
        # sleeve, the trend picks the direction.
        selling = vrp > 1.0

        # Honour the variant's convex budget. A preset with no convex sleeve
        # cannot buy premium at all: live, every debit structure sizes to zero
        # and the agent stands aside. Skipping that here made the income-only
        # control look identical to the barbell in cheap-vol regimes, which is
        # the exact comparison the three accounts exist to make.
        if not selling and r.max_convex_open_risk_pct <= 0:
            i += 1
            continue
        is_call = downtrend if selling else not downtrend
        dte = p.core_max_dte if selling else max(p.convex_max_dte, 2)
        t = year_fraction(dte)

        target = p.core_short_delta if selling else p.convex_long_delta
        anchor = strike_for_delta(spot, t, iv, target, is_call)

        best: tuple[float, float, float, float, float] | None = None
        for width in (p.core_widths if selling else p.convex_widths):
            if selling:
                # Sell the delta-selected strike, buy the wing further out.
                short_k = anchor
                long_k = anchor + width if is_call else anchor - width
                net = (
                    bs_price(spot, short_k, t, skewed_iv(iv, spot, short_k), is_call)
                    - bs_price(spot, long_k, t, skewed_iv(iv, spot, long_k), is_call)
                    - 2 * LEG_COST
                )
                if net <= 0:
                    continue
                ratio = net / width
                if ratio < r.min_credit_to_width:
                    continue
                # Best credit spread is the one paying most per unit of width.
                if best is None or ratio > best[0]:
                    best = (ratio, width, short_k, long_k, net)
            else:
                # Buy the delta-selected strike, sell further out to cheapen it.
                long_k = anchor
                short_k = anchor + width if is_call else anchor - width
                debit = (
                    bs_price(spot, long_k, t, skewed_iv(iv, spot, long_k), is_call)
                    - bs_price(spot, short_k, t, skewed_iv(iv, spot, short_k), is_call)
                    + 2 * LEG_COST
                )
                if debit <= 0:
                    continue
                ratio = debit / width
                if ratio > r.max_debit_to_width:
                    continue
                payoff = (width - debit) / debit
                # Best convex spread is the one with the largest payoff ratio.
                if best is None or payoff > best[0]:
                    best = (payoff, width, short_k, long_k, -debit)

        if best is None:
            i += 1
            continue

        _ratio, width, short_k, long_k, net = best
        max_loss = ((width - net) if net > 0 else abs(net)) * 100
        if max_loss <= 0:
            i += 1
            continue

        qty = int((cash * r.max_risk_per_trade_pct) // max_loss)
        if qty < 1:
            i += 1
            continue

        trade = Trade(
            opened=dates[i], closed=None, symbol=symbol,
            sleeve="core" if selling else "convex",
            kind=("credit" if selling else "debit") + ("_call" if is_call else "_put"),
            qty=qty, net_price=net, max_loss_per_unit=max_loss,
        )

        # Hold forward, applying the same exits the live manager applies.
        entry_abs = abs(net)
        for j in range(i + 1, min(i + dte + 1, len(bars))):
            days_left = dte - (j - i)
            tj = year_fraction(max(days_left, 0))
            rvj = realized_vol(closes[: j + 1]) or rv
            ivj = rvj * vrp
            value = _vertical_value(closes[j], short_k, long_k, tj, ivj, is_call, net > 0)
            value = max(value, 0.0)

            if net > 0:  # credit
                captured = (net - value) / net if net else 0
                if captured >= p.core_profit_target:
                    trade.pnl = (net - value - 2 * LEG_COST) * 100 * qty
                    trade.reason = "profit target"
                elif value >= net * p.core_stop_multiple:
                    trade.pnl = (net - value - 2 * LEG_COST) * 100 * qty
                    trade.reason = "stop"
            else:  # debit
                gain = (value - entry_abs) / entry_abs if entry_abs else 0
                if gain >= p.convex_profit_target:
                    trade.pnl = (value - entry_abs - 2 * LEG_COST) * 100 * qty
                    trade.reason = "profit target"
                elif value <= entry_abs * (1 - p.convex_stop_loss_fraction):
                    trade.pnl = (value - entry_abs - 2 * LEG_COST) * 100 * qty
                    trade.reason = "stop"

            if trade.reason or days_left <= 0:
                if not trade.reason:  # held to expiry
                    intrinsic = _vertical_value(closes[j], short_k, long_k, 1e-6, ivj, is_call, net > 0)
                    intrinsic = max(intrinsic, 0.0)
                    trade.pnl = (
                        (net - intrinsic) if net > 0 else (intrinsic - entry_abs)
                    ) * 100 * qty
                    trade.reason = "expiry"
                trade.closed = dates[j]
                break

        if trade.closed is None:
            trade.closed = dates[min(i + dte, len(dates) - 1)]
            trade.reason = trade.reason or "expiry"

        cash += trade.pnl
        result.trades.append(trade)
        result.equity_curve.append((trade.closed, cash))
        i += max(dte, 1)

    return result


def sweep(symbols: tuple[str, ...], vrps: tuple[float, ...], years: int = 5) -> dict[float, Result]:
    """Run every symbol at every volatility-premium assumption."""
    cached = {s: daily_bars(s, days=252 * years) for s in symbols}
    out: dict[float, Result] = {}
    for vrp in vrps:
        combined = Result(vrp=vrp)
        for symbol in symbols:
            r = run(symbol, vrp, years=years, bars=cached[symbol])
            combined.trades.extend(r.trades)
            combined.equity_curve.extend(r.equity_curve)
        combined.equity_curve.sort(key=lambda x: x[0])
        running = 100_000.0
        rebuilt = []
        for t in sorted(combined.trades, key=lambda t: t.closed or t.opened):
            running += t.pnl
            rebuilt.append((t.closed or t.opened, running))
        combined.equity_curve = rebuilt
        out[vrp] = combined
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay the strategy across volatility regimes")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--symbols", default="SPY,QQQ,IWM")
    ap.add_argument("--variants", action="store_true",
                    help="compare the configured variants instead of sweeping premia")
    args = ap.parse_args()

    symbols = tuple(s.strip().upper() for s in args.symbols.split(","))

    if args.variants:
        results = compare_variants(symbols, years=args.years)
        print(f"\nVariant comparison over {args.years}y of {', '.join(symbols)}")
        print("Every variant replayed against the same tape, weighted across")
        print("volatility regimes. Treat the result as a hypothesis, not an edge.\n")
        print(f"{'variant':<14} {'trades':>7} {'win%':>6} {'weighted P&L':>14} {'PF':>6} {'maxDD':>7}")
        print("-" * 60)
        for name, m in results.items():
            pf = m["profit_factor"]
            wr = m["win_rate"] or 0
            print(
                f"{name:<14} {m['trades']:7d} {wr*100:5.1f}% {m['weighted_pnl']:14,.0f} "
                f"{(f'{pf:.2f}' if pf else '  inf'):>6} {m['max_drawdown']*100:6.1f}%"
            )
        print()
        for name, m in results.items():
            sleeves = ", ".join(
                f"{k} {v['n']} trades {v['pnl']:+,.0f}" for k, v in m["by_sleeve"].items()
            )
            print(f"  {name:<14} {sleeves}")
        print("\nAbsolute P&L is not trustworthy here: entry and exit share a pricing")
        print("model, so no volatility risk is simulated. Cross-sleeve rankings are")
        print("not proof either: that modelling error affects long and short volatility")
        print("differently. Use this to reject fragile ideas, then compare paper accounts.")
        return

    vrps = (0.80, 0.90, 0.95, 1.00, 1.05, 1.15, 1.30)
    results = sweep(symbols, vrps, years=args.years)

    print(f"\nReplay over {args.years}y of {', '.join(symbols)} price history")
    print("IV is modelled as realized vol x premium. Sleeve routing, gates,")
    print("sizing and exits are the live code.\n")
    print(f"{'IV/RV':>6} {'sleeve':>7} {'trades':>7} {'win%':>6} {'P&L':>11} {'PF':>6} {'maxDD':>7}")
    print("-" * 56)
    for vrp, res in results.items():
        if not res.trades:
            print(f"{vrp:6.2f} {'--':>7} {'0':>7}")
            continue
        sleeve = "credit" if vrp > 1.0 else "convex"
        wr = res.win_rate or 0
        pf = res.profit_factor
        print(
            f"{vrp:6.2f} {sleeve:>7} {len(res.trades):7d} {wr*100:5.1f}% "
            f"{res.pnl:11,.0f} {(f'{pf:.2f}' if pf else '  inf'):>6} {res.max_drawdown*100:6.1f}%"
        )
    print("\nModelled: linear skew, no term structure, 2c per leg cost.")
    print()
    print("READ THE P&L COLUMN WITH SUSPICION. Entry and exit are priced with the")
    print("same model and the same implied vol assumption, so no volatility risk")
    print("is simulated at all: the only thing driving P&L is where the underlying")
    print("went. A long-gamma book will always look good under that assumption over")
    print("a rising five years. Kill switches are not applied either, which is why")
    print("the convex drawdowns exceed the 8% limit the live agent enforces.")
    print()
    print("What this harness is actually good for: exercising the gates, sizing and")
    print("exit rules over a thousand trades, and catching configurations that")
    print("contradict each other. It found one -- see the note on short delta.")


# --- variant comparison ----------------------------------------------------

#: The volatility regimes to weight the comparison over, and how much of the
#: time each is worth. Implied usually sits a little above realized, which is
#: the variance risk premium; the discount case is rarer but is exactly where
#: this week started, so it carries real weight rather than being a footnote.
REGIME_WEIGHTS = (
    (0.85, 0.15),   # implied well below realized: options cheap
    (0.95, 0.20),   # mild discount
    (1.05, 0.30),   # mild premium, the usual state
    (1.20, 0.25),   # healthy premium
    (1.35, 0.10),   # rich, post-shock
)


#: How often a new carry position is opened, in trading days. The sleeve holds
#: for five to nine weeks, so opening one every fortnight keeps two or three
#: overlapping rather than replacing the position every session.
CARRY_CADENCE = 10


def _reversal_value(
    spot: float,
    short_put: float,
    long_put: float,
    long_call: float,
    short_call: float,
    t: float,
    iv: float,
) -> float:
    """Cost to close the four-leg package, signed as the live manager signs it.

    Sold legs cost money to buy back, bought legs return money when sold, so a
    package that has gained is cheaper (or pays more) to close.
    """
    def price(strike: float, is_call: bool) -> float:
        return bs_price(spot, strike, t, skewed_iv(iv, spot, strike), is_call)

    return (
        price(short_put, False)
        - price(long_put, False)
        - price(long_call, True)
        + price(short_call, True)
    )


def run_carry(
    symbol: str,
    vrp: float,
    years: int = 5,
    equity: float = 100_000.0,
    bars: list[dict[str, Any]] | None = None,
    config: tuple[Any, Any] | None = None,
) -> list[Trade]:
    """Replay the carry sleeve: financed bullish risk reversals held for weeks.

    Run as its own pass rather than folded into the tactical loop, because that
    loop advances by the life of each position. A forty-five day structure
    would consume the whole replay and the other sleeves would never trade.

    The volatility premium is swept here as everywhere else, but this sleeve is
    much less sensitive to it than the other two: it is short put volatility
    and long call volatility at once, so a uniform shift in implied vol largely
    cancels. What it is sensitive to is the skew, which is modelled, and the
    direction of the tape, which is real.
    """
    r, p = config if config else (SETTINGS.risk, SETTINGS.strategy)
    if r.max_carry_open_risk_pct <= 0:
        return []

    bars = bars if bars is not None else daily_bars(symbol, days=252 * years)
    closes = [b["close"] for b in bars]
    dates = [datetime.fromisoformat(b["ts"]).date() for b in bars]

    trades: list[Trade] = []
    cash = equity
    dte = (p.carry_min_dte + p.carry_max_dte) // 2

    i = 60
    while i < len(bars) - 1:
        window = closes[: i + 1]
        rv = realized_vol(window)
        if rv is None or rv <= 0:
            i += 1
            continue

        iv = rv * vrp
        spot = closes[i]
        sma20 = mean(window[-20:])
        sma50 = mean(window[-50:]) if len(window) >= 50 else sma20
        # Long delta only when the tape is not actively falling. This sleeve is
        # paid for holding equity risk; it is not paid for holding it into a
        # downtrend.
        if spot < sma20 < sma50:
            i += CARRY_CADENCE
            continue

        t = year_fraction(dte)
        short_put = strike_for_delta(spot, t, iv, p.carry_put_delta, False)
        long_call = strike_for_delta(spot, t, iv, p.carry_call_delta, True)

        best: tuple[float, float, float, float, float, float] | None = None
        for put_width in p.carry_put_widths:
            for call_width in p.carry_call_widths:
                long_put = short_put - put_width
                short_call = long_call + call_width
                # The cost to close a package at entry IS its net price in the
                # credit-positive convention: both are
                # short_put - long_put - long_call + short_call. Crossing four
                # spreads makes a credit smaller and a debit larger, which is
                # one subtraction either way.
                net = _reversal_value(
                    spot, short_put, long_put, long_call, short_call, t, iv
                ) - 4 * LEG_COST
                loss_points = put_width - net
                gain_points = call_width + net
                if loss_points <= 0 or gain_points <= 0:
                    continue
                if net < 0 and (-net) / call_width > p.carry_max_net_debit_to_call_width:
                    continue
                payoff = gain_points / loss_points
                if best is None or payoff > best[0]:
                    best = (payoff, net, short_put, long_put, long_call, short_call)

        if best is None:
            i += CARRY_CADENCE
            continue

        _payoff, net, short_put, long_put, long_call, short_call = best
        put_width = short_put - long_put
        call_width = short_call - long_call
        max_loss = (put_width - net) * 100
        max_gain = (call_width + net) * 100
        qty = int((cash * r.max_carry_risk_per_trade_pct) // max_loss)
        if qty < 1:
            i += CARRY_CADENCE
            continue

        trade = Trade(
            opened=dates[i], closed=None, symbol=symbol, sleeve="carry",
            kind="risk_reversal", qty=qty, net_price=net,
            max_loss_per_unit=max_loss,
        )

        for j in range(i + 1, min(i + dte + 1, len(bars))):
            days_left = dte - (j - i)
            tj = year_fraction(max(days_left, 1e-4))
            rvj = realized_vol(closes[: j + 1]) or rv
            current = _reversal_value(
                closes[j], short_put, long_put, long_call, short_call, tj, rvj * vrp
            )
            unrealized = (net - current) * 100 * qty
            if unrealized >= max_gain * qty * p.carry_profit_target:
                trade.pnl = unrealized - 4 * LEG_COST * 100 * qty
                trade.reason = "profit target"
            elif unrealized <= -max_loss * qty * p.carry_stop_fraction:
                trade.pnl = unrealized - 4 * LEG_COST * 100 * qty
                trade.reason = "stop"
            elif days_left <= p.carry_min_hold_dte:
                trade.pnl = unrealized - 4 * LEG_COST * 100 * qty
                trade.reason = "time exit"

            if trade.reason:
                trade.closed = dates[j]
                break

        if trade.closed is None:
            trade.closed = dates[min(i + dte, len(dates) - 1)]
            trade.reason = trade.reason or "time exit"

        cash += trade.pnl
        trades.append(trade)
        i += CARRY_CADENCE

    return trades


def compare_variants(
    symbols: tuple[str, ...] = ("SPY", "QQQ", "IWM"),
    years: int = 5,
    equity: float = 100_000.0,
) -> dict[str, dict[str, Any]]:
    """Replay every configured variant over identical price history.

    Absolute P&L from this harness is not trustworthy: entry and exit share a
    pricing model, so no volatility risk is simulated. Relative results can
    reject obviously fragile parameter sets within a sleeve, but do not fairly
    rank long-volatility against short-volatility strategies.

    This is the evidence behind running three accounts rather than one.
    """
    from engine.config import LIVE_VARIANTS, VARIANTS

    cached = {s: daily_bars(s, days=252 * years) for s in symbols}
    out: dict[str, dict[str, Any]] = {}

    for name, (risk_cfg, strategy_cfg) in VARIANTS.items():
        trades: list[Trade] = []
        weighted_pnl = 0.0
        for vrp, weight in REGIME_WEIGHTS:
            regime_trades: list[Trade] = []
            for symbol in symbols:
                res = run(
                    symbol, vrp, years=years, bars=cached[symbol],
                    config=(risk_cfg, strategy_cfg), equity=equity,
                )
                regime_trades.extend(res.trades)
                regime_trades.extend(run_carry(
                    symbol, vrp, years=years, bars=cached[symbol],
                    config=(risk_cfg, strategy_cfg), equity=equity,
                ))
            weighted_pnl += sum(t.pnl for t in regime_trades) * weight
            trades.extend(regime_trades)

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl < 0]
        gross_loss = abs(sum(t.pnl for t in losses))

        # Rebuild an equity path in trade order to measure drawdown honestly.
        running, peak, worst = equity, equity, 0.0
        for t in sorted(trades, key=lambda x: x.closed or x.opened):
            running += t.pnl
            peak = max(peak, running)
            worst = max(worst, (peak - running) / peak)

        by_sleeve: dict[str, dict[str, float]] = {}
        for t in trades:
            b = by_sleeve.setdefault(t.sleeve, {"n": 0, "pnl": 0.0})
            b["n"] += 1
            b["pnl"] += t.pnl

        out[name] = {
            "live": name in LIVE_VARIANTS,
            "equity": equity,
            "trades": len(trades),
            "win_rate": (len(wins) / len(trades)) if trades else None,
            "weighted_pnl": round(weighted_pnl, 0),
            "profit_factor": (sum(t.pnl for t in wins) / gross_loss) if gross_loss else None,
            "max_drawdown": worst,
            "return_pct": round(weighted_pnl / equity, 4) if equity else None,
            "by_sleeve": {k: {"n": v["n"], "pnl": round(v["pnl"], 0)} for k, v in by_sleeve.items()},
        }
    return out


if __name__ == "__main__":
    main()
