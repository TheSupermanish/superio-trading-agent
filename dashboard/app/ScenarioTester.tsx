"use client";

import { useMemo, useState } from "react";
import type { LiveMark, Structure } from "./types";
import { calculatePayoff } from "./PayoffChart";

interface ScenarioTesterProps {
  structures: Structure[];
  marks: LiveMark[];
  equity: number;
}

export default function ScenarioTester({ structures, marks, equity }: ScenarioTesterProps) {
  const [shockPct, setShockPct] = useState<number>(0);
  const markById = useMemo(() => new Map(marks.map((m) => [m.structure_id, m])), [marks]);

  const shockResults = useMemo(() => {
    let simulatedTotalPnl = 0;
    const perStructure: { id: number; symbol: string; currentPnl: number; simulatedPnl: number; spot: number; simSpot: number }[] = [];

    for (const s of structures) {
      const mark = markById.get(s.id);
      const spot = mark?.spot ?? 770;
      const simSpot = spot * (1 + shockPct / 100);
      const simPnl = calculatePayoff(simSpot, s.legs, s.net_price, s.qty);
      const curPnl = mark?.unrealized_pnl ?? 0;

      simulatedTotalPnl += simPnl;
      perStructure.push({
        id: s.id,
        symbol: s.underlying,
        currentPnl: curPnl,
        simulatedPnl: simPnl,
        spot,
        simSpot,
      });
    }

    const simEquity = equity + simulatedTotalPnl;
    const simReturnPct = equity > 0 ? (simulatedTotalPnl / equity) * 100 : 0;
    const killSwitchBreached = simReturnPct <= -8.0;

    return { simulatedTotalPnl, perStructure, simEquity, simReturnPct, killSwitchBreached };
  }, [structures, markById, equity, shockPct]);

  const money = (n: number) => `${n < 0 ? "-" : ""}$${Math.abs(Math.round(n)).toLocaleString()}`;

  const presets = [
    { label: "-5% Flash Crash", shock: -5 },
    { label: "-2% Dip", shock: -2 },
    { label: "0% Flat (Expiry)", shock: 0 },
    { label: "+2% Rally", shock: 2 },
    { label: "+5% Melt-Up", shock: 5 },
  ];

  return (
    <div className="card" style={{ marginBottom: 24, border: "1px solid var(--line)" }}>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12, gap: 10 }}>
        <div>
          <div className="label" style={{ color: "#38bdf8" }}>What-If Scenario & Market Stress Simulator</div>
          <div style={{ fontSize: 13, color: "var(--muted)", marginTop: 2 }}>
            Simulate portfolio expiration payoff under macro shock scenarios
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {presets.map((p) => (
            <button
              key={p.label}
              onClick={() => setShockPct(p.shock)}
              style={{
                background: shockPct === p.shock ? "var(--panel-2)" : "transparent",
                border: `1px solid ${shockPct === p.shock ? "var(--accent)" : "var(--line)"}`,
                color: shockPct === p.shock ? "var(--accent)" : "var(--muted)",
                fontSize: 11,
                padding: "3px 8px",
                borderRadius: 4,
                cursor: "pointer",
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Interactive Slider */}
      <div style={{ background: "var(--panel-2)", padding: "12px 16px", borderRadius: 6, border: "1px solid var(--line)", marginBottom: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>Market Price Shock:</span>
          <span style={{ fontSize: 16, fontWeight: 700, color: shockPct === 0 ? "var(--text)" : shockPct > 0 ? "var(--up)" : "var(--down)" }}>
            {shockPct >= 0 ? "+" : ""}{shockPct.toFixed(1)}% Shock
          </span>
        </div>
        <input
          type="range"
          min="-8"
          max="8"
          step="0.5"
          value={shockPct}
          onChange={(e) => setShockPct(parseFloat(e.target.value))}
          style={{ width: "100%", accentColor: "var(--accent)", cursor: "pointer" }}
        />
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
          <span>-8.0% (Extreme Selloff)</span>
          <span>0% (Unchanged at Expiration)</span>
          <span>+8.0% (Aggressive Breakout)</span>
        </div>
      </div>

      {/* Outcome Metric Deck */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12, marginBottom: 14 }}>
        <div style={{ background: "rgba(0,0,0,0.25)", padding: "10px 14px", borderRadius: 4, border: "1px solid var(--line)" }}>
          <div className="label">Simulated Expiration P&L</div>
          <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: shockResults.simulatedTotalPnl >= 0 ? "var(--up)" : "var(--down)" }}>
            {shockResults.simulatedTotalPnl >= 0 ? "+" : ""}{money(shockResults.simulatedTotalPnl)}
          </div>
          <div className="note">
            {shockResults.simReturnPct >= 0 ? "+" : ""}{shockResults.simReturnPct.toFixed(2)}% on portfolio
          </div>
        </div>

        <div style={{ background: "rgba(0,0,0,0.25)", padding: "10px 14px", borderRadius: 4, border: "1px solid var(--line)" }}>
          <div className="label">Simulated Net Equity</div>
          <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4 }}>
            {money(shockResults.simEquity)}
          </div>
          <div className="note">vs {money(equity)} current</div>
        </div>

        <div style={{ background: "rgba(0,0,0,0.25)", padding: "10px 14px", borderRadius: 4, border: "1px solid var(--line)" }}>
          <div className="label">8% Kill Switch Safety</div>
          <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: shockResults.killSwitchBreached ? "var(--down)" : "var(--up)" }}>
            {shockResults.killSwitchBreached ? "BREACHED" : "DEFENDED (PASS)"}
          </div>
          <div className="note">
            Max book loss bounded to put spread widths
          </div>
        </div>
      </div>

      {/* Per-Position Simulated Breakdown */}
      <div className="scroll" style={{ background: "rgba(0,0,0,0.2)", borderRadius: 4 }}>
        <table>
          <thead>
            <tr>
              <th>Underlying</th>
              <th>Current Spot</th>
              <th>Simulated Spot</th>
              <th>Current Mark P&amp;L</th>
              <th style={{ textAlign: "right" }}>Simulated Expiry P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {shockResults.perStructure.map((item) => (
              <tr key={item.id}>
                <td><strong>{item.symbol}</strong> (Structure #{item.id})</td>
                <td>${item.spot.toFixed(2)}</td>
                <td style={{ fontWeight: 600 }}>${item.simSpot.toFixed(2)}</td>
                <td className={item.currentPnl >= 0 ? "up" : "down"}>
                  {item.currentPnl >= 0 ? "+" : ""}{money(item.currentPnl)}
                </td>
                <td style={{ textAlign: "right", fontWeight: 700 }} className={item.simulatedPnl >= 0 ? "up" : "down"}>
                  {item.simulatedPnl >= 0 ? "+" : ""}{money(item.simulatedPnl)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
