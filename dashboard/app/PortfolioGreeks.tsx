"use client";

import { useMemo } from "react";
import type { LiveMark, Structure } from "./types";

interface PortfolioGreeksProps {
  structures: Structure[];
  marks: LiveMark[];
}

export default function PortfolioGreeks({ structures, marks }: PortfolioGreeksProps) {
  const markById = useMemo(() => new Map(marks.map((m) => [m.structure_id, m])), [marks]);

  const greeks = useMemo(() => {
    let totalDelta = 0;
    let totalTheta = 0;
    let totalVega = 0;
    const bySymbol: Record<string, { delta: number; theta: number; vega: number }> = {};

    for (const s of structures) {
      const mark = markById.get(s.id);
      const spot = mark?.spot ?? 770;
      const dte = mark?.dte ?? 25;
      const qty = s.qty || 1;

      let structDelta = 0;
      let structTheta = 0;
      let structVega = 0;

      for (const leg of s.legs) {
        const sign = leg.side === "buy" ? 1 : -1;
        const ratio = (leg as any).ratio_qty || 1;
        const delta = leg.delta ?? (leg.is_call ? 0.35 : -0.35);

        // Per 100-share contract
        const legDelta = sign * delta * 100 * ratio * qty;
        structDelta += legDelta;

        // Approximate theta: daily time decay from extrinsic value
        const intrinsic = leg.is_call ? Math.max(0, spot - leg.strike) : Math.max(0, leg.strike - spot);
        const extrinsic = Math.max(0, (leg.mid || 2.0) - intrinsic);
        const dailyTheta = extrinsic > 0 && dte > 0 ? (extrinsic * 100 * ratio * qty) / (dte * 1.5) : 0;
        // Long options bleed theta (-), short options capture theta (+)
        structTheta += -sign * dailyTheta;

        // Vega approximation: ~0.01 * spot * sqrt(T)
        const tYear = Math.max(dte, 1) / 365;
        const approxVega = (0.01 * (leg.mid || 2.0) * 100 * ratio * qty);
        structVega += sign * approxVega;
      }

      totalDelta += structDelta;
      totalTheta += structTheta;
      totalVega += structVega;

      if (!bySymbol[s.underlying]) {
        bySymbol[s.underlying] = { delta: 0, theta: 0, vega: 0 };
      }
      bySymbol[s.underlying].delta += structDelta;
      bySymbol[s.underlying].theta += structTheta;
      bySymbol[s.underlying].vega += structVega;
    }

    return { totalDelta, totalTheta, totalVega, bySymbol };
  }, [structures, markById]);

  const money = (n: number) => `${n < 0 ? "-" : ""}$${Math.abs(Math.round(n)).toLocaleString()}`;

  return (
    <div className="card" style={{ marginBottom: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
        <div>
          <div className="label" style={{ color: "var(--accent)" }}>Portfolio Greeks & Exposure Telemetry</div>
          <div style={{ fontSize: 13, color: "var(--muted)", marginTop: 2 }}>
            Real-time sensitivity surface across all open positions
          </div>
        </div>
        <span className="badge live">LIVE GREEKS DECK</span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
        {/* Net Delta */}
        <div style={{ background: "var(--panel-2)", padding: "10px 12px", borderRadius: 4, border: "1px solid var(--line)" }}>
          <div className="label">Net Delta (Δ)</div>
          <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: greeks.totalDelta >= 0 ? "var(--up)" : "var(--down)" }}>
            {greeks.totalDelta >= 0 ? "+" : ""}{greeks.totalDelta.toFixed(1)}Δ
          </div>
          <div className="note" style={{ marginTop: 4 }}>
            {greeks.totalDelta >= 0 ? "Bullish beta bias" : "Bearish beta bias"}
            <span className="muted"> (~{Math.abs(Math.round(greeks.totalDelta))} shs equiv)</span>
          </div>
        </div>

        {/* Net Theta */}
        <div style={{ background: "var(--panel-2)", padding: "10px 12px", borderRadius: 4, border: "1px solid var(--line)" }}>
          <div className="label">Daily Theta (Θ)</div>
          <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: greeks.totalTheta >= 0 ? "var(--up)" : "var(--down)" }}>
            {greeks.totalTheta >= 0 ? "+" : ""}{money(greeks.totalTheta)}/day
          </div>
          <div className="note" style={{ marginTop: 4 }}>
            {greeks.totalTheta >= 0 ? "Harvesting overnight decay" : "Net paying time decay"}
          </div>
        </div>

        {/* Net Vega */}
        <div style={{ background: "var(--panel-2)", padding: "10px 12px", borderRadius: 4, border: "1px solid var(--line)" }}>
          <div className="label">Net Vega (ν)</div>
          <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: "var(--text)" }}>
            {greeks.totalVega >= 0 ? "+" : ""}{money(greeks.totalVega)} / 1% IV
          </div>
          <div className="note" style={{ marginTop: 4 }}>
            {greeks.totalVega >= 0 ? "Long volatility expansion" : "Short volatility crush"}
          </div>
        </div>

        {/* Asset-Level Breakdown */}
        <div style={{ background: "var(--panel-2)", padding: "10px 12px", borderRadius: 4, border: "1px solid var(--line)" }}>
          <div className="label">Greeks by Underlying</div>
          <div style={{ marginTop: 6, fontSize: 11, display: "flex", flexDirection: "column", gap: 3 }}>
            {Object.entries(greeks.bySymbol).map(([sym, g]) => (
              <div key={sym} style={{ display: "flex", justifyContent: "space-between" }}>
                <strong>{sym}</strong>
                <span className={g.delta >= 0 ? "up" : "down"}>
                  {g.delta >= 0 ? "+" : ""}{g.delta.toFixed(1)}Δ
                </span>
                <span className="muted">{g.theta >= 0 ? "+" : ""}{money(g.theta)}/d</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
