"use client";

import { useMemo, useState } from "react";
import type { Leg } from "./types";

interface PayoffChartProps {
  legs: Leg[];
  netPrice: number;
  qty: number;
  spot?: number | null;
  height?: number;
  compact?: boolean;
}

export function calculatePayoff(
  price: number,
  legs: Leg[],
  netPrice: number,
  qty: number
): number {
  let intrinsicTotal = 0;
  for (const leg of legs) {
    const ratio = (leg as any).ratio_qty || 1;
    const strike = leg.strike;
    let intrinsic = 0;
    if (leg.is_call) {
      intrinsic = Math.max(0, price - strike);
    } else {
      intrinsic = Math.max(0, strike - price);
    }
    const sign = leg.side === "buy" ? 1 : -1;
    intrinsicTotal += sign * intrinsic * 100 * ratio;
  }
  return qty * (netPrice * 100 + intrinsicTotal);
}

export default function PayoffChart({
  legs,
  netPrice,
  qty,
  spot,
  height = 140,
  compact = false,
}: PayoffChartProps) {
  const [hoverX, setHoverX] = useState<number | null>(null);

  const analysis = useMemo(() => {
    if (!legs || legs.length === 0) return null;

    const strikes = legs.map((l) => l.strike);
    const minStrike = Math.min(...strikes);
    const maxStrike = Math.max(...strikes);
    const spreadSpan = Math.max(maxStrike - minStrike, minStrike * 0.04);

    const refPrice = spot ?? (minStrike + maxStrike) / 2;
    const minPrice = Math.min(minStrike - spreadSpan * 0.4, refPrice * 0.94);
    const maxPrice = Math.max(maxStrike + spreadSpan * 0.4, refPrice * 1.06);

    const steps = 100;
    const stepSize = (maxPrice - minPrice) / steps;
    const points: { price: number; pnl: number }[] = [];

    let minPnl = Infinity;
    let maxPnl = -Infinity;
    const breakevens: number[] = [];

    let prevPnl: number | null = null;
    let prevPrice: number | null = null;

    for (let i = 0; i <= steps; i++) {
      const p = minPrice + i * stepSize;
      const pnl = calculatePayoff(p, legs, netPrice, qty);
      points.push({ price: p, pnl });

      if (pnl < minPnl) minPnl = pnl;
      if (pnl > maxPnl) maxPnl = pnl;

      if (prevPnl !== null && prevPrice !== null) {
        if ((prevPnl < 0 && pnl >= 0) || (prevPnl >= 0 && pnl < 0)) {
          const ratio = Math.abs(prevPnl) / (Math.abs(prevPnl) + Math.abs(pnl) || 1);
          breakevens.push(prevPrice + ratio * (p - prevPrice));
        }
      }
      prevPnl = pnl;
      prevPrice = p;
    }

    // Pad pnl range
    const pnlSpan = Math.max(maxPnl - minPnl, 100);
    const yMin = minPnl - pnlSpan * 0.15;
    const yMax = maxPnl + pnlSpan * 0.15;

    return {
      minPrice,
      maxPrice,
      points,
      minPnl,
      maxPnl,
      yMin,
      yMax,
      breakevens,
    };
  }, [legs, netPrice, qty, spot]);

  if (!analysis) return null;

  const W = 600;
  const H = height;
  const PAD_X = 14;
  const PAD_Y = 16;

  const mapX = (price: number) =>
    PAD_X + ((price - analysis.minPrice) / (analysis.maxPrice - analysis.minPrice)) * (W - PAD_X * 2);

  const mapY = (pnl: number) =>
    H - PAD_Y - ((pnl - analysis.yMin) / (analysis.yMax - analysis.yMin || 1)) * (H - PAD_Y * 2);

  const zeroY = mapY(0);

  // Path string
  const polylinePoints = analysis.points.map((pt) => `${mapX(pt.price).toFixed(1)},${mapY(pt.pnl).toFixed(1)}`).join(" ");

  // Spot X
  const spotX = spot != null ? mapX(spot) : null;
  const spotPnl = spot != null ? calculatePayoff(spot, legs, netPrice, qty) : null;

  // Hovered price
  let hoveredPrice: number | null = null;
  let hoveredPnl: number | null = null;
  if (hoverX !== null) {
    const clampedX = Math.max(PAD_X, Math.min(W - PAD_X, hoverX));
    hoveredPrice = analysis.minPrice + ((clampedX - PAD_X) / (W - PAD_X * 2)) * (analysis.maxPrice - analysis.minPrice);
    hoveredPnl = calculatePayoff(hoveredPrice, legs, netPrice, qty);
  }

  const money = (n: number) => `${n < 0 ? "-" : ""}$${Math.abs(Math.round(n)).toLocaleString()}`;

  return (
    <div className="payoff-wrap" style={{ position: "relative", width: "100%", marginTop: compact ? 8 : 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", fontSize: 10, color: "var(--muted)", marginBottom: 4 }}>
        <span>
          <strong style={{ color: "var(--text)", letterSpacing: "0.06em" }}>EXPIRATION PAYOFF</strong>
          {spot != null && (
            <span style={{ marginLeft: 8 }}>
              Spot: <strong style={{ color: "#38bdf8" }}>${spot.toFixed(2)}</strong> ({spotPnl != null ? `${spotPnl >= 0 ? "+" : ""}${money(spotPnl)}` : ""})
            </span>
          )}
        </span>
        <span>
          Max Win: <span className="up" style={{ fontWeight: 600 }}>{money(analysis.maxPnl)}</span> · Max Loss: <span className="down" style={{ fontWeight: 600 }}>{money(analysis.minPnl)}</span>
        </span>
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        style={{ overflow: "visible", background: "rgba(10, 12, 16, 0.75)", borderRadius: 4, border: "1px solid var(--line)" }}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const scale = W / rect.width;
          setHoverX((e.clientX - rect.left) * scale);
        }}
        onMouseLeave={() => setHoverX(null)}
      >
        {/* Zero baseline */}
        <line
          x1={PAD_X}
          x2={W - PAD_X}
          y1={zeroY}
          y2={zeroY}
          stroke="var(--line)"
          strokeWidth="1.5"
          strokeDasharray="4 3"
        />

        {/* Strike price markers */}
        {legs.map((leg, idx) => {
          const sx = mapX(leg.strike);
          const isShort = leg.side === "sell";
          return (
            <g key={idx}>
              <line
                x1={sx}
                x2={sx}
                y1={PAD_Y}
                y2={H - PAD_Y}
                stroke={isShort ? "var(--down)" : "var(--up)"}
                strokeWidth="1"
                strokeDasharray="2 2"
                opacity="0.45"
              />
              <text
                x={sx}
                y={isShort ? PAD_Y + 9 : H - PAD_Y - 3}
                fill={isShort ? "var(--down)" : "var(--up)"}
                fontSize="8.5"
                fontFamily="var(--mono)"
                textAnchor="middle"
                opacity="0.85"
              >
                {leg.strike}{leg.is_call ? "C" : "P"}
              </text>
            </g>
          );
        })}

        {/* Payoff Polyline */}
        <polyline
          points={polylinePoints}
          fill="none"
          stroke="var(--accent)"
          strokeWidth="2.2"
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {/* Breakeven points */}
        {analysis.breakevens.map((be, i) => {
          const bx = mapX(be);
          return (
            <circle
              key={i}
              cx={bx}
              cy={zeroY}
              r="3.5"
              fill="var(--bg)"
              stroke="var(--accent)"
              strokeWidth="1.5"
            />
          );
        })}

        {/* Current Spot Vertical Line */}
        {spotX != null && (
          <g>
            <line
              x1={spotX}
              x2={spotX}
              y1={4}
              y2={H - 4}
              stroke="#38bdf8"
              strokeWidth="1.5"
              strokeDasharray="3 3"
            />
            <circle cx={spotX} cy={mapY(spotPnl ?? 0)} r="4" fill="#38bdf8" />
            <text
              x={spotX}
              y={PAD_Y - 2}
              fill="#38bdf8"
              fontSize="8.5"
              fontFamily="var(--mono)"
              fontWeight="bold"
              textAnchor="middle"
            >
              SPOT
            </text>
          </g>
        )}

        {/* Hover Crosshair & Tooltip */}
        {hoverX != null && hoveredPrice != null && hoveredPnl != null && (
          <g>
            <line
              x1={hoverX}
              x2={hoverX}
              y1={4}
              y2={H - 4}
              stroke="var(--text)"
              strokeWidth="1"
              strokeDasharray="2 2"
              opacity="0.7"
            />
            <circle cx={hoverX} cy={mapY(hoveredPnl)} r="4.5" fill="var(--accent)" />
            <rect
              x={Math.max(PAD_X, Math.min(W - 130, hoverX - 55))}
              y={Math.max(4, Math.min(H - 30, mapY(hoveredPnl) - 26))}
              width="110"
              height="20"
              rx="3"
              fill="var(--panel-2)"
              stroke="var(--line)"
            />
            <text
              x={Math.max(PAD_X, Math.min(W - 130, hoverX - 55)) + 55}
              y={Math.max(4, Math.min(H - 30, mapY(hoveredPnl) - 26)) + 14}
              fill="var(--text)"
              fontSize="9.5"
              fontFamily="var(--mono)"
              textAnchor="middle"
            >
              ${hoveredPrice.toFixed(1)}: {hoveredPnl >= 0 ? "+" : ""}{money(hoveredPnl)}
            </text>
          </g>
        )}
      </svg>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9.5, color: "var(--muted)", marginTop: 3 }}>
        <span>${analysis.minPrice.toFixed(1)}</span>
        <span>
          {analysis.breakevens.length > 0 && (
            <span>Breakeven: {analysis.breakevens.map((b) => `$${b.toFixed(1)}`).join(", ")}</span>
          )}
        </span>
        <span>${analysis.maxPrice.toFixed(1)}</span>
      </div>

      {/* Interactive Price Inspect Slider */}
      {!compact && (
        <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 9.5, color: "var(--muted)", whiteSpace: "nowrap" }}>Scrub Price:</span>
          <input
            type="range"
            min={analysis.minPrice}
            max={analysis.maxPrice}
            step={(analysis.maxPrice - analysis.minPrice) / 100}
            value={hoveredPrice ?? (spot ?? (analysis.minPrice + analysis.maxPrice) / 2)}
            onChange={(e) => {
              const val = parseFloat(e.target.value);
              setHoverX(mapX(val));
            }}
            style={{ width: "100%", height: 4, accentColor: "var(--accent)", cursor: "pointer" }}
          />
        </div>
      )}
    </div>
  );
}
