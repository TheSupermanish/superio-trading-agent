"use client";

import { useEffect, useMemo, useState } from "react";
import Nav from "../Nav";
import type { Decision, Snapshot } from "../types";

const POLL_MS = 30_000;
const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

const AGENT_INFO: Record<string, { title: string; desc: string; icon: string; model: string }> = {
  scout: {
    title: "Scout",
    desc: "Measures tape dynamics: trend direction, 30-day realized volatility, implied volatility from ATM chains, and the volatility risk premium (IV - RV spread).",
    icon: "👁️",
    model: "Deterministic Math (NumPy/SciPy)",
  },
  analyst: {
    title: "Analyst",
    desc: "Ingests live financial news, macroeconomic releases, and market sentiment. Synthesizes session tone and geopolitical catalysts before the opening bell.",
    icon: "📰",
    model: "Gemini 3.8 Flash + Google Search",
  },
  strategist: {
    title: "Strategist",
    desc: "Autonomous tool-using reasoning agent. Analyzes live strike ladders, checks remaining risk budget, builds candidate multi-leg structures, and compares payoff profiles.",
    icon: "♟️",
    model: "Gemini 3.1 Pro Preview (Vertex AI · Global)",
  },
  risk_officer: {
    title: "Risk Officer",
    desc: "Mathematical firewall. Enforces 8 inviolable gates (G1-G8): tactical sizing <=0.75%, carry sizing <=1%, aggregate risk <=6%, no naked short options, credit floors, and calendar blackout windows.",
    icon: "🛡️",
    model: "Deterministic Gatekeeper (0 Model Calls)",
  },
  executor: {
    title: "Executor",
    desc: "Translates approved multi-leg option packages into official Alpaca CLI commands. Dispatches MLEG limit orders directly to paper accounts.",
    icon: "⚡",
    model: "Alpaca CLI (Multi-Leg Order Engine)",
  },
  manager: {
    title: "Manager",
    desc: "Continuous mark-to-market engine. Tracks every leg, applies sleeve-specific profit targets and stops, and auto-flattens at 15:00 ET on expiration day.",
    icon: "⏱️",
    model: "Live Position Monitor & Exit Arbiter",
  },
};

