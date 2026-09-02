export type Perf = {
  trades_closed: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  realized_pnl: number;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  max_drawdown_pct: number;
  equity_start: number;
  equity_latest: number;
  return_pct: number | null;
  by_sleeve: Record<string, { n: number; pnl: number; wins: number }>;
  by_kind: Record<string, { n: number; pnl: number; wins: number }>;
  by_underlying: Record<string, { n: number; pnl: number; wins: number }>;
};

export type Leg = {
  symbol: string;
  side: "buy" | "sell";
  strike: number;
  expiry: string;
  is_call: boolean;
};

export type Structure = {
  id: number;
  opened_at: string;
  closed_at: string | null;
  sleeve: string;
  underlying: string;
  kind: string;
  legs: Leg[];
  qty: number;
  net_price: number;
  max_loss: number;
  max_gain: number;
  status: string;
  realized_pnl: number | null;
  close_reason: string | null;
  thesis: string | null;
  held_hours?: number | null;
  return_on_risk?: number | null;
};

export type AgentSummary = {
  id: string;
  label: string;
  variant: string;
  live: boolean;
  stake: number;
  status: string;
  performance: Perf | null;
  open_structures?: number;
  open_risk?: number;
  generated_at?: string | null;
  age_seconds?: number | null;
  dry_run?: boolean;
  diary?: boolean;
  gates?: {
    considered: number;
    approved: number;
    rejected: number;
    rejections_by_gate: { gate: string; count: number }[];
  };
};

export type AgentDetail = AgentSummary & {
  open: Structure[];
  closed: Structure[];
  equity_curve: { ts: string; equity: number; open_risk: number; day_pnl: number }[];
  decisions: {
    id: number; ts: string; agent: string; sleeve: string | null;
    underlying: string | null; verdict: string; reasons: string;
  }[];
  events: { id: number; ts: string; level: string; kind: string; message: string }[];
};

export const GATE_NAMES: Record<string, string> = {
  G1: "kill switches",
  G2: "daily and concurrent trade budget",
  G3: "defined risk, no naked shorts",
  G4: "leg liquidity",
  G5: "credit floor and debit cap",
  G6: "scheduled event blackout",
  G7: "volatility side",
  G8: "position sizing",
};
