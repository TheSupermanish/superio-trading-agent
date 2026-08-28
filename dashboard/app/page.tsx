"use client";

import { useEffect, useState } from "react";
import EquityCurve from "./EquityCurve";
import type { Snapshot } from "./types";

const POLL_MS = 60_000;

const money = (n: number) =>
  `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
const pct = (n: number | null | undefined, digits = 2) =>
  n === null || n === undefined ? "--" : `${(n * 100).toFixed(digits)}%`;

function Kpi({ label, value, note, tone }: {
  label: string; value: string; note?: string; tone?: "up" | "down";
}) {
  return (
    <div className="card">
      <div className="label">{label}</div>
      <div className={`value ${tone ?? ""}`}>{value}</div>
      {note ? <div className="note">{note}</div> : null}
    </div>
  );
}

export default function Page() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        // Cache-bust so a refreshed snapshot is picked up rather than served
        // from the CDN edge cache.
        const res = await fetch(`/snapshot.json?t=${Date.now()}`, { cache: "no-store" });
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as Snapshot;
        if (alive) setSnap(data);
      } catch {
        /* keep whatever we already rendered */
      } finally {
        if (alive) setLoading(false);
      }
    };
    load();
    const timer = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  if (!snap) {
    if (loading) {
      return (
        <div className="wrap">
          <h1>super<span>io</span></h1>
          <p className="empty">Loading the latest snapshot...</p>
        </div>
      );
    }
    return (
      <div className="wrap">
        <h1>super<span>io</span></h1>
        <p className="empty">
          No snapshot yet. Run <code>python -m engine.report</code> to generate one.
        </p>
      </div>
    );
  }

  const p = snap.performance;
  const pnlTone = p.realized_pnl >= 0 ? "up" : "down";
  const maxRejections = Math.max(1, ...snap.gates.rejections_by_gate.map((g) => g.count));

  return (
    <div className="wrap">
      <header className="top">
        <div>
          <h1>super<span>io</span></h1>
          <div className="sub">
            Autonomous defined-risk options agent on Alpaca paper trading
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <span className={`badge ${snap.dry_run ? "dry" : "live"}`}>
            {snap.dry_run ? "dry run" : "trading"}
          </span>{" "}
          <span className="badge">{snap.variant}</span>
          <div className="sub" style={{ marginTop: 6 }}>
            account {snap.account_id || "unset"} · updated{" "}
            {new Date(snap.generated_at).toLocaleString()}
          </div>
        </div>
      </header>

      <div className="grid kpis">
        <Kpi label="Realized P&L" value={money(p.realized_pnl)} tone={pnlTone}
             note={`${p.trades_closed} closed structures`} />
        <Kpi label="Return" value={pct(p.return_pct)} tone={(p.return_pct ?? 0) >= 0 ? "up" : "down"}
             note={`from ${money(p.equity_start)}`} />
        <Kpi label="Win rate" value={p.win_rate === null ? "--" : pct(p.win_rate, 1)}
             note={`${p.wins}W / ${p.losses}L`} />
        <Kpi label="Profit factor" value={p.profit_factor?.toFixed(2) ?? "--"}
             note="gross win / gross loss" />
        <Kpi label="Max drawdown" value={pct(p.max_drawdown_pct)}
             note={`kill switch at ${pct(snap.limits.total_drawdown_kill_pct, 0)}`} />
        <Kpi label="Open risk" value={money(snap.open_risk)}
             note={`cap ${pct(snap.limits.max_open_risk_pct, 0)} of equity`} />
      </div>

      <section>
        <h2>Account equity</h2>
        <EquityCurve points={snap.equity_curve} />
      </section>

      <section>
        <h2>Open structures</h2>
        <div className="scroll">
          {snap.open_structures.length === 0 ? (
            <div className="empty">Flat. No open structures.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Structure</th><th>Sleeve</th><th>Qty</th>
                  <th>Net</th><th>Max loss</th><th>Max gain</th><th>Thesis</th>
                </tr>
              </thead>
              <tbody>
                {snap.open_structures.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <strong>{s.underlying}</strong> {s.kind.replace(/_/g, " ")}
                      <div className="muted">
                        {s.legs.map((l) => `${l.side === "sell" ? "-" : "+"}${l.strike}${l.is_call ? "C" : "P"}`).join(" / ")}
                        {" · "}{s.legs[0]?.expiry}
                      </div>
                    </td>
                    <td>{s.sleeve}</td>
                    <td>{s.qty}</td>
                    <td className={s.net_price >= 0 ? "up" : "down"}>
                      {s.net_price >= 0 ? "+" : ""}{s.net_price.toFixed(2)}
                    </td>
                    <td className="down">{money(s.max_loss)}</td>
                    <td className="up">{money(s.max_gain)}</td>
                    <td className="muted">{s.thesis}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section>
        <h2>Risk gates — what the agent refused to do</h2>
        <div className="card">
          <div className="sub" style={{ marginBottom: 10 }}>
            {snap.gates.considered} structures considered · {snap.gates.approved} approved ·{" "}
            {snap.gates.rejected} rejected. Every rejection is attributed to the gate that made it.
          </div>
          {snap.gates.rejections_by_gate.map((g) => (
            <div className="gate" key={g.gate}>
              <span className="code">{g.gate}</span>
              <span className="name">{g.name}</span>
              <span className="bar">
                <span style={{ width: `${(g.count / maxRejections) * 100}%` }} />
              </span>
              <span className="n">{g.count}</span>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2>Decision trail</h2>
        <div className="scroll feed">
          <table>
            <thead>
              <tr><th>Time</th><th>Agent</th><th>Symbol</th><th>Verdict</th><th>Reasoning</th></tr>
            </thead>
            <tbody>
              {snap.recent_decisions.slice(0, 60).map((d) => {
                let reasons: string[] = [];
                try { reasons = JSON.parse(d.reasons); } catch { reasons = [d.reasons]; }
                return (
                  <tr key={d.id}>
                    <td className="muted">{new Date(d.ts).toLocaleTimeString()}</td>
                    <td>{d.agent}</td>
                    <td>{d.underlying ?? "--"}</td>
                    <td><span className={`verdict ${d.verdict}`}>{d.verdict}</span></td>
                    <td className="muted">{reasons.slice(0, 3).join(" · ")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2>Scheduled catalysts</h2>
        <div className="scroll">
          <table>
            <thead><tr><th>When</th><th>Event</th><th>Impact</th></tr></thead>
            <tbody>
              {snap.upcoming.length === 0 ? (
                <tr><td colSpan={3} className="empty">Nothing scheduled in the window.</td></tr>
              ) : snap.upcoming.map((e) => (
                <tr key={e.name + e.when}>
                  <td>{new Date(e.when).toLocaleString()}</td>
                  <td>{e.name}</td>
                  <td className={e.impact === "high" ? "down" : "muted"}>{e.impact}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <footer>
        Paper trading only. Hypothetical results, not investment advice. Options carry risk
        including total loss of premium. Built for the Alpaca AI Trading Agents Hackathon.
      </footer>
    </div>
  );
}
