export type Bar = {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type Leg = {
  symbol: string;
  side: "buy" | "sell";
  strike: number;
  expiry: string;
  is_call: boolean;
};

export type ExitRule = {
  basis: string;
  target_pct: number;
  target_value: number;
};

export type Trade = {
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
  gates: string;
  held_hours: number | null;
  levels: {
    short_strikes: number[];
    long_strikes: number[];
    min_strike: number | null;
    max_strike: number | null;
    breakeven: number | null;
  };
  take_profit: ExitRule;
  stop: ExitRule;
};

export type ChartPayload = {
  generated_at: string;
  bars: Record<string, Bar[]>;
  trades: Trade[];
  exit_rules: Record<string, number>;
};
