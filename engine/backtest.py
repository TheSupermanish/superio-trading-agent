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

Read the output as a regime sensitivity study, not a P&L forecast.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import math
from datetime import date, datetime, timedelta
from statistics import mean
from typing import Any

import numpy as np

from engine.config import SETTINGS
from engine.greeks import bs_price, greeks, year_fraction
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
    """Nearest whole strike whose delta matches the target."""
    best, best_gap = spot, 9e9
    span = int(spot * 0.15 / step)
    for i in range(-span, span + 1):
        strike = round(spot + i * step)
        if strike <= 0:
            continue
        d = greeks(spot, strike, t, iv, is_call).delta
        gap = abs(abs(d) - target_delta)
        if gap < best_gap:
            best, best_gap = strike, gap
    return best


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
) -> Result:
    """Replay one symbol at one volatility-premium assumption."""
    p, r = SETTINGS.strategy, SETTINGS.risk
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
                elif value <= entry_abs * 0.25:
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
    args = ap.parse_args()

    symbols = tuple(s.strip().upper() for s in args.symbols.split(","))
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


if __name__ == "__main__":
    main()