export default function IntelligencePage() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedAgent, setSelectedAgent] = useState<string>("all");
  const [selectedVerdict, setSelectedVerdict] = useState<string>("all");
  const [selectedSymbol, setSelectedSymbol] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [activePipelineAgent, setActivePipelineAgent] = useState<string>("strategist");

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch(`${BASE}/snapshot.json?t=${Date.now()}`, { cache: "no-store" });
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as Snapshot;
        if (alive) setSnap(data);
      } catch {
        // keep rendered
      } finally {
        if (alive) setLoading(false);
      }
    };
    load();
    const timer = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const decisions: Decision[] = snap?.recent_decisions ?? [];
  const events = snap?.recent_events ?? [];
  const orders = (snap as any)?.recent_orders ?? [];

  // Extract latest market brief
  const marketBrief = useMemo(() => {
    const briefEvent = events.find((e) => e.kind === "market_brief");
    if (briefEvent) {
      let data: any = null;
      try {
        data = typeof (briefEvent as any).data === "string" ? JSON.parse((briefEvent as any).data) : (briefEvent as any).data;
      } catch {
        data = null;
      }
      return {
        ts: briefEvent.ts,
        message: briefEvent.message,
        tone: data?.session_tone ?? "mixed",
        summary: data?.summary ?? briefEvent.message,
      };
    }
    if (snap?.session_plan?.brief) {
      return {
        ts: snap.session_plan.generated_at,
        message: snap.session_plan.brief.summary ?? snap.session_plan.summary,
        tone: snap.session_plan.brief.session_tone ?? "mixed",
        summary: snap.session_plan.brief.summary ?? snap.session_plan.summary,
      };
    }
    return null;
  }, [events, snap]);

  // Filtered decisions
  const filteredDecisions = useMemo(() => {
    return decisions.filter((d) => {
      if (selectedAgent !== "all" && d.agent !== selectedAgent) return false;
      if (selectedVerdict !== "all") {
        if (selectedVerdict === "approved" && d.verdict !== "approved" && d.verdict !== "trade" && d.verdict !== "selected") return false;
        if (selectedVerdict === "rejected" && d.verdict !== "rejected") return false;
        if (selectedVerdict === "stand_aside" && d.verdict !== "stand_aside") return false;
        if (selectedVerdict === "exit" && !["take_profit", "stop_loss", "time_exit"].includes(d.verdict)) return false;
      }
      if (selectedSymbol !== "all" && d.underlying !== selectedSymbol) return false;
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const text = `${d.agent} ${d.underlying ?? ""} ${d.verdict} ${d.reasons} ${d.proposal}`.toLowerCase();
        if (!text.includes(q)) return false;
      }
      return true;
    });
  }, [decisions, selectedAgent, selectedVerdict, selectedSymbol, searchQuery]);

  if (!snap && loading) {
    return (
      <div className="wrap">
        <Nav here="/intelligence" />
        <h1>super<span>io</span> intelligence</h1>
        <p className="empty">Loading intelligence telemetry...</p>
      </div>
    );
  }

  return (
    <div className="wrap">
      <Nav here="/intelligence" />
      <header className="top" style={{ alignItems: "center" }}>
        <div>
          <h1 style={{ fontSize: 22 }}>
            Roast Lab &amp; Multi-Agent Cognitive Engine
          </h1>
          <div className="sub" style={{ marginTop: 4 }}>
            The 6-barista execution loop: Gemini 3.1 Pro Preview reasoning, Gemini 3.8 Flash news grounding, deterministic risk gatekeeping, and Alpaca CLI execution
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <span className="badge" style={{ borderColor: "var(--accent)", color: "var(--accent)" }}>☕ Freshly Roasted Logic</span>
          <div className="sub" style={{ marginTop: 6 }}>
            {decisions.length} decisions journaled · {orders.length} orders dispatched
          </div>
        </div>
      </header>

      {/* Multi-Agent Visual Pipeline */}
      <section>
        <h2>The 6-Agent Execution Pipeline</h2>
        <div className="card" style={{ padding: 18, marginBottom: 20 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 10 }}>
            {Object.entries(AGENT_INFO).map(([key, info]) => {
              const isSelected = activePipelineAgent === key;
              return (
                <button
                  key={key}
                  onClick={() => setActivePipelineAgent(key)}
                  style={{
                    background: isSelected ? "var(--panel-2)" : "var(--panel)",
                    border: `1px solid ${isSelected ? "var(--accent)" : "var(--line)"}`,
                    borderRadius: 6,
                    padding: "12px 14px",
                    textAlign: "left",
                    cursor: "pointer",
                    color: "inherit",
                    transition: "all 0.15s ease",
                  }}
                >
                  <div style={{ fontSize: 22, marginBottom: 4 }}>{info.icon}</div>
                  <div style={{ fontWeight: 700, fontSize: 14, color: isSelected ? "var(--accent)" : "var(--text)" }}>
                    {info.title}
                  </div>
                  <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>{info.model}</div>
                </button>
              );
            })}
          </div>

          <div
            style={{
              background: "rgba(10, 12, 16, 0.6)",
              border: "1px solid var(--line)",
              borderRadius: 6,
              padding: "14px 18px",
              marginTop: 14,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6 }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: "var(--accent)" }}>
                {AGENT_INFO[activePipelineAgent].icon} {AGENT_INFO[activePipelineAgent].title} — Role & Architectural Boundary
              </span>
              <span style={{ fontSize: 10.5, color: "var(--muted)" }}>
                Engine Tier: <strong>{AGENT_INFO[activePipelineAgent].model}</strong>
              </span>
            </div>
            <p style={{ margin: 0, fontSize: 12.5, color: "var(--text)", lineHeight: 1.6 }}>
              {AGENT_INFO[activePipelineAgent].desc}
            </p>
          </div>
        </div>
      </section>

      {/* Market Brief Card from Analyst */}
      {marketBrief && (
        <section>
          <h2>Live Macro Intelligence Brief (Analyst · Gemini 3.8 Flash + Google Search)</h2>
          <div
            className="card"
            style={{
              borderLeft: "4px solid var(--accent)",
              padding: "16px 20px",
              marginBottom: 24,
              background: "linear-gradient(170deg, var(--panel-2), var(--panel))",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--muted)" }}>
                  SESSION READ
                </span>
                <span
                  className="badge"
                  style={{
                    borderColor: marketBrief.tone === "bullish" ? "var(--up)" : marketBrief.tone === "bearish" ? "var(--down)" : "var(--accent)",
                    color: marketBrief.tone === "bullish" ? "var(--up)" : marketBrief.tone === "bearish" ? "var(--down)" : "var(--accent)",
                  }}
                >
                  {marketBrief.tone.toUpperCase()} TONE
                </span>
              </div>
              <span style={{ fontSize: 11, color: "var(--muted)" }}>
                {new Date(marketBrief.ts).toLocaleString()}
              </span>
            </div>
            <div style={{ fontSize: 13.5, lineHeight: 1.65, color: "var(--text)" }}>
              {marketBrief.summary}
            </div>
          </div>
        </section>
      )}

      {/* Premarket Volatility Regimes */}
      {snap?.session_plan?.regimes && (
        <section>
          <h2>Tape Regimes & Volatility Premium (Scout Analysis)</h2>
          <div className="scroll" style={{ marginBottom: 24 }}>
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Spot</th>
                  <th>Trend / Bias</th>
                  <th>Realized Vol (30D)</th>
                  <th>ATM Implied Vol</th>
                  <th>Vol Premium (IV - RV)</th>
                  <th>Regime Edge</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(snap.session_plan.regimes).map(([sym, r]) => {
                  const premium = r.vol_premium ?? 0;
                  const isPaid = premium > 0;
                  return (
                    <tr key={sym}>
                      <td><strong style={{ fontSize: 14 }}>{sym}</strong></td>
                      <td>${r.spot.toFixed(2)}</td>
                      <td>
                        <span className="badge" style={{ fontSize: 10 }}>{r.trend.toUpperCase()} / {r.bias.toUpperCase()}</span>
                      </td>
                      <td>{r.realized_vol != null ? `${(r.realized_vol * 100).toFixed(1)}%` : "--"}</td>
                      <td>{r.atm_iv != null ? `${(r.atm_iv * 100).toFixed(1)}%` : "--"}</td>
                      <td className={isPaid ? "up" : "down"} style={{ fontWeight: 600 }}>
                        {premium > 0 ? "+" : ""}{(premium * 100).toFixed(2)}%
                      </td>
                      <td style={{ fontSize: 11.5, color: "var(--muted)" }}>
                        {isPaid ? "⚡ Implied > Realized: Core Sleeve (Selling Premium)" : "🎯 Implied < Realized: Convex Sleeve (Cheap Gamma)"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Interactive Decision Lab */}
      <section>
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12, gap: 10 }}>
          <h2>Decision Lab & Journal Explorer</h2>
          <span style={{ fontSize: 11, color: "var(--muted)" }}>
            Showing {filteredDecisions.length} of {decisions.length} recorded decisions
          </span>
        </div>

        {/* Filter Controls */}
        <div
          className="card"
          style={{
            padding: "12px 16px",
            marginBottom: 16,
            display: "flex",
            flexWrap: "wrap",
            gap: 12,
            alignItems: "center",
          }}
        >
          {/* Agent Filter */}
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
            <span style={{ color: "var(--muted)" }}>Agent:</span>
            <select
              value={selectedAgent}
              onChange={(e) => setSelectedAgent(e.target.value)}
              style={{
                background: "var(--panel-2)",
                color: "var(--text)",
                border: "1px solid var(--line)",
                borderRadius: 4,
                padding: "4px 8px",
                fontSize: 12,
                fontFamily: "inherit",
              }}
            >
              <option value="all">All Agents</option>
              <option value="strategist">Strategist (LLM)</option>
              <option value="risk_officer">Risk Officer (Gates)</option>
              <option value="scout">Scout (Tape)</option>
              <option value="manager">Manager (Exits)</option>
            </select>
          </div>

          {/* Verdict Filter */}
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
            <span style={{ color: "var(--muted)" }}>Verdict:</span>
            <select
              value={selectedVerdict}
              onChange={(e) => setSelectedVerdict(e.target.value)}
              style={{
                background: "var(--panel-2)",
                color: "var(--text)",
                border: "1px solid var(--line)",
                borderRadius: 4,
                padding: "4px 8px",
                fontSize: 12,
                fontFamily: "inherit",
              }}
            >
              <option value="all">All Verdicts</option>
              <option value="approved">Approved / Trade</option>
              <option value="rejected">Rejected (Risk Gate)</option>
              <option value="stand_aside">Stand Aside</option>
              <option value="exit">Exits (TP / SL)</option>
            </select>
          </div>

          {/* Symbol Filter */}
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
            <span style={{ color: "var(--muted)" }}>Symbol:</span>
            <select
              value={selectedSymbol}
              onChange={(e) => setSelectedSymbol(e.target.value)}
              style={{
                background: "var(--panel-2)",
                color: "var(--text)",
                border: "1px solid var(--line)",
                borderRadius: 4,
                padding: "4px 8px",
                fontSize: 12,
                fontFamily: "inherit",
              }}
            >
              <option value="all">All Symbols</option>
              <option value="SPY">SPY</option>
              <option value="QQQ">QQQ</option>
              <option value="IWM">IWM</option>
            </select>
          </div>

          {/* Search Box */}
          <div style={{ flex: 1, minWidth: 200 }}>
            <input
              type="text"
              placeholder="Search reasoning, gates, tool calls..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{
                width: "100%",
                background: "var(--panel-2)",
                border: "1px solid var(--line)",
                borderRadius: 4,
                padding: "5px 10px",
                color: "var(--text)",
                fontSize: 12,
                fontFamily: "inherit",
                boxSizing: "border-box",
              }}
            />
          </div>
        </div>

        {/* Decisions Table */}
        <div className="scroll" style={{ maxHeight: 520, overflowY: "auto", marginBottom: 28 }}>
          <table>
            <thead style={{ position: "sticky", top: 0, background: "var(--panel)", zIndex: 2 }}>
              <tr>
                <th style={{ width: 110 }}>Time</th>
                <th style={{ width: 110 }}>Agent</th>
                <th style={{ width: 70 }}>Symbol</th>
                <th style={{ width: 110 }}>Verdict</th>
                <th>Reasoning & Gate Trail</th>
              </tr>
            </thead>
            <tbody>
              {filteredDecisions.length === 0 ? (
                <tr>
                  <td colSpan={5} className="empty">No decisions matched the current filters.</td>
                </tr>
              ) : (
                filteredDecisions.map((d) => {
                  let reasons: string[] = [];
                  try {
                    reasons = JSON.parse(d.reasons);
                  } catch {
                    reasons = [d.reasons];
                  }
                  const isApprove = ["approved", "trade", "selected"].includes(d.verdict);
                  const isReject = d.verdict === "rejected";
                  const isExit = ["take_profit", "stop_loss", "time_stop"].includes(d.verdict);

                  return (
                    <tr key={d.id}>
                      <td className="muted" style={{ whiteSpace: "nowrap" }}>
                        {new Date(d.ts).toLocaleTimeString()}
                      </td>
                      <td>
                        <strong style={{ color: d.agent === "risk_officer" ? "#38bdf8" : d.agent === "strategist" ? "var(--accent)" : "inherit" }}>
                          {d.agent}
                        </strong>
                      </td>
                      <td>
                        {d.underlying ? <strong>{d.underlying}</strong> : <span className="muted">--</span>}
                      </td>
                      <td>
                        <span
                          className={`verdict ${isApprove ? "approved" : isReject ? "rejected" : ""}`}
                          style={{
                            borderColor: isExit ? "var(--up)" : undefined,
                            color: isExit ? "var(--up)" : undefined,
                          }}
                        >
                          {d.verdict}
                        </span>
                      </td>
                      <td style={{ fontSize: 12, lineHeight: 1.5 }}>
                        {reasons.join(" · ")}
                        {d.proposal && d.proposal !== "{}" && (
                          <details style={{ marginTop: 4 }}>
                            <summary style={{ fontSize: 10.5, color: "var(--muted)", cursor: "pointer" }}>
                              View raw proposal JSON
                            </summary>
                            <pre style={{ margin: "4px 0 0", fontSize: 10.5, color: "var(--muted)", whiteSpace: "pre-wrap" }}>
                              {d.proposal}
                            </pre>
                          </details>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* Alpaca Execution Audit Log */}
      {orders.length > 0 && (
        <section>
          <h2>Alpaca CLI Execution Audit (Multi-Leg Orders)</h2>
          <div className="sub muted" style={{ marginBottom: 10 }}>
            Every multi-leg order sent by the Executor through the official Alpaca CLI with broker fill prices and client order IDs.
          </div>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Intent</th>
                  <th>Structure ID</th>
                  <th>Fill Price</th>
                  <th>Status</th>
                  <th>Client Order ID</th>
                  <th>Broker Order ID</th>
                </tr>
              </thead>
              <tbody>
                {orders.slice(0, 30).map((o: any) => {
                  const isFilled = o.status === "filled";
                  const isRejected = o.status === "rejected";
                  return (
                    <tr key={o.id}>
                      <td className="muted">{new Date(o.ts).toLocaleTimeString()}</td>
                      <td>
                        <span className="badge" style={{ fontSize: 10 }}>{o.intent}</span>
                      </td>
                      <td>#{o.structure_id}</td>
                      <td className={o.fill_price ? "up" : "muted"} style={{ fontWeight: 600 }}>
                        {o.fill_price != null ? `$${Number(o.fill_price).toFixed(2)}` : "--"}
                      </td>
                      <td>
                        <span className={`verdict ${isFilled ? "approved" : isRejected ? "rejected" : ""}`}>
                          {o.status}
                        </span>
                      </td>
                      <td className="muted" style={{ fontSize: 11 }}>{o.client_order_id ?? "--"}</td>
                      <td className="muted" style={{ fontSize: 11 }}>{o.broker_order_id ? `${o.broker_order_id.slice(0, 12)}...` : "--"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <footer>
        Paper trading only. Hypothetical results, not investment advice. Options carry risk including total loss of premium.
      </footer>
    </div>
  );
}
