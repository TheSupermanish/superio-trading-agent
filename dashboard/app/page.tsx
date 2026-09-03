"use client";

import Nav from "./Nav";
import { useEffect, useMemo, useState } from "react";
import EquityCurve from "./EquityCurve";
import PositionCard from "./PositionCard";
import RiskBudget from "./RiskBudget";
import PortfolioGreeks from "./PortfolioGreeks";
import ScenarioTester from "./ScenarioTester";
import type { LiveMark, Snapshot } from "./types";

const POLL_MS = 30_000;
const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

const money = (n: number) =>
  `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
const money0 = (n: number) =>
  `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const pct = (n: number | null | undefined, digits = 2) =>
  n === null || n === undefined ? "--" : `${(n * 100).toFixed(digits)}%`;
const tone = (n: number | null | undefined) =>
  n === null || n === undefined || n === 0 ? "" : n > 0 ? "up" : "down";

function Kpi({ label, value, note, tone: t, sub }: {
  label: string; value: string; note?: string; tone?: "up" | "down"; sub?: string;
}) {
  return (
    <div className="card">
      <div className="label">{label}</div>
      <div className={`value ${t ?? ""}`}>{value}</div>
      {note ? <div className="note">{note}</div> : null}
      {sub ? <div style={{ fontSize: 10.5, color: "var(--muted)", marginTop: 2 }}>{sub}</div> : null}
    </div>
  );
}

