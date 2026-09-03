"use client";

import Nav from "../Nav";
import { useEffect, useMemo, useState } from "react";
import PriceChart from "./PriceChart";
import type { ChartPayload, Trade } from "./types";
import PayoffChart from "../PayoffChart";

const POLL_MS = 60_000;
const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

const money = (n: number) =>
  `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const pct = (n: number, digits = 0) => `${(n * 100).toFixed(digits)}%`;
const tone = (n: number | null | undefined) =>
  n === null || n === undefined || n === 0 ? "" : n > 0 ? "up" : "down";

function legLine(t: Trade) {
  return t.legs
    .map((l) => `${l.side === "sell" ? "-" : "+"}${l.strike}${l.is_call ? "C" : "P"}`)
    .join(" / ");
}

/** The gate trail is stored as a JSON list of strings, or a bare string. */
function parseGates(raw: string): string[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed.map(String);
  } catch {
    /* not JSON, fall through */
  }
  return [raw];
}

function Why({ trade, spot }: { trade: Trade; spot: number | null }) {
  const gates = parseGates(trade.gates);
  const risked = trade.max_loss;
  const payoff = risked ? trade.max_gain / risked : 0;

  return (
    <div className="why">
      <div className="why-head">
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span className="why-sym" style={{ fontWeight: 700 }}>{trade.underlying}</span>{" "}
          <span className="why-kind">{trade.kind.replace(/_/g, " ")}</span>
          <span className={`badge ${trade.sleeve === "carry" ? "live" : "dry"}`}>
            {trade.sleeve}
          </span>
        </div>
        <div className="muted" style={{ fontSize: 11 }}>
          {new Date(trade.opened_at).toLocaleString()}
          {trade.closed_at ? ` → ${new Date(trade.closed_at).toLocaleString()}` : " · open position"}
        </div>
      </div>

      <div className="why-legs">
        <strong>{legLine(trade)}</strong> · {trade.legs[0]?.expiry} · x{trade.qty}
      </div>

      <div className="why-grid">
        <div>
          <div className="label">Entry</div>
          <div className={`value ${tone(trade.net_price)}`}>
            {trade.net_price >= 0 ? "+" : ""}{trade.net_price.toFixed(2)}
            <span className="unit">{trade.net_price >= 0 ? " credit" : " debit"}</span>
          </div>
        </div>
        <div>
          <div className="label">Risked</div>
          <div className="value down">{money(risked)}</div>
        </div>
        <div>
          <div className="label">Can win</div>
          <div className="value up">{money(trade.max_gain)}</div>
          <div className="note">{payoff.toFixed(2)}x payoff</div>
        </div>
        <div>
          <div className="label">{trade.closed_at ? "Realized" : "Status"}</div>
          <div className={`value ${tone(trade.realized_pnl)}`}>
            {trade.realized_pnl != null
              ? `${trade.realized_pnl >= 0 ? "+" : ""}${money(trade.realized_pnl)}`
              : trade.status}
          </div>
          {trade.close_reason ? <div className="note">{trade.close_reason}</div> : null}
        </div>
      </div>

      {/* Payoff Profile Curve */}
      <PayoffChart
        legs={trade.legs as any}
        netPrice={trade.net_price}
        qty={trade.qty}
        spot={spot}
        height={130}
      />

      <div className="exits" style={{ marginTop: 14 }}>
        <div className="exit tp">
          <div className="exit-tag">TAKE PROFIT</div>
          <div className="exit-val up">{money(trade.take_profit.target_value)}</div>
          <div className="exit-basis">
            {pct(trade.take_profit.target_pct)} · {trade.take_profit.basis}
          </div>
        </div>
        <div className="exit sl">
          <div className="exit-tag">STOP LOSS</div>
          <div className="exit-val down">{money(trade.stop.target_value)}</div>
          <div className="exit-basis">
            {trade.sleeve === "carry"
              ? `${trade.stop.target_pct.toFixed(1)}x risk`
              : pct(trade.stop.target_pct)}{" "}
            · {trade.stop.basis}
          </div>
        </div>
      </div>

      <div className="why-note">
        An option spread is managed on premium and defined risk, not underlying price alone. The horizontal chart lines show the critical inflection points: short strikes where downside begins, protective covers that cap the loss, and the net breakeven.
      </div>

      {trade.thesis ? (
        <div className="thesis">
          <div className="label">AI Strategist Thesis</div>
          <p style={{ lineHeight: 1.6 }}>{trade.thesis}</p>
        </div>
      ) : null}

      {gates.length ? (
        <div className="thesis">
          <div className="label">Deterministic Risk Gates Cleared</div>
          <ul className="gatelist">
            {gates.map((g, i) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export default function ChartPage() {
  const [data, setData] = useState<ChartPayload | null>(null);
  const [symbol, setSymbol] = useState("SPY");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [tradeFilter, setTradeFilter] = useState<"all" | "open" | "closed">("all");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch(`${BASE}/chart.json?t=${Date.now()}`, { cache: "no-store" });
        if (!res.ok) throw new Error(String(res.status));
        const payload = (await res.json()) as ChartPayload;
        if (alive) {
          setData(payload);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "unreachable");
      }
    };
    load();
    const timer = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const symbols = useMemo(() => Object.keys(data?.bars ?? {}), [data]);
  const allSymbolTrades = useMemo(
    () => (data?.trades ?? []).filter((t) => t.underlying === symbol),
    [data, symbol],
  );

  const trades = useMemo(() => {
    return allSymbolTrades.filter((t) => {
      if (tradeFilter === "open") return !t.closed_at;
      if (tradeFilter === "closed") return !!t.closed_at;
      return true;
    });
  }, [allSymbolTrades, tradeFilter]);

  const selected = useMemo(
    () => allSymbolTrades.find((t) => t.id === selectedId) ?? allSymbolTrades[allSymbolTrades.length - 1] ?? null,
    [allSymbolTrades, selectedId],
  );

  const bars = data?.bars[symbol] ?? [];
  const latestBar = bars.length > 0 ? bars[bars.length - 1] : null;
  const prevBar = bars.length > 1 ? bars[bars.length - 2] : null;
  const dayChange = latestBar && prevBar ? latestBar.close - prevBar.close : null;
  const dayChangePct = latestBar && prevBar ? (dayChange! / prevBar.close) * 100 : null;

  if (!data) {
    return (
      <div className="wrap">
        <Nav here="/chart" />
        <header className="top">
          <h1>super<span>io</span> chart</h1>
        </header>
        <div className="empty">{error ? `chart.json unreachable (${error})` : "Loading market tape..."}</div>
      </div>
    );
  }

  return (
    <div className="wrap">
      <Nav here="/chart" />
      <header className="top" style={{ alignItems: "center" }}>
        <div>
          <h1 style={{ fontSize: 22 }}>
            Options Tape &amp; Strike Ladder
          </h1>
          <div className="sub" style={{ marginTop: 4 }}>
            Candlestick price action overlaid with options structures, strike ladders, protective boundaries, and expiration payoffs
          </div>
        </div>
        <div className="tabs">
          {symbols.map((s) => (
            <button
              key={s}
              className={`tab ${s === symbol ? "on" : ""}`}
              onClick={() => {
                setSymbol(s);
                setSelectedId(null);
              }}
            >
              {s}
            </button>
          ))}
        </div>
      </header>

      {/* Symbol Tape Quote Bar */}
      {latestBar && (
        <div
          className="card"
          style={{
            padding: "12px 18px",
            marginBottom: 16,
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 16,
          }}
        >
          <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
            <span style={{ fontSize: 18, fontWeight: 700 }}>{symbol}</span>
            <span style={{ fontSize: 22, fontWeight: 700, letterSpacing: "-0.02em" }}>
              ${latestBar.close.toFixed(2)}
            </span>
            {dayChange != null && dayChangePct != null && (
              <span className={dayChange >= 0 ? "up" : "down"} style={{ fontWeight: 600, fontSize: 13 }}>
                {dayChange >= 0 ? "+" : ""}{dayChange.toFixed(2)} ({dayChange >= 0 ? "+" : ""}{dayChangePct.toFixed(2)}%)
              </span>
            )}
          </div>
          <div style={{ display: "flex", gap: 16, fontSize: 11, color: "var(--muted)" }}>
            <div>OPEN: <strong style={{ color: "var(--text)" }}>${latestBar.open.toFixed(2)}</strong></div>
            <div>HIGH: <strong style={{ color: "var(--text)" }}>${latestBar.high.toFixed(2)}</strong></div>
            <div>LOW: <strong style={{ color: "var(--text)" }}>${latestBar.low.toFixed(2)}</strong></div>
            <div>VOLUME: <strong style={{ color: "var(--text)" }}>{latestBar.volume?.toLocaleString() ?? "--"}</strong></div>
          </div>
        </div>
      )}

      <section>
        <div className="chart-card">
          <PriceChart
            bars={bars}
            trades={allSymbolTrades}
            selected={selected}
            onSelect={(t) => setSelectedId(t?.id ?? null)}
          />
        </div>
        <div className="legend" style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap" }}>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
            <span><i className="sw up" /> Protective cover strike</span>
            <span><i className="sw down" /> Short strike (risk boundary)</span>
            <span><i className="sw acc" /> Breakeven level</span>
          </div>
          <div style={{ display: "flex", gap: 16 }}>
            <span>▲ Trade entry</span>
            <span>▼ Trade exit</span>
          </div>
        </div>
      </section>

      <section className="split">
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 10 }}>
            <h2 style={{ margin: 0 }}>
              {symbol} structures ({allSymbolTrades.length})
            </h2>
            <div style={{ display: "flex", gap: 4 }}>
              {(["all", "open", "closed"] as const).map((filter) => (
                <button
                  key={filter}
                  onClick={() => setTradeFilter(filter)}
                  style={{
                    background: tradeFilter === filter ? "var(--panel-2)" : "transparent",
                    border: `1px solid ${tradeFilter === filter ? "var(--accent)" : "var(--line)"}`,
                    color: tradeFilter === filter ? "var(--accent)" : "var(--muted)",
                    fontSize: 10.5,
                    padding: "2px 8px",
                    borderRadius: 3,
                    cursor: "pointer",
                    textTransform: "capitalize",
                  }}
                >
                  {filter}
                </button>
              ))}
            </div>
          </div>

          <div className="scroll">
            {trades.length === 0 ? (
              <div className="empty">No {tradeFilter} structures for {symbol}.</div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Opened</th>
                    <th>Structure</th>
                    <th>Qty</th>
                    <th>Risked</th>
                    <th>P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t) => (
                    <tr
                      key={t.id}
                      className={`clickable ${t.id === selected?.id ? "on" : ""}`}
                      onClick={() => setSelectedId(t.id)}
                    >
                      <td className="muted" style={{ whiteSpace: "nowrap" }}>
                        {new Date(t.opened_at).toLocaleDateString()}
                      </td>
                      <td>
                        <strong>{t.kind.replace(/_/g, " ")}</strong>
                        <div className="muted">{legLine(t)}</div>
                      </td>
                      <td>{t.qty}</td>
                      <td className="down" style={{ fontWeight: 600 }}>{money(t.max_loss)}</td>
                      <td className={tone(t.realized_pnl)} style={{ fontWeight: 600 }}>
                        {t.realized_pnl != null
                          ? `${t.realized_pnl >= 0 ? "+" : ""}${money(t.realized_pnl)}`
                          : "OPEN"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        <div>
          <h2>Structure Inspection & Payoff</h2>
          {selected ? (
            <Why trade={selected} spot={latestBar ? latestBar.close : null} />
          ) : (
            <div className="empty">Pick a structure to see its levels and its reasoning.</div>
          )}
        </div>
      </section>

      <footer>
        Candles are daily closes from Alpaca. Published {new Date(data.generated_at).toLocaleString()}.
      </footer>
    </div>
  );
}
