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
  account_id: string;
  limits: Record<string, number>;
  performance: {
    trades_closed: number;
    wins: number;
    losses: number;
    win_rate: number | null;
    realized_pnl: number;
    profit_factor: number | null;
    max_drawdown_pct: number;
    equity_start: number;
    equity_latest: number;
    return_pct: number | null;
    by_sleeve: Record<string, { n: number; pnl: number; wins: number }>;
    by_kind: Record<string, { n: number; pnl: number; wins: number }>;
  };
  gates: {
    considered: number;
    approved: number;
    rejected: number;
    rejections_by_gate: { gate: string; name: string; count: number }[];
  };
  open_structures: Structure[];
  open_risk: number;
  equity_curve: EquityPoint[];
  recent_decisions: Decision[];
  recent_events: { id: number; ts: string; level: string; kind: string; message: string }[];
  upcoming: { name: string; when: string; impact: string }[];
};