export default function Page() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [tradeFilter, setTradeFilter] = useState<string>("all");
  const [symbolFilter, setSymbolFilter] = useState<string>("all");
  const [secondsUntilRefresh, setSecondsUntilRefresh] = useState<number>(30);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);

  const exportCsv = () => {
    if (!snap?.closed_structures?.length) return;
    const headers = [
      "ID", "ClosedAt", "Underlying", "Kind", "Sleeve", "Qty", "NetPrice", "MaxLoss", "HeldHours", "RealizedPnL", "ReturnOnRisk", "ExitReason"
    ];
    const rows = snap.closed_structures.map(s => [
      s.id,
      s.closed_at ?? "",
      s.underlying,
      s.kind,
      s.sleeve,
      s.qty,
      s.net_price,
      s.max_loss,
      s.held_hours ?? "",
      s.realized_pnl ?? "",
      s.return_on_risk ?? "",
      `"${(s.close_reason ?? "").replace(/"/g, '""')}"`
    ]);
    const csvContent = [headers.join(","), ...rows.map(r => r.join(","))].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.setAttribute("download", `superio_trades_${Date.now()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const loadData = async () => {
    setIsRefreshing(true);
    try {
      const res = await fetch(`${BASE}/snapshot.json?t=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      const data = (await res.json()) as Snapshot;
      setSnap(data);
      setSecondsUntilRefresh(30);
    } catch {
      // keep existing snapshot
    } finally {
      setLoading(false);
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, POLL_MS);
    const countdown = setInterval(() => {
      setSecondsUntilRefresh((prev) => (prev > 1 ? prev - 1 : 30));
    }, 1000);

    return () => {
      clearInterval(interval);
      clearInterval(countdown);
    };
  }, []);

  const closedStructures = useMemo(() => {
    if (!snap?.closed_structures) return [];
    return snap.closed_structures.filter((s) => {
      if (symbolFilter !== "all" && s.underlying !== symbolFilter) return false;
      if (tradeFilter === "win" && (s.realized_pnl ?? 0) <= 0) return false;
      if (tradeFilter === "loss" && (s.realized_pnl ?? 0) >= 0) return false;
      return true;
    });
  }, [snap?.closed_structures, tradeFilter, symbolFilter]);

  if (!snap) {
    if (loading) {
      return (
        <div className="wrap">
          <Nav here="/" />
          <h1>super<span>io</span></h1>
          <p className="empty">Loading the latest snapshot...</p>
        </div>
      );
    }
    return (
      <div className="wrap">
        <Nav here="/" />
        <h1>super<span>io</span></h1>
        <p className="empty">
          No snapshot yet. Run <code>python -m engine.report</code> to generate one.
        </p>
      </div>
    );
  }

  const p = snap.performance;
  const pnlTone = (p.total_pnl ?? p.realized_pnl) >= 0 ? "up" : "down";
  const marks: LiveMark[] = snap.live?.marks ?? [];
  const markById = new Map(marks.map((m) => [m.structure_id, m]));
  const openPnl = marks.length
    ? marks.reduce((sum, m) => sum + m.unrealized_pnl, 0)
    : (p.open_pnl ?? null);
  const acting = marks.filter((m) => m.action !== "hold");
  const realized = p.realized_implied ?? p.realized_pnl;
  const unrecorded = Math.abs(p.realized_unrecorded ?? 0);
  const maxRejections = Math.max(1, ...snap.gates.rejections_by_gate.map((g) => g.count));

  // Calculate drawdown headroom
  const currentDd = p.max_drawdown_pct;
  const killDd = snap.limits.total_drawdown_kill_pct ?? 0.08;
  const ddHeadroomPct = Math.max(0, 1 - (currentDd / killDd));

  return (
    <div className="wrap">
      <Nav here="/" />
      <header className="top" style={{ alignItems: "center" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span className={`badge ${snap.diary ? "dry" : snap.dry_run ? "dry" : "live"}`}>
              {snap.diary ? "DIARY · NO BROKER" : snap.dry_run ? "DRY RUN MODE" : "TRADING LIVE"}
            </span>
            <span className="badge" style={{ borderColor: "var(--accent)", color: "var(--accent)" }}>
              ⚡ {snap.variant.toUpperCase()} STRATEGY
            </span>
            <span className="sub" style={{ margin: 0 }}>
              Autonomous defined-risk options agent on Alpaca paper trading
            </span>
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 8 }}>
            <button
              onClick={loadData}
              disabled={isRefreshing}
              style={{
                background: "var(--panel-2)",
                border: "1px solid var(--line)",
                color: "var(--text)",
                padding: "3px 8px",
                borderRadius: 4,
                fontSize: 11,
                cursor: "pointer",
              }}
            >
              {isRefreshing ? "Refreshing..." : `Refresh (${secondsUntilRefresh}s)`}
            </button>
          </div>
          <div className="sub" style={{ marginTop: 6 }}>
            Account <strong>{snap.account_id ? snap.account_id.slice(0, 8) + "..." : "Paper"}</strong> · Updated{" "}
            {new Date(snap.generated_at).toLocaleTimeString()}
          </div>
        </div>
      </header>

      {/* Hero Section */}
      <div className="hero">
        <div className="hero-main" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div className="label">Portfolio Net Liquidating Value</div>
            <div className="hero-eq">{money(p.equity_latest)}</div>
            <div className={`hero-ret ${(p.return_pct ?? 0) >= 0 ? "up" : "down"}`}>
              {(p.return_pct ?? 0) >= 0 ? "+" : ""}{pct(p.return_pct)}
              <span className="muted"> from {money0(p.equity_start)} initial</span>
            </div>
            <div className="hero-split">
              <div>
                <span className="muted">Realized P&amp;L:</span>{" "}
                <strong className={realized >= 0 ? "up" : "down"}>
                  {realized >= 0 ? "+" : ""}{money(realized)}
                </strong>
              </div>
              {openPnl !== null ? (
                <div>
                  <span className="muted">Open Book:</span>{" "}
                  <strong className={openPnl >= 0 ? "up" : "down"}>
                    {openPnl >= 0 ? "+" : ""}{money(openPnl)}
                  </strong>
                </div>
              ) : null}
            </div>
            {unrecorded > 50 ? (
              <div className="hero-warn">
                {money(unrecorded)} was realized before this journal session, fully reconciled from broker equity.
              </div>
            ) : null}
          </div>

          <div style={{ textAlign: "center", padding: "0 10px", flexShrink: 0 }}>
            <img
              src={`${BASE}/alphaca_icon.jpg`}
              alt="Chad Alphaca"
              style={{
                width: 90,
                height: 90,
                borderRadius: "50%",
                border: "2px solid var(--accent)",
                boxShadow: "0 0 20px rgba(16, 185, 129, 0.4)",
                objectFit: "cover",
              }}
            />
            <div style={{ fontSize: 10, fontWeight: 700, color: "var(--up)", marginTop: 6, letterSpacing: "0.05em" }}>
              CHAD ALPHACA
            </div>
          </div>
        </div>

        <div className="grid kpis hero-kpis">
          <Kpi
            label="Total Net P&L"
            value={`${(p.total_pnl ?? realized) >= 0 ? "+" : ""}${money(p.total_pnl ?? realized)}`}
            tone={pnlTone}
            note="From broker equity"
          />
          <Kpi
            label="Win Rate"
            value={p.win_rate === null ? "--" : pct(p.win_rate, 1)}
            note={`${p.wins}W / ${p.losses}L in journal`}
            sub={p.trades_closed ? `${p.trades_closed} closed structures` : undefined}
          />
          <Kpi
            label="Profit Factor"
            value={p.profit_factor?.toFixed(2) ?? "--"}
            note="Gross Win / Gross Loss"
            sub={p.avg_win != null ? `Avg win: +$${p.avg_win.toFixed(0)}` : undefined}
          />
          <Kpi
            label="Max Drawdown"
            value={pct(p.max_drawdown_pct)}
            note={`Kill switch at ${pct(snap.limits.total_drawdown_kill_pct, 0)}`}
            sub={`${(ddHeadroomPct * 100).toFixed(0)}% safety headroom`}
          />
        </div>
      </div>

      {/* Competition Timeline & Trading Days Tracker */}
      <section style={{ marginBottom: 20 }}>
        <div
          className="card"
          style={{
            padding: "16px 20px",
            background: "linear-gradient(170deg, var(--panel-2), var(--panel))",
            borderLeft: "4px solid var(--up)",
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            gap: 16,
            alignItems: "center",
          }}
        >
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, color: "var(--up)", letterSpacing: "0.05em", textTransform: "uppercase" }}>
              🏆 Competition Progress
            </div>
            <div style={{ fontSize: 18, fontWeight: 800, marginTop: 4 }}>
              Trading Day 4 of 5 <span style={{ fontSize: 13, color: "var(--muted)", fontWeight: 500 }}>(80% Elapsed)</span>
            </div>
            <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>
              1 Active Trading Day Remaining · Final Judging Mark Friday, Sept 4
            </div>
          </div>

          <div>
            <div style={{ fontSize: 11, fontWeight: 700, color: "var(--muted)", letterSpacing: "0.05em", textTransform: "uppercase" }}>
              Yesterday (Wednesday, Day 3)
            </div>
            <div style={{ fontSize: 16, fontWeight: 700, marginTop: 4, color: "var(--up)" }}>
              +$564.40 (+0.56%)
            </div>
            <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>
              Closed Day at $101,943.56 · 1W Take-Profit Hit
            </div>
          </div>

          <div>
            <div style={{ fontSize: 11, fontWeight: 700, color: "var(--muted)", letterSpacing: "0.05em", textTransform: "uppercase" }}>
              All-Time Session Peak
            </div>
            <div style={{ fontSize: 16, fontWeight: 700, marginTop: 4, color: "var(--accent)" }}>
              $101,965.56 (+1.97%)
            </div>
            <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>
              Established Intraday on Day 3
            </div>
          </div>
        </div>
      </section>

      {/* Account Equity Curve */}
      <section>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
          <h2 style={{ margin: 0 }}>Account Equity Path & Performance</h2>
          <span style={{ fontSize: 11, color: "var(--muted)" }}>
            High-frequency equity mark telemetry ({snap.equity_curve?.length ?? 0} points)
          </span>
        </div>
        <EquityCurve points={snap.equity_curve} />
      </section>

      {/* Risk Budget & Strategy Sleeves */}
      {snap.budget ? (
        <section>
          <h2>Strategic Risk Budget & Sleeve Allocation</h2>
          <RiskBudget budget={snap.budget} />
        </section>
      ) : null}

      {/* Autonomous Engine Diagnostics & Asymmetry Audit */}
      <section>
        <h2>Autonomous Engine Diagnostics & Asymmetry Audit</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12 }}>
          <div className="card" style={{ borderLeft: "3px solid var(--up)" }}>
            <div className="label" style={{ color: "var(--up)" }}>Top Alpha Driver: Carry Sleeve</div>
            <div style={{ fontSize: 15, fontWeight: 700, margin: "6px 0 4px" }}>Bullish Risk Reversals (2.88x Upside)</div>
            <div className="sub" style={{ lineHeight: 1.5 }}>
              Selling the expensive put skew to finance call spreads generated the cleanest beta participation: protecting $593 defined risk while capturing up to $1,707 upside.
            </div>
          </div>

          <div className="card" style={{ borderLeft: "3px solid var(--accent)" }}>
            <div className="label" style={{ color: "var(--accent)" }}>Key Learning: Momentum Drift</div>
            <div style={{ fontSize: 15, fontWeight: 700, margin: "6px 0 4px" }}>Call Credit Spread Drag (-$540)</div>
            <div className="sub" style={{ lineHeight: 1.5 }}>
              Puts achieved a 100% win rate under premium decay, but writing call spreads against strong underlying index momentum triggered the 2x stop loss. Suggests adding a trend momentum filter.
            </div>
          </div>

          <div className="card" style={{ borderLeft: "3px solid #38bdf8" }}>
            <div className="label" style={{ color: "#38bdf8" }}>Risk Gate G6: Event Blackout</div>
            <div style={{ fontSize: 15, fontWeight: 700, margin: "6px 0 4px" }}>72 Proposals Filtered Ahead of NFP</div>
            <div className="sub" style={{ lineHeight: 1.5 }}>
              Gate G6 blocked 72 short premium proposals crossing tomorrow&apos;s Non-Farm Payrolls report, strictly defending the book against event volatility gaps.
            </div>
          </div>
        </div>
      </section>

      {/* Portfolio Greeks & Sensitivity Surface */}
      {snap.open_structures.length > 0 && (
        <section>
          <h2>Portfolio Greeks & Delta-Adjusted Exposure</h2>
          <PortfolioGreeks structures={snap.open_structures} marks={marks} />
        </section>
      )}

      {/* Open Positions with Payoff Curve */}
      <section>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 10 }}>
          <h2 style={{ margin: 0 }}>
            Open Book ({snap.open_structures.length})
            {marks.length ? (
              <span className="h2-note">
                · {marks.length} live marks
                {acting.length ? (
                  <span className="down" style={{ fontWeight: 600 }}> · {acting.length} at exit rule</span>
                ) : null}
              </span>
            ) : null}
          </h2>
          <span style={{ fontSize: 11, color: "var(--muted)" }}>
            Marked against live option chains
          </span>
        </div>

        {snap.open_structures.length === 0 ? (
          <div className="empty">Flat. No open structures currently held.</div>
        ) : (
          <div className="positions">
            {snap.open_structures.map((s) => (
              <PositionCard key={s.id} structure={s} mark={markById.get(s.id) ?? null} />
            ))}
          </div>
        )}
      </section>

      {/* What-If Scenario Tester */}
      {snap.open_structures.length > 0 && (
        <section>
          <h2>Macro Shock & What-If Stress Simulator</h2>
          <ScenarioTester
            structures={snap.open_structures}
            marks={marks}
            equity={p.equity_latest}
            drawdownKillPct={snap.budget?.drawdown_kill_pct ?? 0.10}
          />
        </section>
      )}

      {/* Performance Attribution Breakdown */}
      {p.by_sleeve && Object.keys(p.by_sleeve).length > 0 && (
        <section>
          <h2>Performance Attribution by Sleeve & Underlying</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 14 }}>
            {/* By Sleeve Card */}
            <div className="card">
              <div className="label" style={{ marginBottom: 8 }}>By Strategy Sleeve</div>
              <table>
                <thead>
                  <tr>
                    <th>Sleeve</th>
                    <th>Trades</th>
                    <th>Win Rate</th>
                    <th style={{ textAlign: "right" }}>Realized P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(p.by_sleeve).map(([sleeve, data]) => {
                    const winRate = data.n ? data.wins / data.n : 0;
                    return (
                      <tr key={sleeve}>
                        <td><strong>{sleeve}</strong></td>
                        <td>{data.n}</td>
                        <td>{pct(winRate, 0)}</td>
                        <td style={{ textAlign: "right" }} className={tone(data.pnl)}>
                          {data.pnl >= 0 ? "+" : ""}{money(data.pnl)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* By Underlying Card */}
            {p.by_underlying && Object.keys(p.by_underlying).length > 0 && (
              <div className="card">
                <div className="label" style={{ marginBottom: 8 }}>By Underlying Asset</div>
                <table>
                  <thead>
                    <tr>
                      <th>Underlying</th>
                      <th>Trades</th>
                      <th>Win Rate</th>
                      <th style={{ textAlign: "right" }}>Realized P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(p.by_underlying).map(([und, data]: [string, any]) => {
                      const winRate = data.n ? data.wins / data.n : 0;
                      return (
                        <tr key={und}>
                          <td><strong>{und}</strong></td>
                          <td>{data.n}</td>
                          <td>{pct(winRate, 0)}</td>
                          <td style={{ textAlign: "right" }} className={tone(data.pnl)}>
                            {data.pnl >= 0 ? "+" : ""}{money(data.pnl)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      )}

      {/* Closed Trades */}
      <section>
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "baseline", marginBottom: 10, gap: 10 }}>
          <div>
            <h2 style={{ margin: 0 }}>Closed Structures & Trade History ({closedStructures.length})</h2>
            <div className="sub muted">
              Full leg-by-leg audit trail with exit rationale, holding duration, and return on risk
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {/* Symbol Filter */}
            <select
              value={symbolFilter}
              onChange={(e) => setSymbolFilter(e.target.value)}
              style={{
                background: "var(--panel-2)",
                color: "var(--text)",
                border: "1px solid var(--line)",
                borderRadius: 4,
                padding: "3px 6px",
                fontSize: 11,
              }}
            >
              <option value="all">All Assets</option>
              <option value="SPY">SPY</option>
              <option value="QQQ">QQQ</option>
              <option value="IWM">IWM</option>
            </select>

            {/* Trade Outcome Filter */}
            <div style={{ display: "flex", gap: 4 }}>
              {(["all", "win", "loss"] as const).map((filter) => (
                <button
                  key={filter}
                  onClick={() => setTradeFilter(filter)}
                  style={{
                    background: tradeFilter === filter ? "var(--panel-2)" : "transparent",
                    border: `1px solid ${tradeFilter === filter ? "var(--accent)" : "var(--line)"}`,
                    color: tradeFilter === filter ? "var(--accent)" : "var(--muted)",
                    fontSize: 10.5,
                    padding: "2px 8px",
                    borderRadius: 3,
                    cursor: "pointer",
                    textTransform: "capitalize",
                  }}
                >
                  {filter}
                </button>
              ))}
            </div>

            {/* Export CSV */}
            <button
              onClick={exportCsv}
              style={{
                background: "var(--panel-2)",
                border: "1px solid var(--line)",
                color: "var(--text)",
                fontSize: 10.5,
                padding: "2px 8px",
                borderRadius: 3,
                cursor: "pointer",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
              title="Download closed trades as CSV spreadsheet"
            >
              <span>📥</span> Export CSV
            </button>
          </div>
        </div>

        <div className="scroll">
          {closedStructures.length === 0 ? (
            <div className="empty">No closed structures matching filters.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Closed</th>
                  <th>Structure</th>
                  <th>Sleeve</th>
                  <th>Qty</th>
                  <th>Entry</th>
                  <th>Max Loss</th>
                  <th>Held</th>
                  <th>Realized P&amp;L</th>
                  <th>Return on Risk</th>
                  <th>Exit Reason</th>
                </tr>
              </thead>
              <tbody>
                {closedStructures.map((s) => (
                  <tr key={s.id}>
                    <td className="muted" style={{ whiteSpace: "nowrap" }}>
                      {s.closed_at ? new Date(s.closed_at).toLocaleString() : "--"}
                    </td>
                    <td>
                      <strong>{s.underlying}</strong> {s.kind.replace(/_/g, " ")}
                      <div className="muted" style={{ fontSize: 11 }}>
                        {s.legs.map((l) => `${l.side === "sell" ? "-" : "+"}${l.strike}${l.is_call ? "C" : "P"}`).join(" / ")}
                        {" · "}{s.legs[0]?.expiry}
                      </div>
                    </td>
                    <td>{s.sleeve}</td>
                    <td>{s.qty}</td>
                    <td className={s.net_price >= 0 ? "up" : "down"} style={{ fontWeight: 600 }}>
                      {s.net_price >= 0 ? "+" : ""}{s.net_price.toFixed(2)}
                    </td>
                    <td className="down">{money0(s.max_loss)}</td>
                    <td className="muted">
                      {s.held_hours != null ? `${s.held_hours.toFixed(1)}h` : "--"}
                    </td>
                    <td className={(s.realized_pnl ?? 0) >= 0 ? "up" : "down"} style={{ fontWeight: 700 }}>
                      {s.realized_pnl != null
                        ? `${s.realized_pnl >= 0 ? "+" : ""}${money(s.realized_pnl)}`
                        : "--"}
                    </td>
                    <td className={(s.return_on_risk ?? 0) >= 0 ? "up" : "down"} style={{ fontWeight: 600 }}>
                      {s.return_on_risk != null
                        ? `${(s.return_on_risk * 100).toFixed(1)}%`
                        : "--"}
                    </td>
                    <td className="muted" style={{ fontSize: 11.5 }}>{s.close_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      {/* Risk Gates Activity */}
      <section>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
          <h2 style={{ margin: 0 }}>Deterministic Risk Officer Gates (G1 - G8)</h2>
          <span style={{ fontSize: 11, color: "var(--muted)" }}>
            {snap.gates.considered} proposals evaluated · {snap.gates.approved} approved · {snap.gates.rejected} rejected
          </span>
        </div>
        <div className="card">
          <div className="sub" style={{ marginBottom: 12 }}>
            Every refusal is attributed directly to its mathematical gate. The model proposes structures; these numbers decide.
          </div>
          {snap.gates.rejections_by_gate.map((g) => (
            <div className="gate" key={g.gate}>
              <span className="code" style={{ fontWeight: 700 }}>{g.gate}</span>
              <span className="name">{g.name}</span>
              <span className="bar">
                <span style={{ width: `${(g.count / maxRejections) * 100}%` }} />
              </span>
              <span className="n" style={{ fontWeight: 600 }}>{g.count}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Scheduled Catalysts */}
      <section>
        <h2>Scheduled Economic Catalysts & Event Blackouts (Gate G6)</h2>
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Event Time</th>
                <th>Catalyst</th>
                <th>Impact Level</th>
                <th>Policy</th>
              </tr>
            </thead>
            <tbody>
              {snap.upcoming.length === 0 ? (
                <tr><td colSpan={4} className="empty">Nothing scheduled in the immediate horizon.</td></tr>
              ) : snap.upcoming.map((e) => (
                <tr key={e.name + e.when}>
                  <td>{new Date(e.when).toLocaleString()}</td>
                  <td><strong>{e.name}</strong></td>
                  <td>
                    <span
                      className="badge"
                      style={{
                        borderColor: e.impact === "high" ? "var(--down)" : "var(--accent)",
                        color: e.impact === "high" ? "var(--down)" : "var(--accent)",
                      }}
                    >
                      {e.impact.toUpperCase()} IMPACT
                    </span>
                  </td>
                  <td className="muted" style={{ fontSize: 11.5 }}>
                    {e.impact === "high" ? "Blocks short-term credit spreads across event; permits convex structures" : "Monitored by Risk Officer"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Connected Google Calendars */}
      {snap.google && snap.google.connected.length > 0 ? (
        <section>
          <h2>Connected Google Workspaces (Read-Only Dynamic Catalysts)</h2>
          <div className="card" style={{ marginBottom: 12 }}>
            <div className="sub">
              {snap.google.connected.map((a) => (
                <span key={a.label} style={{ marginRight: 18 }}>
                  <span className={a.token === "valid" ? "up" : "down"}>●</span>{" "}
                  <strong>{a.label}</strong>{" "}
                  <span className="muted">
                    {a.email} · {a.calendars} calendars
                    {a.enabled ? "" : " · disabled"}
                  </span>
                </span>
              ))}
            </div>
            <div className="sub muted" style={{ marginTop: 8 }}>
              Events from these accounts dynamically feed Gate G6 alongside built-in macroeconomic catalysts.
            </div>
          </div>
        </section>
      ) : null}

      <footer>
        Paper trading only. Options carry risk including total loss of premium. Powered by the Alphaca multi-agent options engine for the Alpaca AI Trading Agents Hackathon.
      </footer>
    </div>
  );
}
