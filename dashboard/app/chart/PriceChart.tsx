"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import type { Bar, Trade } from "./types";

const UP = "#3ddc97";
const DOWN = "#ff5c5c";
const ACCENT = "#ffd23f";
const MUTED = "#8b94a7";
const LINE = "#232936";

const day = (iso: string) => iso.slice(0, 10) as unknown as Time;

/** Strike lines are only meaningful for the trade you are looking at. */
function strikeLines(series: ISeriesApi<"Candlestick">, trade: Trade | null) {
  const created: ReturnType<typeof series.createPriceLine>[] = [];
  if (!trade) return created;

  for (const strike of trade.levels.short_strikes) {
    created.push(
      series.createPriceLine({
        price: strike,
        color: DOWN,
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title: `short ${strike}`,
      }),
    );
  }
  for (const strike of trade.levels.long_strikes) {
    created.push(
      series.createPriceLine({
        price: strike,
        color: UP,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `cover ${strike}`,
      }),
    );
  }
  if (trade.levels.breakeven != null) {
    created.push(
      series.createPriceLine({
        price: trade.levels.breakeven,
        color: ACCENT,
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        axisLabelVisible: true,
        title: "breakeven",
      }),
    );
  }
  return created;
}

export default function PriceChart({
  bars,
  trades,
  selected,
  onSelect,
}: {
  bars: Bar[];
  trades: Trade[];
  selected: Trade | null;
  onSelect: (trade: Trade | null) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lines = useRef<ReturnType<ISeriesApi<"Candlestick">["createPriceLine"]>[]>([]);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  // Build once. Rebuilding on every prop change loses the user's zoom, which
  // makes the chart unusable for the thing it is for: looking closely at where
  // a trade was placed.
  useEffect(() => {
    if (!host.current) return;
    const c = createChart(host.current, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: MUTED,
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        fontSize: 11,
      },
      grid: { vertLines: { color: LINE }, horzLines: { color: LINE } },
      rightPriceScale: { borderColor: LINE },
      timeScale: { borderColor: LINE, rightOffset: 6 },
      crosshair: { mode: 0 },
    });
    const s = c.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderUpColor: UP,
      borderDownColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
    });
    chart.current = c;
    series.current = s;
    return () => {
      c.remove();
      chart.current = null;
      series.current = null;
    };
  }, []);

  useEffect(() => {
    const s = series.current;
    if (!s) return;
    s.setData(
      bars.map((b) => ({
        time: day(b.ts),
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      })),
    );
    chart.current?.timeScale().fitContent();
  }, [bars]);

  // Markers: one per entry, one per exit, coloured by outcome.
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    const markers = trades.flatMap((t) => {
      const out: SeriesMarker<Time>[] = [
        {
          time: day(t.opened_at),
          position: "belowBar",
          color: t.sleeve === "carry" ? ACCENT : t.net_price > 0 ? UP : "#5b9dff",
          shape: "arrowUp",
          text: `${t.kind.replace(/_/g, " ")} x${t.qty}`,
          size: 1,
        },
      ];
      if (t.closed_at) {
        const won = (t.realized_pnl ?? 0) >= 0;
        out.push({
          time: day(t.closed_at),
          position: "aboveBar",
          color: won ? UP : DOWN,
          shape: "arrowDown",
          text: `${t.close_reason ?? "closed"} ${won ? "+" : ""}${Math.round(t.realized_pnl ?? 0)}`,
          size: 1,
        });
      }
      return out;
    });
    const handle = createSeriesMarkers(s, markers);
    return () => handle.detach();
  }, [trades]);

  // Selecting a trade draws its levels. Clicking empty space clears them.
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    for (const line of lines.current) s.removePriceLine(line);
    lines.current = strikeLines(s, selected);
  }, [selected]);

  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const handler = (param: { time?: Time }) => {
      if (!param.time) return;
      const stamp = String(param.time);
      const hit = trades.find((t) => t.opened_at.slice(0, 10) === stamp)
        ?? trades.find((t) => t.closed_at?.slice(0, 10) === stamp);
      if (hit) onSelectRef.current(hit);
    };
    c.subscribeClick(handler);
    return () => c.unsubscribeClick(handler);
  }, [trades]);

  return <div ref={host} className="chart-host" />;
}
