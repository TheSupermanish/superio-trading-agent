export type LiveMark = {
  structure_id: number;
  underlying: string;
  kind: string;
  sleeve: string;
  qty: number;
  entry_price: number;
  current_price: number;
  unrealized_pnl: number;
  pct_of_max_gain: number;
  dte: number;
  action: string;
  rationale: string;
  max_loss: number;
  max_gain: number;
  spot: number | null;
  short_strike: number | null;
  distance_pct: number | null;
  tp_progress: number;
  sl_progress: number;
  tp_basis: string;
  sl_basis: string;
};

export type SleeveBudget = {
  sleeve: string;
  used: number;
  cap: number;
  cap_pct: number;
  used_pct_of_equity: number;
  utilisation: number | null;
};

export type Budget = {
  equity: number;
  sleeves: SleeveBudget[];
  total_used: number;
  total_cap: number;
  total_cap_pct: number;
  total_utilisation: number | null;
  daily_kill_pct: number;
  drawdown_kill_pct: number;
  max_new_trades_per_day: number;
  max_open_structures: number;
  trades_today: number;
  failed_today: number;
};

export type EquityPoint = {
  ts: string;
  equity: number;
  cash: number;
  buying_power: number;
  open_risk: number;
  day_pnl: number;
};

export type Leg = {
  symbol: string;
  side: "buy" | "sell";
  strike: number;
  expiry: string;
  is_call: boolean;
  mid: number;
  delta: number | null;
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
  thesis: string;
  held_hours?: number | null;
  return_on_risk?: number | null;
};

export type Decision = {
  id: number;
  ts: string;
  agent: string;
  sleeve: string | null;
  underlying: string | null;
  proposal: string;
  verdict: string;
  reasons: string;
};

export type Snapshot = {
  generated_at: string;
  profile: string;
  variant: string;
  dry_run: boolean;
  diary?: boolean;
  account_id: string;
  limits: Record<string, number>;
  live?: { ok: boolean; marks: LiveMark[]; spots: Record<string, number> };
  budget?: Budget;
  performance: {
    trades_closed: number;
    wins: number;
    losses: number;
    win_rate: number | null;
    realized_pnl: number;
    avg_win?: number | null;
    avg_loss?: number | null;
    profit_factor: number | null;
    max_drawdown_pct: number;
    equity_start: number;
    equity_latest: number;
    total_pnl?: number;
    realized_in_journal?: number;
    realized_implied?: number;
    realized_unrecorded?: number;
    open_pnl?: number;
    return_pct: number | null;
    by_sleeve: Record<string, { n: number; pnl: number; wins: number }>;
    by_kind: Record<string, { n: number; pnl: number; wins: number }>;
    by_underlying?: Record<string, { n: number; pnl: number; wins: number }>;
  };
  gates: {
    considered: number;
    approved: number;
    rejected: number;
    rejections_by_gate: { gate: string; name: string; count: number }[];
  };
  open_structures: Structure[];
  closed_structures: Structure[];
  open_risk: number;
  equity_curve: EquityPoint[];
  recent_decisions: Decision[];
  recent_events: { id: number; ts: string; level: string; kind: string; message: string }[];
  upcoming: { name: string; when: string; impact: string }[];
  session_plan?: {
    generated_at: string;
    summary: string;
    note: string;
    regimes: Record<string, {
      spot: number; trend: string; bias: string;
      realized_vol: number | null; atm_iv: number | null; vol_premium: number | null;
    }>;
    candidates: {
      symbol: string; style: string; verdict: string; reason: string;
      qty?: number; net_price?: number; max_loss?: number; expiry?: string;
    }[];
    brief?: { session_tone?: string; summary?: string; vol_context?: string };
  } | null;
  google?: {
    connected: { label: string; email: string; enabled: boolean; token: string; calendars: number }[];
    tasks: { account: string; list: string; title: string; due: string | null }[];
    events: { name: string; when: string; impact: string; affects: string[] }[];
  };
};
