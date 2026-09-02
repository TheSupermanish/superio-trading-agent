"use client";

import type { Budget } from "./types";

const money = (n: number) =>
  `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

const LABEL: Record<string, string> = {
  core: "core · short premium",
  convex: "convex · bought convexity",
  carry: "carry · financed long delta",
};

/** Payoff per dollar risked, as measured on live chains. Why the caps differ. */
const PAYOFF: Record<string, string> = {
  core: "~0.3x",
  convex: "~3.0x",
  carry: "~2.6x",
};

function Bar({ used, cap, tone }: { used: number; cap: number; tone: string }) {
  const filled = cap > 0 ? Math.min(used / cap, 1) : 0;
  const over = cap > 0 && used > cap;
  return (
    <div className="rb-track">
      <div
        className={`rb-fill ${tone} ${over ? "over" : ""}`}
        style={{ width: `${Math.max(filled * 100, used > 0 ? 1.5 : 0)}%` }}
      />
    </div>
  );
}

export default function RiskBudget({ budget }: { budget: Budget }) {
  const util = budget.total_utilisation ?? 0;

  return (
    <div className="card rb">
      <div className="rb-head">
        <div>
          <div className="label">Risk deployed</div>
          <div className="rb-total">
            {money(budget.total_used)}
            <span className="rb-of">of {money(budget.total_cap)}</span>
          </div>
        </div>
        <div className="rb-util">
          <div className={`rb-util-n ${util > 0.9 ? "down" : util > 0.5 ? "" : "muted"}`}>
            {(util * 100).toFixed(0)}%
          </div>
          <div className="note">of the {(budget.total_cap_pct * 100).toFixed(0)}% cap</div>
        </div>
      </div>

      <Bar used={budget.total_used} cap={budget.total_cap} tone="total" />

      <div className="rb-sleeves">
        {budget.sleeves.map((s) => (
          <div className="rb-row" key={s.sleeve}>
            <div className="rb-name">
              {LABEL[s.sleeve] ?? s.sleeve}
              <span className="rb-payoff">{PAYOFF[s.sleeve] ?? ""}</span>
            </div>
            <Bar used={s.used} cap={s.cap} tone={s.sleeve} />
            <div className="rb-nums">
              {money(s.used)} <span className="muted">/ {money(s.cap)}</span>
              {s.used > s.cap ? (
                <span className="down" title="held from before the cap was lowered; blocks new core entries">
                  {" "}over
                </span>
              ) : null}
            </div>
          </div>
        ))}
      </div>

      <div className="rb-foot">
        <div>
          <span className="muted">worst case</span>{" "}
          <span className="down">-{money(budget.total_cap)}</span>
        </div>
        <div>
          <span className="muted">stands down at</span>{" "}
          {(budget.drawdown_kill_pct * 100).toFixed(0)}%{" "}
          <span className="muted">
            ({money(budget.equity * budget.drawdown_kill_pct)})
          </span>
        </div>
        <div>
          <span className="muted">trades today</span> {budget.trades_today}/
          {budget.max_new_trades_per_day}
          {budget.failed_today ? (
            <span className="muted"> · {budget.failed_today} unfilled</span>
          ) : null}
        </div>
      </div>
      <div className="rb-note">
        The kill switch sits deliberately above the cap. Fully deployed the worst
        case IS the cap, so a switch below it would let one gap end the run
        having protected nothing.
      </div>
    </div>
  );
}
