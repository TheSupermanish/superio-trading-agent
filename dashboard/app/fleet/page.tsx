"use client";

import { useCallback, useEffect, useState } from "react";
import type { AgentDetail, AgentSummary, Structure } from "./types";
import { GATE_NAMES } from "./types";

const POLL_MS = 30_000;

const money = (n: number) =>
  `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const money2 = (n: number) =>
  `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pct = (n: number | null | undefined, digits = 2) =>
  n === null || n === undefined ? "--" : `${(n * 100).toFixed(digits)}%`;
const tone = (n: number | null | undefined) =>
  n === null || n === undefined || n === 0 ? "" : n > 0 ? "up" : "down";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
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
        <span className="agent-name">{agent.label}</span>
        <span className={`badge ${agent.live ? "live" : "dry"}`}>
          {agent.live ? "live" : "diary"}
        </span>
      </div>
      <div className="agent-variant">{agent.variant.replace(/_/g, " ")}</div>
      <div className={`agent-ret ${tone(ret)}`}>{ret === null ? "--" : `${ret >= 0 ? "+" : ""}${pct(ret)}`}</div>
      <div className="agent-eq">{p ? money(p.equity_latest) : agent.status}</div>
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

export default function Fleet() {
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [selected, setSelected] = useState<string>("main");
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updated, setUpdated] = useState<string>("");

  const refresh = useCallback(async (id: string) => {
    try {
      const [list, one] = await Promise.all([
        getJson<{ agents: AgentSummary[] }>("/api/agents"),
        getJson<AgentDetail>(`/api/agents/${id}`),
      ]);
      setAgents(list.agents);
      setDetail(one);
      setUpdated(new Date().toLocaleTimeString());
      setError(null);
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
        <header className="top">
          <h1>super<span>io</span> fleet</h1>
        </header>
        <div className="empty">{error ? `API unreachable (${error})` : "Loading the fleet..."}</div>
      </div>
    );
  }

  const live = agents.filter((a) => a.live);
  const totalEquity = live.reduce((sum, a) => sum + (a.performance?.equity_latest ?? a.stake), 0);
  const totalStake = live.reduce((sum, a) => sum + a.stake, 0);
  const totalPnl = totalEquity - totalStake;
  const totalRisk = agents.reduce((sum, a) => sum + (a.live ? a.open_risk ?? 0 : 0), 0);
  const totalClosed = agents.reduce((sum, a) => sum + (a.performance?.trades_closed ?? 0), 0);
  const p = detail?.performance ?? null;
  const maxRejections = Math.max(
    1,
    ...(detail?.gates?.rejections_by_gate ?? []).map((g) => g.count),
  );

  return (
    <div className="wrap">
      <header className="top">
        <div>
          <h1>super<span>io</span> fleet</h1>
          <div className="sub">
            Seven books, one engine. Three on real paper accounts, four diary presets that
            shadow the same chain and cannot reach a broker.
          </div>
        </div>
        <div className="sub">
          {error ? (
            <span className="down">API unreachable</span>
          ) : (
            <>
              updated {updated}
              {detail?.age_seconds != null ? (
                <span className="muted"> · snapshot {Math.round(detail.age_seconds)}s old</span>
              ) : null}
            </>
          )}
        </div>
      </header>

      <div className="grid kpis">
        <Kpi label="Live equity" value={money2(totalEquity)} note={`across ${live.length} paper accounts`} />
        <Kpi
          label="Live P&L"
          value={`${totalPnl >= 0 ? "+" : ""}${money2(totalPnl)}`}
          klass={tone(totalPnl)}
          note={pct(totalPnl / totalStake)}
        />
        <Kpi label="Open risk" value={money(totalRisk)} note={pct(totalRisk / totalStake)} />
        <Kpi label="Closed trades" value={String(totalClosed)} note="all books" />
      </div>

      <section>
        <h2>The fleet</h2>
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

      {detail ? (
        <>
          <section>
            <h2>
              {detail.label} · {detail.variant.replace(/_/g, " ")} ·{" "}
              {detail.live ? "paper account" : "diary book, no broker"}
            </h2>
            <div className="grid kpis">
              <Kpi
                label="Equity"
                value={p ? money2(p.equity_latest) : "--"}
                note={p ? `from ${money(p.equity_start)}` : undefined}
              />
              <Kpi
                label="Return"
                value={p ? pct(p.return_pct) : "--"}
                klass={tone(p?.return_pct)}
              />
              <Kpi
                label="Realized"
                value={p ? money2(p.realized_pnl) : "--"}
                klass={tone(p?.realized_pnl)}
                note={p ? `${p.wins}W / ${p.losses}L` : undefined}
              />
              <Kpi
                label="Win rate"
                value={p?.win_rate != null ? pct(p.win_rate, 1) : "--"}
                note={p ? `${p.trades_closed} closed` : undefined}
              />
              <Kpi
                label="Profit factor"
                value={p?.profit_factor != null ? p.profit_factor.toFixed(2) : "--"}
              />
              <Kpi
                label="Max drawdown"
                value={p ? pct(p.max_drawdown_pct) : "--"}
              />
            </div>
          </section>

          <section>
            <h2>Open structures</h2>
            <div className="scroll">
              {detail.open.length === 0 ? (
                <div className="empty">Flat. No open structures.</div>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>Opened</th><th>Structure</th><th>Sleeve</th><th>Qty</th>
                      <th>Net</th><th>Max loss</th><th>Max gain</th><th>State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.open.map((s) => (
                      <tr key={s.id}>
                        <td className="muted">{new Date(s.opened_at).toLocaleString()}</td>
                        <td>
                          <strong>{s.underlying}</strong> {s.kind.replace(/_/g, " ")}
                          <div className="muted">{legLine(s)} · {s.legs[0]?.expiry}</div>
                        </td>
                        <td>{s.sleeve}</td>
                        <td>{s.qty}</td>
                        <td className={tone(s.net_price)}>
                          {s.net_price >= 0 ? "+" : ""}{s.net_price.toFixed(2)}
                        </td>
                        <td className="down">{money(s.max_loss)}</td>
                        <td className="up">{money(s.max_gain)}</td>
                        <td className="muted">{s.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>

          <section>
            <h2>Closed trades — where the P&amp;L came from</h2>
            <div className="scroll">
              {detail.closed.length === 0 ? (
                <div className="empty">Nothing closed yet.</div>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>Closed</th><th>Structure</th><th>Qty</th><th>Max loss</th>
                      <th>Held</th><th>Realized</th><th>On risk</th><th>Exit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.closed.map((s) => (
                      <tr key={s.id}>
                        <td className="muted">
                          {s.closed_at ? new Date(s.closed_at).toLocaleString() : "--"}
                        </td>
                        <td>
                          <strong>{s.underlying}</strong> {s.kind.replace(/_/g, " ")}
                          <div className="muted">{legLine(s)}</div>
                        </td>
                        <td>{s.qty}</td>
                        <td className="down">{money(s.max_loss)}</td>
                        <td className="muted">
                          {s.held_hours != null ? `${s.held_hours.toFixed(1)}h` : "--"}
                        </td>
                        <td className={tone(s.realized_pnl)}>
                          {s.realized_pnl != null
                            ? `${s.realized_pnl >= 0 ? "+" : ""}${money2(s.realized_pnl)}`
                            : "--"}
                        </td>
                        <td className={tone(s.return_on_risk)}>
                          {s.return_on_risk != null ? pct(s.return_on_risk, 0) : "--"}
                        </td>
                        <td className="muted">{s.close_reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>

          <section>
            <h2>Risk gates — what this agent refused to do</h2>
            <div className="card">
              <div className="sub" style={{ marginBottom: 10 }}>
                {detail.gates?.considered ?? 0} considered · {detail.gates?.approved ?? 0} approved ·{" "}
                {detail.gates?.rejected ?? 0} rejected. Every rejection names the gate that made it.
              </div>
              {(detail.gates?.rejections_by_gate ?? []).length === 0 ? (
                <div className="muted">Nothing refused yet.</div>
              ) : (
                (detail.gates?.rejections_by_gate ?? []).map((g) => (
                  <div className="gate" key={g.gate}>
                    <span className="code">{g.gate}</span>
                    <span className="name">{GATE_NAMES[g.gate] ?? ""}</span>
                    <span className="bar">
                      <span style={{ width: `${(g.count / maxRejections) * 100}%` }} />
                    </span>
                    <span className="n">{g.count}</span>
                  </div>
                ))
              )}
            </div>
          </section>

          <section>
            <h2>Recent decisions</h2>
            <div className="scroll feed">
              <table>
                <tbody>
                  {detail.decisions.slice(0, 40).map((d) => (
                    <tr key={d.id}>
                      <td className="muted" style={{ whiteSpace: "nowrap" }}>
                        {new Date(d.ts).toLocaleTimeString()}
                      </td>
                      <td>
                        <span className={`verdict ${d.verdict === "approve" ? "approved" : d.verdict === "reject" ? "rejected" : "observed"}`}>
                          {d.verdict}
                        </span>
                      </td>
                      <td>{d.underlying ?? "--"}</td>
                      <td className="muted">{d.reasons}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section>
            <h2>Events</h2>
            <div className="scroll feed">
              <table>
                <tbody>
                  {detail.events.slice(0, 30).map((e) => (
                    <tr key={e.id}>
                      <td className="muted" style={{ whiteSpace: "nowrap" }}>
                        {new Date(e.ts).toLocaleTimeString()}
                      </td>
                      <td className={e.level === "error" ? "down" : e.level === "warning" ? "" : "muted"}>
                        {e.level}
                      </td>
                      <td>{e.kind}</td>
                      <td className="muted">{e.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : null}

      <footer>
        Read-only monitor. This process makes no call to Alpaca and cannot place, cancel or
        close an order. Polls every {POLL_MS / 1000}s.
      </footer>
    </div>
  );
}
