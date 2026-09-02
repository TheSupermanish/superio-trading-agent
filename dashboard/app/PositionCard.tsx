"use client";

import type { LiveMark, Structure } from "./types";

const money = (n: number) =>
  `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
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
  const shown = Math.min(value, 1);
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
  const legs = structure.legs
    .map((l) => `${l.side === "sell" ? "-" : "+"}${l.strike}${l.is_call ? "C" : "P"}`)
    .join(" / ");

  const distance = mark?.distance_pct ?? null;
  // Inside 1% of a short strike is where a defined-risk position stops being
  // comfortable, so it is called out rather than left as a number to read.
  const tight = distance !== null && Math.abs(distance) < 0.01;

  return (
    <div className={`pos ${mark && mark.action !== "hold" ? "acting" : ""}`}>
      <div className="pos-head">
        <div>
          <span className="pos-sym">{structure.underlying}</span>
          <span className="pos-kind">{structure.kind.replace(/_/g, " ")}</span>
          <span className={`badge ${structure.sleeve === "carry" ? "dry" : "live"}`}>
            {structure.sleeve}
          </span>
        </div>
        <div className="pos-pnl">
          {mark ? (
            <span className={tone(mark.unrealized_pnl)}>
              {mark.unrealized_pnl >= 0 ? "+" : ""}{money(mark.unrealized_pnl)}
            </span>
          ) : (
            <span className="muted">no mark</span>
          )}
        </div>
      </div>

      <div className="pos-legs">
        {legs} <span className="muted">· {structure.legs[0]?.expiry} · x{structure.qty}</span>
      </div>

      <div className="pos-stats">
        <div>
          <div className="label">Risked</div>
          <div className="down">{money(structure.max_loss)}</div>
        </div>
        <div>
          <div className="label">Can win</div>
          <div className="up">{money(structure.max_gain)}</div>
        </div>
        <div>
          <div className="label">Days left</div>
          <div className={mark && mark.dte <= 1 ? "down" : ""}>
            {mark ? mark.dte : "--"}
          </div>
        </div>
        <div>
          <div className="label">Spot vs short</div>
          <div className={tight ? "down" : ""}>
            {mark?.spot != null && mark.short_strike != null ? (
              <>
                {mark.spot} <span className="muted">/ {mark.short_strike}</span>
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

      {structure.thesis ? <div className="pos-thesis">{structure.thesis}</div> : null}
    </div>
  );
}
