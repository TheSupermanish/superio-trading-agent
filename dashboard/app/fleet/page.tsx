"use client";

import Nav from "../Nav";
import { useCallback, useEffect, useState } from "react";
import type { AgentDetail, AgentSummary, Structure } from "./types";
import { GATE_NAMES } from "./types";
import PayoffChart from "../PayoffChart";

const POLL_MS = 30_000;
const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

const money = (n: number) =>
  `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const money2 = (n: number) =>
  `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pct = (n: number | null | undefined, digits = 2) =>
  n === null || n === undefined ? "--" : `${(n * 100).toFixed(digits)}%`;
const tone = (n: number | null | undefined) =>
  n === null || n === undefined || n === 0 ? "" : n > 0 ? "up" : "down";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${path}${path.includes("?") ? "&" : "?"}t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(String(res.status));
  return (await res.json()) as T;
}

function legLine(s: Structure) {
  return s.legs
    .map((l) => `${l.side === "sell" ? "-" : "+"}${l.strike}${l.is_call ? "C" : "P"}`)
    .join(" / ");
}

function AgentCard({ agent, selected, onSelect }: {
  agent: AgentSummary; selected: boolean; onSelect: () => void;
}) {
  const p = agent.performance;
  const ret = p?.return_pct ?? null;
  return (
    <button className={`agent ${selected ? "on" : ""}`} onClick={onSelect}>
      <div className="agent-top">
        <span className="agent-name" style={{ fontWeight: 700 }}>{agent.label}</span>
        <span className={`badge ${agent.live ? "live" : "dry"}`}>
          {agent.live ? "live paper" : "diary shadow"}
        </span>
      </div>
      <div className="agent-variant">{agent.variant.replace(/_/g, " ")}</div>
      <div className={`agent-ret ${tone(ret)}`} style={{ fontWeight: 700 }}>
        {ret === null ? "--" : `${ret >= 0 ? "+" : ""}${pct(ret)}`}
      </div>
      <div className="agent-eq">{p ? money2(p.equity_latest) : agent.status}</div>
      <div className="agent-foot">
        <span>{p ? `${p.trades_closed} closed` : "no trades"}</span>
        <span>{agent.open_structures ?? 0} open</span>
        <span>{money(agent.open_risk ?? 0)} risk</span>
      </div>
    </button>
  );
}

function Kpi({ label, value, note, klass }: {
  label: string; value: string; note?: string; klass?: string;
}) {
  return (
    <div className="card">
      <div className="label">{label}</div>
      <div className={`value ${klass ?? ""}`}>{value}</div>
      {note ? <div className="note">{note}</div> : null}
    </div>
  );
}

// Fallback loader if API is not running or on static host
async function fallbackLoad(id: string): Promise<{ agents: AgentSummary[]; detail: AgentDetail }> {
  const snapshotFiles = [
    { id: "main", label: "Main", variant: "barbell", live: true, file: "snapshot.json", stake: 100000 },
    { id: "test2", label: "Test 2", variant: "convex_tilt", live: true, file: "snapshot-test2.json", stake: 100000 },
    { id: "test3", label: "Test 3", variant: "income_only", live: true, file: "snapshot-test3.json", stake: 100000 },
    { id: "levered", label: "Levered", variant: "levered", live: false, file: "snapshot-diary-levered.json", stake: 50000 },
    { id: "vrp_router", label: "VRP router", variant: "vrp_router", live: false, file: "snapshot-diary-vrp_router.json", stake: 50000 },
    { id: "fat_credit", label: "Fat credit", variant: "fat_credit", live: false, file: "snapshot-diary-fat_credit.json", stake: 50000 },
    { id: "long_gamma", label: "Long gamma", variant: "long_gamma", live: false, file: "snapshot-diary-long_gamma.json", stake: 50000 },
  ];

  const agentSummaries: AgentSummary[] = [];
  let currentDetail: AgentDetail | null = null;

  for (const cfg of snapshotFiles) {
    try {
      const snap = await getJson<any>(`${BASE}/${cfg.file}`);
      const summary: AgentSummary = {
        id: cfg.id,
        label: cfg.label,
        variant: cfg.variant,
        live: cfg.live,
        stake: cfg.stake,
        status: "running",
        performance: snap.performance,
        open_structures: (snap.open_structures || []).length,
        open_risk: snap.open_risk || 0,
        gates: snap.gates,
        dry_run: snap.dry_run,
        diary: snap.diary,
        generated_at: snap.generated_at,
        age_seconds: 10,
      };
      agentSummaries.push(summary);

      if (cfg.id === id) {
        currentDetail = {
          ...summary,
          open: snap.open_structures || [],
          closed: snap.closed_structures || [],
          equity_curve: snap.equity_curve || [],
          decisions: snap.recent_decisions || [],
          events: snap.recent_events || [],
          orders: snap.recent_orders || [],
          session_plan: snap.session_plan,
          upcoming: snap.upcoming || [],
        };
      }
    } catch {
      agentSummaries.push({
        id: cfg.id,
        label: cfg.label,
        variant: cfg.variant,
        live: cfg.live,
        stake: cfg.stake,
        status: "not published yet",
        performance: null,
      });
    }
  }

  if (!currentDetail) {
    const firstActive = agentSummaries.find((a) => a.performance !== null);
    if (firstActive) {
      return fallbackLoad(firstActive.id);
    }
    throw new Error("No active agent snapshots found");
  }

  return { agents: agentSummaries, detail: currentDetail };
}

