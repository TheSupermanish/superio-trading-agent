"use client";

import { useMemo, useState } from "react";
import type { EquityPoint } from "./types";

const money = (n: number) =>
  `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pct = (n: number | null | undefined, digits = 2) =>
  n === null || n === undefined ? "--" : `${(n * 100).toFixed(digits)}%`;

export default function EquityCurve({ points }: { points: EquityPoint[] }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  if (points.length < 2) {
    return <div className="empty">Equity curve appears once the agent has marked the account twice.</div>;
  }

  const W = 1000;
  const H = 240;
  const PAD_X = 14;
  const PAD_Y = 24;

  const values = points.map((p) => p.equity);
  const start = values[0];
  const min = Math.min(...values, start);
  const max = Math.max(...values, start);
  const peak = Math.max(...values);
  const range = max - min || 1;

  const x = (i: number) => PAD_X + (i / (points.length - 1)) * (W - PAD_X * 2);
  const y = (v: number) => H - PAD_Y - ((v - min) / range) * (H - PAD_Y * 2);

  const line = points.map((p, i) => `${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join(" ");
  const area = `${PAD_X},${H - PAD_Y} ${line} ${(W - PAD_X).toFixed(1)},${H - PAD_Y}`;
  const latest = values[values.length - 1];
  const gaining = latest >= start;
  const stroke = gaining ? "var(--up)" : "var(--down)";

  const activePoint = hoverIndex !== null ? points[hoverIndex] : points[points.length - 1];
  const activeEquity = activePoint.equity;
  const activeGain = activeEquity - start;
  const activeGainPct = start ? activeGain / start : 0;

  return (
    <div className="card" style={{ padding: "16px 18px", position: "relative" }}>
      {/* Interactive Top Stats Bar */}
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12, gap: 12 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
          <div>
            <span style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
              {hoverIndex !== null ? "Inspected Equity" : "Latest Equity"}
            </span>
            <div style={{ fontSize: 24, fontWeight: 700, letterSpacing: "-0.02em", color: "var(--text)" }}>
              {money(activeEquity)}
            </div>
          </div>
          <div style={{ fontSize: 14, fontWeight: 600 }} className={activeGain >= 0 ? "up" : "down"}>
            {activeGain >= 0 ? "+" : ""}{pct(activeGainPct)} ({activeGain >= 0 ? "+" : ""}{money(activeGain)})
          </div>
        </div>

        <div style={{ display: "flex", gap: 16, fontSize: 11, color: "var(--muted)" }}>
          <div>
            <span style={{ color: "var(--muted)" }}>PEAK: </span>
            <strong style={{ color: "var(--text)" }}>{money(peak)}</strong>
          </div>
          <div>
            <span style={{ color: "var(--muted)" }}>LOW: </span>
            <strong style={{ color: "var(--text)" }}>{money(min)}</strong>
          </div>
          <div>
            <span style={{ color: "var(--muted)" }}>POINTS: </span>
            <strong style={{ color: "var(--text)" }}>{points.length}</strong>
          </div>
          {hoverIndex !== null && (
            <div style={{ color: "var(--accent)" }}>
              {new Date(activePoint.ts).toLocaleString()}
            </div>
          )}
        </div>
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        preserveAspectRatio="none"
        role="img"
        aria-label="Account equity curve over time"
        style={{ overflow: "visible", cursor: "crosshair" }}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const scale = (e.clientX - rect.left) / rect.width;
          const idx = Math.min(points.length - 1, Math.max(0, Math.round(scale * (points.length - 1))));
          setHoverIndex(idx);
        }}
        onMouseLeave={() => setHoverIndex(null)}
      >
        <defs>
          <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
            <stop offset="85%" stopColor={stroke} stopOpacity="0.04" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* Starting line */}
        <line
          x1={PAD_X}
          x2={W - PAD_X}
          y1={y(start)}
          y2={y(start)}
          stroke="var(--line)"
          strokeDasharray="4 4"
          strokeWidth="1.2"
        />
        <text
          x={PAD_X + 6}
          y={y(start) - 6}
          fill="var(--muted)"
          fontSize="9.5"
          fontFamily="var(--mono)"
        >
          START ${start.toLocaleString(undefined, { maximumFractionDigits: 0 })}
        </text>

        {/* Peak High-Water Mark line */}
        {peak > start && (
          <>
            <line
              x1={PAD_X}
              x2={W - PAD_X}
              y1={y(peak)}
              y2={y(peak)}
              stroke="rgba(61, 220, 151, 0.3)"
              strokeDasharray="2 3"
              strokeWidth="1"
            />
            <text
              x={W - PAD_X - 6}
              y={y(peak) - 5}
              fill="var(--up)"
              fontSize="9"
              fontFamily="var(--mono)"
              textAnchor="end"
              opacity="0.8"
            >
              HIGH-WATER MARK ${peak.toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </text>
          </>
        )}

        {/* Shaded Area & Polyline */}
        <polygon points={area} fill="url(#equityFill)" />
        <polyline
          points={line}
          fill="none"
          stroke={stroke}
          strokeWidth="2.4"
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {/* Hover Crosshair Marker */}
        {hoverIndex !== null && (
          <g>
            <line
              x1={x(hoverIndex)}
              x2={x(hoverIndex)}
              y1={PAD_Y}
              y2={H - PAD_Y}
              stroke="var(--text)"
              strokeWidth="1"
              strokeDasharray="3 3"
              opacity="0.75"
            />
            <circle
              cx={x(hoverIndex)}
              cy={y(points[hoverIndex].equity)}
              r="5"
              fill="var(--accent)"
              stroke="var(--bg)"
              strokeWidth="2"
            />
          </g>
        )}
      </svg>

      <div className="sub" style={{ display: "flex", justifyContent: "space-between", marginTop: 8 }}>
        <span>{new Date(points[0].ts).toLocaleString()}</span>
        <span className="muted">Hover along timeline to inspect equity marks</span>
        <span>{new Date(points[points.length - 1].ts).toLocaleString()}</span>
      </div>
    </div>
  );
}
