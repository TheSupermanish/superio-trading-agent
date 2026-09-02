"use client";

import Nav from "./Nav";
import { useEffect, useState } from "react";
import EquityCurve from "./EquityCurve";
import PositionCard from "./PositionCard";
import RiskBudget from "./RiskBudget";
import type { LiveMark, Snapshot } from "./types";

const POLL_MS = 60_000;
// GitHub Pages serves a project site under a path prefix; a bare "/snapshot.json"
// would resolve to the domain root and 404.
const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

const money = (n: number) =>
  `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
const pct = (n: number | null | undefined, digits = 2) =>
  n === null || n === undefined ? "--" : `${(n * 100).toFixed(digits)}%`;

function Kpi({ label, value, note, tone }: {
  label: string; value: string; note?: string; tone?: "up" | "down";
}) {
  return (
    <div className="card">
      <div className="label">{label}</div>
      <div className={`value ${tone ?? ""}`}>{value}</div>
      {note ? <div className="note">{note}</div> : null}
    </div>
  );
}

export default function Page() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        // Cache-bust so a refreshed snapshot is picked up rather than served
        // from the CDN edge cache.
        const res = await fetch(`${BASE}/snapshot.json?t=${Date.now()}`, { cache: "no-store" });
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as Snapshot;
        if (alive) setSnap(data);
      } catch {
        /* keep whatever we already rendered */
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
  const pnlTone = p.realized_pnl >= 0 ? "up" : "down";
  const marks: LiveMark[] = snap.live?.marks ?? [];
  const markById = new Map(marks.map((m) => [m.structure_id, m]));
  const openPnl = marks.length
    ? marks.reduce((sum, m) => sum + m.unrealized_pnl, 0)
    : null;
  const acting = marks.filter((m) => m.action !== "hold");
  const maxRejections = Math.max(1, ...snap.gates.rejections_by_gate.map((g) => g.count));

  return (
    <div className="wrap">
      <Nav here="/" />
      <header className="top">
        <div>
          <h1>super<span>io</span></h1>
          <div className="sub">
            Autonomous defined-risk options agent on Alpaca paper trading
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <span className={`badge ${snap.diary ? "dry" : snap.dry_run ? "dry" : "live"}`}>
            {snap.diary ? "diary · no broker" : snap.dry_run ? "dry run" : "trading"}
          </span>{" "}
          <span className="badge">{snap.variant}</span>
          <div className="sub" style={{ marginTop: 6 }}>
            account {snap.account_id || "unset"} · updated{" "}
            {new Date(snap.generated_at).toLocaleString()}
          </div>
        </div>
      </header>

      <div className="hero">
        <div className="hero-main">
          <div className="label">Equity</div>
          <div className="hero-eq">{money(p.equity_latest)}</div>
          <div className={`hero-ret ${(p.return_pct ?? 0) >= 0 ? "up" : "down"}`}>
            {(p.return_pct ?? 0) >= 0 ? "+" : ""}{pct(p.return_pct)}
            <span className="muted"> from {money(p.equity_start)}</span>
          </div>
          {openPnl !== null ? (
            <div className="hero-open">
              <span className="muted">open positions marked at</span>{" "}
              <span className={openPnl >= 0 ? "up" : "down"}>
                {openPnl >= 0 ? "+" : ""}{money(openPnl)}
              </span>
            </div>
          ) : null}
        </div>

        <div className="grid kpis hero-kpis">
          <Kpi label="Realized P&L" value={money(p.realized_pnl)} tone={pnlTone}
               note={`${p.trades_closed} closed`} />
          <Kpi label="Win rate" value={p.win_rate === null ? "--" : pct(p.win_rate, 1)}
               note={`${p.wins}W / ${p.losses}L`} />
          <Kpi label="Profit factor" value={p.profit_factor?.toFixed(2) ?? "--"}
               note="gross win / gross loss" />
          <Kpi label="Max drawdown" value={pct(p.max_drawdown_pct)}
               note={`stands down at ${pct(snap.limits.total_drawdown_kill_pct, 0)}`} />
        </div>
      </div>

      {snap.budget ? (
        <section>
          <h2>Where the risk is, and what each sleeve pays for it</h2>
          <RiskBudget budget={snap.budget} />
        </section>
      ) : null}

      {snap.dry_run && snap.session_plan ? (
        <section>
          <h2>Waiting to trade — what the agent would do at the next open</h2>
          <div className="card" style={{ marginBottom: 12 }}>
            <div className="sub">
              {snap.session_plan.summary}
              {snap.session_plan.brief?.session_tone
                ? ` · session reads ${snap.session_plan.brief.session_tone}`
                : ""}
            </div>
            {snap.session_plan.brief?.summary ? (
              <div className="sub muted" style={{ marginTop: 8 }}>
                {snap.session_plan.brief.summary}
              </div>
            ) : null}
          </div>

          <div className="scroll" style={{ marginBottom: 12 }}>
            <table>
              <thead>
                <tr><th>Symbol</th><th>Trend</th><th>Realized vol</th><th>Implied vol</th>
                    <th>Premium</th><th>Read</th></tr>
              </thead>
              <tbody>
                {Object.entries(snap.session_plan.regimes).map(([sym, r]) => (
                  <tr key={sym}>
                    <td><strong>{sym}</strong> <span className="muted">{r.spot}</span></td>
                    <td>{r.trend} / {r.bias}</td>
                    <td>{r.realized_vol !== null ? pct(r.realized_vol, 1) : "--"}</td>
                    <td>{r.atm_iv !== null ? pct(r.atm_iv, 1) : "--"}</td>
                    <td className={(r.vol_premium ?? 0) > 0 ? "up" : "down"}>
                      {r.vol_premium !== null ? pct(r.vol_premium, 2) : "--"}
                    </td>
                    <td className="muted">
                      {(r.vol_premium ?? 0) > 0
                        ? "implied above realized — premium is paid well"
                        : "implied below realized — options are cheap"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="scroll">
            <table>
              <thead><tr><th>Structure</th><th>Verdict</th><th>Size</th><th>Why</th></tr></thead>
              <tbody>
                {snap.session_plan.candidates.map((c, i) => (
                  <tr key={i}>
                    <td><strong>{c.symbol}</strong> {c.style.replace(/_/g, " ")}</td>
                    <td>
                      <span className={`verdict ${c.verdict === "would trade" ? "approved" : "rejected"}`}>
                        {c.verdict}
                      </span>
                    </td>
                    <td className="muted">
                      {c.verdict === "would trade" && c.qty
                        ? `${c.qty} · risk ${money(c.max_loss ?? 0)}`
                        : "--"}
                    </td>
                    <td className="muted">{c.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="sub muted" style={{ marginTop: 8 }}>
            {snap.session_plan.note}
          </div>
        </section>
      ) : null}

      <section>
        <h2>Account equity</h2>
        <EquityCurve points={snap.equity_curve} />
      </section>

      <section>
        <h2>
          Open book
          {marks.length ? (
            <span className="h2-note">
              {marks.length} live · marked against the chain right now
              {acting.length ? (
                <span className="down"> · {acting.length} at an exit rule</span>
              ) : null}
            </span>
          ) : null}
        </h2>
        {snap.open_structures.length === 0 ? (
          <div className="empty">Flat. No open structures.</div>
        ) : (
          <div className="positions">
            {snap.open_structures.map((s) => (
              <PositionCard key={s.id} structure={s} mark={markById.get(s.id) ?? null} />
            ))}
          </div>
        )}
        {snap.live && !snap.live.ok ? (
          <div className="sub muted" style={{ marginTop: 8 }}>
            Live marks unavailable on the last pass, so the cards show what was
            journaled at entry rather than what the book is worth now.
          </div>
        ) : null}
      </section>

      <section>
        <h2>Closed trades — where the P&amp;L came from</h2>
        <div className="sub muted" style={{ marginBottom: 10 }}>
          Every closed structure, leg by leg, with the reason it ended. These rows sum to the
          realized P&amp;L above, and to the same number in Alpaca&apos;s own account history.
        </div>
        <div className="scroll">
          {(snap.closed_structures ?? []).length === 0 ? (
            <div className="empty">Nothing closed yet.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Closed</th><th>Structure</th><th>Sleeve</th><th>Qty</th>
                  <th>Entry</th><th>Max loss</th><th>Held</th>
                  <th>Realized</th><th>On risk</th><th>Exit</th>
                </tr>
              </thead>
              <tbody>
                {(snap.closed_structures ?? []).map((s) => (
                  <tr key={s.id}>
                    <td className="muted">
                      {s.closed_at ? new Date(s.closed_at).toLocaleString() : "--"}
                    </td>
                    <td>
                      <strong>{s.underlying}</strong> {s.kind.replace(/_/g, " ")}
                      <div className="muted">
                        {s.legs.map((l) => `${l.side === "sell" ? "-" : "+"}${l.strike}${l.is_call ? "C" : "P"}`).join(" / ")}
                        {" · "}{s.legs[0]?.expiry}
                      </div>
                    </td>
                    <td>{s.sleeve}</td>
                    <td>{s.qty}</td>
                    <td className={s.net_price >= 0 ? "up" : "down"}>
                      {s.net_price >= 0 ? "+" : ""}{s.net_price.toFixed(2)}
                    </td>
                    <td className="down">{money(s.max_loss)}</td>
                    <td className="muted">
                      {s.held_hours != null ? `${s.held_hours.toFixed(1)}h` : "--"}
                    </td>
                    <td className={(s.realized_pnl ?? 0) >= 0 ? "up" : "down"}>
                      {s.realized_pnl != null
                        ? `${s.realized_pnl >= 0 ? "+" : ""}${money(s.realized_pnl)}`
                        : "--"}
                    </td>
                    <td className={(s.return_on_risk ?? 0) >= 0 ? "up" : "down"}>
                      {s.return_on_risk != null
                        ? `${(s.return_on_risk * 100).toFixed(0)}%`
                        : "--"}
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
        <h2>Risk gates — what the agent refused to do</h2>
        <div className="card">
          <div className="sub" style={{ marginBottom: 10 }}>
            {snap.gates.considered} structures considered · {snap.gates.approved} approved ·{" "}
            {snap.gates.rejected} rejected. Every rejection is attributed to the gate that made it.
          </div>
          {snap.gates.rejections_by_gate.map((g) => (
            <div className="gate" key={g.gate}>
              <span className="code">{g.gate}</span>
              <span className="name">{g.name}</span>
              <span className="bar">
                <span style={{ width: `${(g.count / maxRejections) * 100}%` }} />
              </span>
              <span className="n">{g.count}</span>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2>Decision trail</h2>
        <div className="scroll feed">
          <table>
            <thead>
              <tr><th>Time</th><th>Agent</th><th>Symbol</th><th>Verdict</th><th>Reasoning</th></tr>
            </thead>
            <tbody>
              {snap.recent_decisions.slice(0, 60).map((d) => {
                let reasons: string[] = [];
                try { reasons = JSON.parse(d.reasons); } catch { reasons = [d.reasons]; }
                return (
                  <tr key={d.id}>
                    <td className="muted">{new Date(d.ts).toLocaleTimeString()}</td>
                    <td>{d.agent}</td>
                    <td>{d.underlying ?? "--"}</td>
                    <td><span className={`verdict ${d.verdict}`}>{d.verdict}</span></td>
                    <td className="muted">{reasons.slice(0, 3).join(" · ")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2>Scheduled catalysts</h2>
        <div className="scroll">
          <table>
            <thead><tr><th>When</th><th>Event</th><th>Impact</th></tr></thead>
            <tbody>
              {snap.upcoming.length === 0 ? (
                <tr><td colSpan={3} className="empty">Nothing scheduled in the window.</td></tr>
              ) : snap.upcoming.map((e) => (
                <tr key={e.name + e.when}>
                  <td>{new Date(e.when).toLocaleString()}</td>
                  <td>{e.name}</td>
                  <td className={e.impact === "high" ? "down" : "muted"}>{e.impact}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {snap.google && snap.google.connected.length > 0 ? (
        <section>
          <h2>Connected calendars</h2>
          <div className="card" style={{ marginBottom: 12 }}>
            <div className="sub">
              {snap.google.connected.map((a) => (
                <span key={a.label} style={{ marginRight: 18 }}>
                  <span className={a.token === "valid" ? "up" : "down"}>&#9679;</span>{" "}
                  <strong>{a.label}</strong>{" "}
                  <span className="muted">
                    {a.email} · {a.calendars} calendars
                    {a.enabled ? "" : " · disabled"}
                  </span>
                </span>
              ))}
            </div>
            <div className="sub muted" style={{ marginTop: 8 }}>
              Events from these calendars feed gate G6 alongside the built-in catalysts.
              Tag an entry <span className="mono">[high]</span> or{" "}
              <span className="mono">[ignore]</span> to set its impact explicitly.
            </div>
          </div>

          {snap.google.events.length > 0 ? (
            <div className="scroll" style={{ marginBottom: 12 }}>
              <table>
                <thead><tr><th>When</th><th>Catalyst</th><th>Impact</th><th>Affects</th></tr></thead>
                <tbody>
                  {snap.google.events.map((e) => (
                    <tr key={e.name + e.when}>
                      <td className="muted">{new Date(e.when).toLocaleString()}</td>
                      <td>{e.name}</td>
                      <td className={e.impact === "high" ? "down" : "muted"}>{e.impact}</td>
                      <td className="muted">{e.affects.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {snap.google.tasks.length > 0 ? (
            <div className="scroll">
              <table>
                <thead><tr><th>Due</th><th>Task</th><th>Source</th></tr></thead>
                <tbody>
                  {snap.google.tasks.map((t, i) => (
                    <tr key={i}>
                      <td className="muted">{t.due ? String(t.due).slice(0, 10) : "--"}</td>
                      <td>{t.title}</td>
                      <td className="muted">{t.account} / {t.list}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      ) : null}

      <footer>
        Paper trading only. Hypothetical results, not investment advice. Options carry risk
        including total loss of premium. Built for the Alpaca AI Trading Agents Hackathon.
      </footer>
    </div>
  );
}