export default function Fleet() {
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [selected, setSelected] = useState<string>("main");
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updated, setUpdated] = useState<string>("");

  const refresh = useCallback(async (id: string) => {
    try {
      // First attempt API
      try {
        const [list, one] = await Promise.all([
          getJson<{ agents: AgentSummary[] }>("/api/agents"),
          getJson<AgentDetail>(`/api/agents/${id}`),
        ]);
        setAgents(list.agents);
        setDetail(one);
        setUpdated(new Date().toLocaleTimeString());
        setError(null);
        return;
      } catch (apiErr) {
        // Fallback to static snapshot files
        const fallback = await fallbackLoad(id);
        setAgents(fallback.agents);
        setDetail(fallback.detail);
        setUpdated(new Date().toLocaleTimeString());
        setError(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "unreachable");
    }
  }, []);

  useEffect(() => {
    refresh(selected);
    const timer = setInterval(() => refresh(selected), POLL_MS);
    return () => clearInterval(timer);
  }, [selected, refresh]);

  if (!agents) {
    return (
      <div className="wrap">
        <Nav here="/fleet" />
        <header className="top">
          <h1>super<span>io</span> fleet</h1>
        </header>
        <div className="empty">{error ? `Fleet telemetry unreachable (${error})` : "Loading the fleet tournament..."}</div>
      </div>
    );
  }

  const live = agents.filter((a) => a.live && a.performance !== null);
  const totalEquity = live.reduce((sum, a) => sum + (a.performance?.equity_latest ?? a.stake), 0);
  const totalStake = live.reduce((sum, a) => sum + a.stake, 0);
  const totalPnl = totalEquity - totalStake;
  const totalRisk = agents.reduce((sum, a) => sum + (a.live ? a.open_risk ?? 0 : 0), 0);
  const totalClosed = agents.reduce((sum, a) => sum + (a.performance?.trades_closed ?? 0), 0);
  const p = detail?.performance ?? null;

  return (
    <div className="wrap">
      <Nav here="/fleet" />
      <header className="top" style={{ alignItems: "center" }}>
        <div>
          <h1 style={{ fontSize: 22 }}>
            Strategy Tournament &amp; Barista Fleet
          </h1>
          <div className="sub" style={{ marginTop: 4 }}>
            Seven strategy variants, one barista engine. Three running on dedicated Alpaca paper accounts, four shadow diary presets.
          </div>
        </div>
        <div className="sub" style={{ textAlign: "right" }}>
          updated {updated}
          {detail?.age_seconds != null ? (
            <span className="muted"> · snapshot {Math.round(detail.age_seconds)}s old</span>
          ) : null}
        </div>
      </header>

      {/* Aggregate Fleet KPIs */}
      <div className="grid kpis">
        <Kpi label="Combined Live Equity" value={money2(totalEquity)} note={`across ${live.length} live accounts`} />
        <Kpi
          label="Combined Net P&L"
          value={`${totalPnl >= 0 ? "+" : ""}${money2(totalPnl)}`}
          klass={tone(totalPnl)}
          note={pct(totalStake ? totalPnl / totalStake : 0)}
        />
        <Kpi label="Total Open Risk" value={money(totalRisk)} note={pct(totalStake ? totalRisk / totalStake : 0)} />
        <Kpi label="Total Closed Trades" value={String(totalClosed)} note="all active variants" />
      </div>

      {/* Strategy Tournament Leaderboard */}
      <section>
        <h2>Strategy Tournament Leaderboard</h2>
        <div className="scroll" style={{ marginBottom: 24 }}>
          <table>
            <thead>
              <tr>
                <th>Rank</th>
                <th>Strategy Variant</th>
                <th>Execution Mode</th>
                <th>Current Equity</th>
                <th>Return %</th>
                <th>Win Rate</th>
                <th>Profit Factor</th>
                <th>Max DD</th>
                <th>Open Risk</th>
                <th>Closed</th>
              </tr>
            </thead>
            <tbody>
              {agents
                .slice()
                .sort((a, b) => (b.performance?.return_pct ?? -999) - (a.performance?.return_pct ?? -999))
                .map((a, idx) => {
                  const perf = a.performance;
                  const ret = perf?.return_pct ?? null;
                  const isSelected = a.id === selected;
                  return (
                    <tr
                      key={a.id}
                      className={`clickable ${isSelected ? "on" : ""}`}
                      onClick={() => setSelected(a.id)}
                    >
                      <td>
                        <strong style={{ color: idx === 0 && ret && ret > 0 ? "var(--accent)" : "inherit" }}>
                          #{idx + 1}
                        </strong>
                      </td>
                      <td>
                        <strong>{a.label}</strong>{" "}
                        <span className="muted">({a.variant.replace(/_/g, " ")})</span>
                      </td>
                      <td>
                        <span className={`badge ${a.live ? "live" : "dry"}`}>
                          {a.live ? "live paper" : "diary"}
                        </span>
                      </td>
                      <td style={{ fontWeight: 600 }}>
                        {perf ? money2(perf.equity_latest) : <span className="muted">--</span>}
                      </td>
                      <td className={tone(ret)} style={{ fontWeight: 700 }}>
                        {ret !== null ? `${ret >= 0 ? "+" : ""}${pct(ret)}` : "--"}
                      </td>
                      <td>
                        {perf?.win_rate != null ? pct(perf.win_rate, 1) : "--"}
                      </td>
                      <td>
                        {perf?.profit_factor != null ? perf.profit_factor.toFixed(2) : "--"}
                      </td>
                      <td className="muted">
                        {perf?.max_drawdown_pct != null ? pct(perf.max_drawdown_pct) : "--"}
                      </td>
                      <td>
                        {money(a.open_risk ?? 0)}
                      </td>
                      <td>
                        {perf ? perf.trades_closed : "--"}
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
      </section>

      {/* The Fleet Quick Selector */}
      <section>
        <h2>Select Strategy Variant</h2>
        <div className="agents">
          {agents.map((a) => (
            <AgentCard
              key={a.id}
              agent={a}
              selected={a.id === selected}
              onSelect={() => setSelected(a.id)}
            />
          ))}
        </div>
      </section>

      {/* Selected Agent Detail Deck */}
      {detail && (
        <section style={{ marginTop: 28 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
            <h2>
              {detail.label} ({detail.variant.replace(/_/g, " ")}) · Deep Dive
            </h2>
            <span className={`badge ${detail.live ? "live" : "dry"}`}>
              {detail.live ? "Alpaca Paper Account" : "Shadow Diary"}
            </span>
          </div>

          <div className="grid kpis">
            <Kpi label="Account Equity" value={p ? money2(p.equity_latest) : "--"} note={`started ${money(detail.stake)}`} />
            <Kpi
              label="Net Return"
              value={p?.return_pct != null ? `${p.return_pct >= 0 ? "+" : ""}${pct(p.return_pct)}` : "--"}
              klass={tone(p?.return_pct)}
              note={p ? `${(p.total_pnl ?? 0) >= 0 ? "+" : ""}${money2(p.total_pnl ?? 0)}` : undefined}
            />
            <Kpi label="Win Rate" value={p?.win_rate != null ? pct(p.win_rate, 1) : "--"} note={p ? `${p.wins}W / ${p.losses}L` : undefined} />
            <Kpi label="Profit Factor" value={p?.profit_factor != null ? p.profit_factor.toFixed(2) : "--"} note="gross win / gross loss" />
            <Kpi label="Max Drawdown" value={p?.max_drawdown_pct != null ? pct(p.max_drawdown_pct) : "--"} note="stands down at 10%" />
          </div>

          {/* Open Structures */}
          <div style={{ marginTop: 20 }}>
            <h3>Active Open Positions ({detail.open.length})</h3>
            {detail.open.length === 0 ? (
              <div className="empty">Flat. No open structures on this book.</div>
            ) : (
              <div className="positions" style={{ marginBottom: 20 }}>
                {detail.open.map((s) => (
                  <div key={s.id} className="pos">
                    <div className="pos-head">
                      <div>
                        <span className="pos-sym" style={{ fontWeight: 700 }}>{s.underlying}</span>
                        <span className="pos-kind">{s.kind.replace(/_/g, " ")}</span>
                        <span className="badge live">{s.sleeve}</span>
                      </div>
                      <div className="pos-pnl">
                        <span style={{ fontSize: 13, color: "var(--muted)" }}>x{s.qty}</span>
                      </div>
                    </div>
                    <div className="pos-legs">
                      <strong>{legLine(s)}</strong> · {s.legs[0]?.expiry}
                    </div>
                    <div className="pos-stats">
                      <div>
                        <div className="label">Risked</div>
                        <div className="down" style={{ fontWeight: 600 }}>{money(s.max_loss)}</div>
                      </div>
                      <div>
                        <div className="label">Can win</div>
                        <div className="up" style={{ fontWeight: 600 }}>{money(s.max_gain)}</div>
                      </div>
                      <div>
                        <div className="label">Net Price</div>
                        <div className={s.net_price >= 0 ? "up" : "down"} style={{ fontWeight: 600 }}>
                          {s.net_price >= 0 ? "+" : ""}{s.net_price.toFixed(2)}
                        </div>
                      </div>
                      <div>
                        <div className="label">Status</div>
                        <div style={{ fontWeight: 600 }}>{s.status}</div>
                      </div>
                    </div>
                    <PayoffChart
                      legs={s.legs as any}
                      netPrice={s.net_price}
                      qty={s.qty}
                      height={105}
                      compact
                    />
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Closed Trades */}
          <div style={{ marginTop: 24 }}>
            <h3>Closed Trades History</h3>
            <div className="scroll">
              {detail.closed.length === 0 ? (
                <div className="empty">No closed trades yet for this profile.</div>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>Closed</th>
                      <th>Structure</th>
                      <th>Qty</th>
                      <th>Max Loss</th>
                      <th>Held</th>
                      <th>Realized</th>
                      <th>On Risk</th>
                      <th>Exit Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.closed.map((c) => (
                      <tr key={c.id}>
                        <td className="muted">{c.closed_at ? new Date(c.closed_at).toLocaleString() : "--"}</td>
                        <td>
                          <strong>{c.underlying}</strong> {c.kind.replace(/_/g, " ")}
                          <div className="muted">{legLine(c)}</div>
                        </td>
                        <td>{c.qty}</td>
                        <td className="down">{money(c.max_loss)}</td>
                        <td className="muted">{c.held_hours != null ? `${c.held_hours.toFixed(1)}h` : "--"}</td>
                        <td className={tone(c.realized_pnl)} style={{ fontWeight: 600 }}>
                          {c.realized_pnl != null ? `${c.realized_pnl >= 0 ? "+" : ""}${money(c.realized_pnl)}` : "--"}
                        </td>
                        <td className={tone(c.return_on_risk)} style={{ fontWeight: 600 }}>
                          {c.return_on_risk != null ? `${(c.return_on_risk * 100).toFixed(0)}%` : "--"}
                        </td>
                        <td className="muted">{c.close_reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </section>
      )}

      <footer>
        Paper trading only. Hypothetical results, not investment advice.
      </footer>
    </div>
  );
}
