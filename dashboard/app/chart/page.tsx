"use client";

import Nav from "../Nav";
import { useEffect, useMemo, useState } from "react";
import PriceChart from "./PriceChart";
import type { ChartPayload, Trade } from "./types";

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

function Why({ trade }: { trade: Trade }) {
  const gates = parseGates(trade.gates);
  const risked = trade.max_loss;
  const payoff = risked ? trade.max_gain / risked : 0;

  return (
    <div className="why">
      <div className="why-head">
        <div>
          <span className="why-sym">{trade.underlying}</span>{" "}
          <span className="why-kind">{trade.kind.replace(/_/g, " ")}</span>
          <span className={`badge ${trade.sleeve === "carry" ? "dry" : "live"}`}>
            {trade.sleeve}
          </span>
        </div>
        <div className="muted">
          {new Date(trade.opened_at).toLocaleString()}
          {trade.closed_at ? ` → ${new Date(trade.closed_at).toLocaleString()}` : " · open"}
        </div>
      </div>

      <div className="why-legs">{legLine(trade)} · {trade.legs[0]?.expiry} · x{trade.qty}</div>

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
          <div className="note">{payoff.toFixed(2)}x</div>
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

      <div className="exits">
        <div className="exit tp">
          <div className="exit-tag">TAKE PROFIT</div>
          <div className="exit-val up">{money(trade.take_profit.target_value)}</div>
          <div className="exit-basis">
            {pct(trade.take_profit.target_pct)} · {trade.take_profit.basis}
          </div>
        </div>
        <div className="exit sl">
          <div className="exit-tag">STOP</div>
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
        An option spread has no take-profit price on the underlying: it is closed
        on premium, not on where the index prints. The lines on the chart are the
        levels that do matter, the short strike where the position starts losing
        and the cover that stops the loss.
      </div>

      {trade.thesis ? (
        <div className="thesis">
          <div className="label">Why this trade</div>
          <p>{trade.thesis}</p>
        </div>
      ) : null}

      {gates.length ? (
        <div className="thesis">
          <div className="label">Gates it had to clear</div>
          <ul className="gatelist">
            {gates.map((g, i) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
          <div className="note">
            Matched to the nearest approval on this underlying at or before the
            open, so it is the trail that produced this shape rather than a hard
            join by id.
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default function ChartPage() {
  const [data, setData] = useState<ChartPayload | null>(null);
  const [symbol, setSymbol] = useState("SPY");
  const [selectedId, setSelectedId] = useState<number | null>(null);
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
  const trades = useMemo(
    () => (data?.trades ?? []).filter((t) => t.underlying === symbol),
    [data, symbol],
  );
  const selected = useMemo(
    () => trades.find((t) => t.id === selectedId) ?? null,
    [trades, selectedId],
  );

  if (!data) {
    return (
      <div className="wrap">
      <Nav here="/chart" />
        <header className="top">
          <h1>super<span>io</span> chart</h1>
        </header>
        <div className="empty">{error ? `chart.json unreachable (${error})` : "Loading..."}</div>
      </div>
    );
  }

  const bars = data.bars[symbol] ?? [];

  return (
    <div className="wrap">
      <Nav here="/chart" />
      <header className="top">
        <div>
          <h1>super<span>io</span> chart</h1>
          <div className="sub">
            Every structure on the tape it was placed against. Click a trade, or an
            arrow on the chart, to draw its strikes and read why it was taken.
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

      <section>
        <div className="chart-card">
          <PriceChart
            bars={bars}
            trades={trades}
            selected={selected}
            onSelect={(t) => setSelectedId(t?.id ?? null)}
          />
        </div>
        <div className="legend">
          <span><i className="sw up" /> covered / cover strike</span>
          <span><i className="sw down" /> short strike, where loss begins</span>
          <span><i className="sw acc" /> breakeven</span>
          <span>▲ entry</span>
          <span>▼ exit</span>
        </div>
      </section>

      <section className="split">
        <div>
          <h2>{symbol} structures ({trades.length})</h2>
          <div className="scroll">
            {trades.length === 0 ? (
              <div className="empty">Nothing traded on {symbol} yet.</div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Opened</th><th>Structure</th><th>Qty</th>
                    <th>Risked</th><th>P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t) => (
                    <tr
                      key={t.id}
                      className={`clickable ${t.id === selectedId ? "on" : ""}`}
                      onClick={() => setSelectedId(t.id)}
                    >
                      <td className="muted">
                        {new Date(t.opened_at).toLocaleDateString()}
                      </td>
                      <td>
                        {t.kind.replace(/_/g, " ")}
                        <div className="muted">{legLine(t)}</div>
                      </td>
                      <td>{t.qty}</td>
                      <td className="down">{money(t.max_loss)}</td>
                      <td className={tone(t.realized_pnl)}>
                        {t.realized_pnl != null
                          ? `${t.realized_pnl >= 0 ? "+" : ""}${money(t.realized_pnl)}`
                          : "open"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        <div>
          <h2>Why, and where it exits</h2>
          {selected ? (
            <Why trade={selected} />
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
