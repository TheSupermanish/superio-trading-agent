"use client";

import { useState } from "react";
import type { LiveMark, Structure } from "./types";
import PayoffChart from "./PayoffChart";

const money = (n: number) =>
  `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const money2 = (n: number) =>
  `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const tone = (n: number) => (n === 0 ? "" : n > 0 ? "up" : "down");

const ACTION_LABEL: Record<string, string> = {
  hold: "holding",
  take_profit: "closing · target hit",
  stop_loss: "closing · stopped",
  time_stop: "closing · expiry",
  time_exit: "closing · time",
};

function Progress({
  label, value, basis, tone: t,
}: { label: string; value: number; basis: string; tone: "up" | "down" }) {
  const shown = Math.min(Math.max(value, 0), 1);
  const armed = value >= 1;
  return (
    <div className="pg">
      <div className="pg-top">
        <span className="pg-label">{label}</span>
        <span className={`pg-val ${armed ? t : "muted"}`}>{(value * 100).toFixed(0)}%</span>
      </div>
      <div className="pg-track">
        <div className={`pg-fill ${t} ${armed ? "armed" : ""}`} style={{ width: `${shown * 100}%` }} />
      </div>
      <div className="pg-basis">{basis}</div>
    </div>
  );
}

export default function PositionCard({
  structure, mark,
}: { structure: Structure; mark: LiveMark | null }) {
  const [showPayoff, setShowPayoff] = useState(true);
  const [showLegs, setShowLegs] = useState(false);

  const legsSummary = structure.legs
    .map((l) => `${l.side === "sell" ? "-" : "+"}${l.strike}${l.is_call ? "C" : "P"}`)
    .join(" / ");

  const distance = mark?.distance_pct ?? null;
  const tight = distance !== null && Math.abs(distance) < 0.01;
  const spot = mark?.spot ?? null;

  const shortLeg = structure.legs.find((l) => l.side === "sell");
  const popPct = shortLeg?.delta != null ? Math.round((1 - Math.abs(shortLeg.delta)) * 100) : null;
  const payoffRatio = structure.max_loss > 0 ? (structure.max_gain / structure.max_loss).toFixed(1) : null;

  return (
    <div className={`pos ${mark && mark.action !== "hold" ? "acting" : ""}`} style={{ display: "flex", flexDirection: "column" }}>
      <div className="pos-head">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="pos-sym" style={{ fontWeight: 700 }}>{structure.underlying}</span>
          <span className="pos-kind">{structure.kind.replace(/_/g, " ")}</span>
          <span className={`badge ${structure.sleeve === "carry" ? "live" : "dry"}`}>
            {structure.sleeve}
          </span>
          {popPct != null && (
            <span
              className="badge"
              style={{
                borderColor: popPct >= 65 ? "var(--up)" : "var(--accent)",
                color: popPct >= 65 ? "var(--up)" : "var(--accent)",
                fontSize: 10,
                padding: "1px 5px",
              }}
              title="Estimated Probability of Expiring OTM based on short strike delta"
            >
              PoP ~{popPct}%
            </span>
          )}
          {payoffRatio != null && Number(payoffRatio) >= 2.0 && (
            <span
              className="badge"
              style={{
                borderColor: "var(--accent)",
                color: "var(--accent)",
                fontSize: 10,
                padding: "1px 5px",
              }}
              title="Payoff Multiple (Max Gain / Max Risk)"
            >
              {payoffRatio}x Asymmetric
            </span>
          )}
        </div>
        <div className="pos-pnl">
          {mark ? (
            <span className={tone(mark.unrealized_pnl)} style={{ fontWeight: 700, fontSize: 18 }}>
              {mark.unrealized_pnl >= 0 ? "+" : ""}{money(mark.unrealized_pnl)}
            </span>
          ) : (
            <span className="muted">no mark</span>
          )}
        </div>
      </div>

      <div className="pos-legs" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>
          <strong>{legsSummary}</strong>
          <span className="muted"> · {structure.legs[0]?.expiry} · x{structure.qty}</span>
        </span>
        <button
          className="btn-tiny"
          onClick={() => setShowLegs(!showLegs)}
          style={{
            background: "none",
            border: "1px solid var(--line)",
            color: showLegs ? "var(--accent)" : "var(--muted)",
            fontSize: 10,
            padding: "2px 6px",
            borderRadius: 3,
            cursor: "pointer",
          }}
        >
          {showLegs ? "hide legs" : "legs"}
        </button>
      </div>

      {showLegs && (
        <div style={{ background: "rgba(18, 21, 28, 0.9)", border: "1px solid var(--line)", borderRadius: 4, padding: 8, marginBottom: 12, fontSize: 11 }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ color: "var(--muted)", fontSize: 9.5, borderBottom: "1px solid var(--line)" }}>
                <th style={{ textAlign: "left", padding: "2px 4px" }}>SIDE</th>
                <th style={{ textAlign: "left", padding: "2px 4px" }}>STRIKE</th>
                <th style={{ textAlign: "left", padding: "2px 4px" }}>TYPE</th>
                <th style={{ textAlign: "right", padding: "2px 4px" }}>DELTA</th>
                <th style={{ textAlign: "right", padding: "2px 4px" }}>MID</th>
              </tr>
            </thead>
            <tbody>
              {structure.legs.map((leg, idx) => (
                <tr key={idx} style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                  <td style={{ padding: "3px 4px" }} className={leg.side === "sell" ? "down" : "up"}>
                    {leg.side.toUpperCase()}
                  </td>
                  <td style={{ padding: "3px 4px", fontWeight: 600 }}>{leg.strike}</td>
                  <td style={{ padding: "3px 4px", color: "var(--muted)" }}>{leg.is_call ? "Call" : "Put"}</td>
                  <td style={{ textAlign: "right", padding: "3px 4px", color: "var(--text)" }}>
                    {leg.delta != null ? leg.delta.toFixed(2) : "--"}
                  </td>
                  <td style={{ textAlign: "right", padding: "3px 4px", color: "var(--muted)" }}>
                    {leg.mid != null ? `$${leg.mid.toFixed(2)}` : "--"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="pos-stats">
        <div>
          <div className="label">Risked</div>
          <div className="down" style={{ fontWeight: 600 }}>{money(structure.max_loss)}</div>
        </div>
        <div>
          <div className="label">Can win</div>
          <div className="up" style={{ fontWeight: 600 }}>{money(structure.max_gain)}</div>
        </div>
        <div>
          <div className="label">Days left</div>
          <div className={mark && mark.dte <= 1 ? "down" : ""} style={{ fontWeight: 600 }}>
            {mark ? `${mark.dte}d` : "--"}
          </div>
        </div>
        <div>
          <div className="label">Spot vs short</div>
          <div className={tight ? "down" : ""} style={{ fontWeight: 600 }}>
            {mark?.spot != null && mark.short_strike != null ? (
              <>
                ${mark.spot.toFixed(2)} <span className="muted">/ {mark.short_strike}</span>
              </>
            ) : (
              "--"
            )}
          </div>
          {distance !== null ? (
            <div className={`note ${tight ? "down" : ""}`}>
              {distance >= 0 ? "+" : ""}{(distance * 100).toFixed(2)}%
              {tight ? " · tight" : ""}
            </div>
          ) : null}
        </div>
      </div>

      {mark ? (
        <div className="pos-exits">
          <Progress label="TAKE PROFIT" value={mark.tp_progress} basis={mark.tp_basis} tone="up" />
          <Progress label="STOP" value={mark.sl_progress} basis={mark.sl_basis} tone="down" />
        </div>
      ) : null}

      {mark ? (
        <div className={`pos-action ${mark.action === "hold" ? "" : "acting"}`}>
          <span className="pos-action-tag">{ACTION_LABEL[mark.action] ?? mark.action}</span>
          <span className="pos-rationale">{mark.rationale}</span>
        </div>
      ) : null}

      {/* Payoff Curve Toggle */}
      <div style={{ marginTop: 10, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <button
          onClick={() => setShowPayoff(!showPayoff)}
          style={{
            background: "none",
            border: "1px solid var(--line)",
            color: showPayoff ? "var(--accent)" : "var(--muted)",
            fontSize: 10.5,
            padding: "3px 8px",
            borderRadius: 3,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: 4,
          }}
        >
          <span>📈</span> {showPayoff ? "Hide Payoff Curve" : "Show Payoff Curve"}
        </button>
        <span style={{ fontSize: 10, color: "var(--muted)" }}>
          Net: <strong className={structure.net_price >= 0 ? "up" : "down"}>{structure.net_price >= 0 ? "+" : ""}{money2(structure.net_price)}</strong>
        </span>
      </div>

      {showPayoff && (
        <PayoffChart
          legs={structure.legs}
          netPrice={structure.net_price}
          qty={structure.qty}
          spot={spot}
          height={110}
          compact
        />
      )}

      {structure.thesis ? (
        <details className="pos-thesis-details" style={{ marginTop: 10, fontSize: 11, color: "var(--muted)" }}>
          <summary style={{ cursor: "pointer", color: "var(--muted)", textDecoration: "underline", textUnderlineOffset: 2 }}>
            AI Strategist Thesis
          </summary>
          <div className="pos-thesis" style={{ marginTop: 6 }}>{structure.thesis}</div>
        </details>
      ) : null}
    </div>
  );
}
