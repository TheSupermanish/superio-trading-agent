"use client";

import type { EquityPoint } from "./types";

/**
 * Equity path drawn as inline SVG. No charting dependency: the shape is a
 * polyline and a baseline, and a library would be more code than the drawing.
 */
export default function EquityCurve({ points }: { points: EquityPoint[] }) {
  if (points.length < 2) {
    return <div className="empty">Equity curve appears once the agent has marked the account twice.</div>;
  }

  const W = 1000;
  const H = 220;
  const PAD = 8;

  const values = points.map((p) => p.equity);
  const start = values[0];
  const min = Math.min(...values, start);
  const max = Math.max(...values, start);
  const range = max - min || 1;

  const x = (i: number) => PAD + (i / (points.length - 1)) * (W - PAD * 2);
  const y = (v: number) => H - PAD - ((v - min) / range) * (H - PAD * 2);

  const line = points.map((p, i) => `${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join(" ");
  const area = `${PAD},${H - PAD} ${line} ${(W - PAD).toFixed(1)},${H - PAD}`;
  const latest = values[values.length - 1];
  const gaining = latest >= start;
  const stroke = gaining ? "var(--up)" : "var(--down)";

  return (
    <div className="card" style={{ padding: 12 }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none"
           role="img" aria-label="Account equity over time">
        <defs>
          <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.22" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>
        <line x1={PAD} x2={W - PAD} y1={y(start)} y2={y(start)}
              stroke="var(--line)" strokeDasharray="4 4" strokeWidth="1" />
        <polygon points={area} fill="url(#fill)" />
        <polyline points={line} fill="none" stroke={stroke} strokeWidth="2"
                  strokeLinejoin="round" strokeLinecap="round" />
      </svg>
      <div className="sub" style={{ display: "flex", justifyContent: "space-between", marginTop: 6 }}>
        <span>{new Date(points[0].ts).toLocaleString()}</span>
        <span className="muted">
          starting line ${start.toLocaleString(undefined, { maximumFractionDigits: 0 })}
        </span>
        <span>{new Date(points[points.length - 1].ts).toLocaleString()}</span>
      </div>
    </div>
  );
}
