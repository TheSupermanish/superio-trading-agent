"""Market data access: underlying prices, bars, and option chains with greeks."""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Iterable

from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import OptionChainRequest, StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame

from engine.config import SETTINGS
from engine.greeks import greeks as bs_greeks, implied_vol, year_fraction

log = logging.getLogger(__name__)


@dataclass
class Contract:
    symbol: str
    underlying: str
    expiry: date
    strike: float
    is_call: bool
    bid: float
    ask: float
    mid: float
    dte: int
    iv: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    greeks_source: str

    @property
    def spread_pct(self) -> float:
        if self.mid <= 0:
            return 1.0
        return (self.ask - self.bid) / self.mid

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["expiry"] = self.expiry.isoformat()
        d["spread_pct"] = round(self.spread_pct, 4)
        return d


@lru_cache(maxsize=1)
def _stock_client() -> StockHistoricalDataClient:
    return StockHistoricalDataClient(SETTINGS.api_key, SETTINGS.secret_key)


@lru_cache(maxsize=1)
def _option_client() -> OptionHistoricalDataClient:
    return OptionHistoricalDataClient(SETTINGS.api_key, SETTINGS.secret_key)


def _options_feed() -> OptionsFeed:
    return OptionsFeed.OPRA if SETTINGS.options_feed.lower() == "opra" else OptionsFeed.INDICATIVE


def _stock_feed() -> DataFeed:
    return DataFeed(SETTINGS.stock_feed.lower())


def underlying_price(symbol: str) -> float:
    """Mid of the latest quote, falling back to the last daily close."""
    try:
        quotes = _stock_client().get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=_stock_feed())
        )
        q = quotes[symbol]
        bid, ask = float(q.bid_price or 0), float(q.ask_price or 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
    except Exception as exc:  # noqa: BLE001 - data outages must not stop the loop
        log.warning("latest quote failed for %s: %s", symbol, exc)

    bars = daily_bars(symbol, days=5)
    if not bars:
        raise RuntimeError(f"no price available for {symbol}")
    return float(bars[-1]["close"])


def daily_bars(symbol: str, days: int = 60) -> list[dict[str, Any]]:
    start = datetime.now(timezone.utc) - timedelta(days=days * 2 + 10)
    try:
        resp = _stock_client().get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=start,
                feed=_stock_feed(),
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("daily bars failed for %s: %s", symbol, exc)
        return []
    rows = resp.data.get(symbol, [])
    return [
        {
            "ts": b.timestamp.isoformat(),
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": float(b.volume),
        }
        for b in rows
    ][-days:]


def realized_vol(symbol: str, window: int = 20) -> float | None:
    """Annualised close-to-close realized volatility."""
    import numpy as np

    bars = daily_bars(symbol, days=window + 5)
    closes = [b["close"] for b in bars]
    if len(closes) < window + 1:
        return None
    rets = np.diff(np.log(closes[-(window + 1):]))
    return float(rets.std(ddof=1) * (252 ** 0.5))


def _snapshot_fields(snap: Any) -> tuple[float, float, float | None, dict[str, float]]:
    quote = getattr(snap, "latest_quote", None)
    bid = float(getattr(quote, "bid_price", 0) or 0)
    ask = float(getattr(quote, "ask_price", 0) or 0)
    iv = getattr(snap, "implied_volatility", None)
    iv = float(iv) if iv else None
    g = getattr(snap, "greeks", None)
    gd: dict[str, float] = {}
    if g is not None:
        for name in ("delta", "gamma", "theta", "vega"):
            val = getattr(g, name, None)
            if val is not None:
                gd[name] = float(val)
    return bid, ask, iv, gd


def get_chain(
    underlying: str,
    min_dte: int,
    max_dte: int,
    kind: str | None = None,
    strike_window_pct: float = 0.12,
    spot: float | None = None,
) -> list[Contract]:
    """Fetch an option chain and guarantee every contract carries greeks.

    Greeks come from the feed when present; otherwise implied vol is solved
    from the mid price and the greeks are computed locally.
    """
    spot = spot if spot is not None else underlying_price(underlying)
    today = datetime.now(timezone.utc).date()
    req = OptionChainRequest(
        underlying_symbol=underlying,
        feed=_options_feed(),
        expiration_date_gte=today + timedelta(days=min_dte),
        expiration_date_lte=today + timedelta(days=max_dte),
        strike_price_gte=round(spot * (1 - strike_window_pct), 2),
        strike_price_lte=round(spot * (1 + strike_window_pct), 2),
    )
    if kind in {"call", "put"}:
        req.type = kind

    snapshots = _option_client().get_option_chain(req)

    contracts: list[Contract] = []
    for symbol, snap in snapshots.items():
        parsed = parse_occ_symbol(symbol)
        if parsed is None:
            continue
        root, expiry, is_call, strike = parsed
        bid, ask, iv, gd = _snapshot_fields(snap)
        if bid <= 0 or ask <= 0 or ask < bid:
            continue
        mid = (bid + ask) / 2
        dte = (expiry - today).days
        t = year_fraction(dte)

        source = "feed"
        if not gd or iv is None:
            solved_iv = iv or implied_vol(mid, spot, strike, t, is_call)
            if solved_iv is None:
                continue
            computed = bs_greeks(spot, strike, t, solved_iv, is_call)
            gd = {
                "delta": computed.delta,
                "gamma": computed.gamma,
                "theta": computed.theta,
                "vega": computed.vega,
            }
            iv = solved_iv
            source = "black-scholes"

        contracts.append(
            Contract(
                symbol=symbol,
                underlying=root,
                expiry=expiry,
                strike=strike,
                is_call=is_call,
                bid=bid,
                ask=ask,
                mid=mid,
                dte=dte,
                iv=iv,
                delta=gd.get("delta"),
                gamma=gd.get("gamma"),
                theta=gd.get("theta"),
                vega=gd.get("vega"),
                greeks_source=source,
            )
        )

    contracts.sort(key=lambda c: (c.expiry, c.is_call, c.strike))
    return contracts


def parse_occ_symbol(symbol: str) -> tuple[str, date, bool, float] | None:
    """Split an OCC symbol such as SPY260904P00640000 into its components."""
    if len(symbol) < 16:
        return None
    tail = symbol[-15:]
    root = symbol[: -15]
    try:
        expiry = datetime.strptime(tail[:6], "%y%m%d").date()
        cp = tail[6].upper()
        strike = int(tail[7:]) / 1000.0
    except (ValueError, IndexError):
        return None
    if cp not in {"C", "P"}:
        return None
    return root, expiry, cp == "C", strike


def nearest_by_delta(
    contracts: Iterable[Contract], target_delta: float, is_call: bool
) -> Contract | None:
    """Pick the contract whose |delta| is closest to the target."""
    pool = [c for c in contracts if c.is_call == is_call and c.delta is not None]
    if not pool:
        return None
    return min(pool, key=lambda c: abs(abs(c.delta) - abs(target_delta)))


def latest_mids(symbols: list[str]) -> dict[str, float]:
    """Current mid price for a set of option symbols, keyed by symbol."""
    if not symbols:
        return {}
    from alpaca.data.requests import OptionSnapshotRequest

    try:
        snaps = _option_client().get_option_snapshot(
            OptionSnapshotRequest(symbol_or_symbols=symbols, feed=_options_feed())
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("snapshot failed for %s: %s", symbols, exc)
        return {}

    out: dict[str, float] = {}
    for symbol, snap in snaps.items():
        bid, ask, _iv, _g = _snapshot_fields(snap)
        if bid > 0 and ask > 0:
            out[symbol] = (bid + ask) / 2
    return out
