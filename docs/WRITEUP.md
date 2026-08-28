# Superio — one-page write-up

**Alpaca AI Trading Agents Hackathon · 28 August – 4 September 2026**
Autonomous defined-risk options agent. Paper account `PA3Z3MMXLBZ0`.

## The thesis

A language model is good at reading a market and bad at respecting a limit. So
the model never touches the limits.

Superio splits the two jobs completely. A deterministic layer reads the
volatility surface, builds candidate option structures from the live chain,
sizes them, and refuses the ones that fail any gate. Only then does a model get
involved, and all it can do is pick one of the survivors or decline. It returns
an integer index. It cannot invent a structure, move a strike, change a
quantity, or widen a limit, because none of those are in its output space.

The worst thing a confused or prompt-injected model can do is choose a slightly
worse safe trade, or trade nothing at all.

## The AI logic

Four roles, only one of which is a model call that matters:

**Scout** (deterministic) reads trend against the 20 and 50 day averages, 20-day
realized volatility, and at-the-money implied volatility, and reports the spread
between implied and realized. That spread decides which sleeve leads. When
implied sits above realized, selling defined-risk premium is being paid well.
When implied sits below realized, the market is charging too little for the
movement it is actually delivering, so the agent buys that cheap optionality
instead. At kick-off the reading was SPY implied 9.1% against realized 10.3%,
and QQQ implied 13.4% against realized 17.9%: cheap options on both, so the
convex sleeve leads this week.

**Strategist** (Claude) sees the regime, up to five headlines, and a numbered
list of structures that have already passed every gate and already been sized.
It picks an index or returns -1. Two samples are drawn and must agree; on
disagreement the deterministic ranking wins. Headlines are treated as untrusted
data, never as instructions.

**Risk officer** (deterministic, no model call anywhere in the file) is below.

**Journalist** (Claude) writes the daily record after the fact. It touches
nothing and carries no risk.

A small open-source model on Featherless does the high-volume work: reducing a
stream of headlines to sentiment labels so that Claude only reads the few that
matter. Cheap model for volume, expensive model for judgement, neither trusted
with an order.

## The risk gates

Seven numbered gates run in order. The first refusal ends the evaluation, and
every refusal is journalled by gate name, so any decision can be traced to one
rule.

| | Gate | Limit |
|---|---|---|
| G1 | Kill switches | −3% on the day flattens and stands down; −8% drawdown ends the event |
| G2 | Trade budget | 8 new structures per day, 10 open at once |
| G3 | Defined risk | Every short leg needs a same-expiry, same-type long leg. No flag disables this |
| G4 | Liquidity | Bid/ask no wider than 12% of mid, per leg |
| G5 | Pricing | Never sell a spread for under 18% of its width; never pay over 45% of width |
| G6 | Event blackout | No writing premium across a scheduled catalyst |
| G7 | Sizing | 0.75% of equity per structure, 6% total, 3% convex sleeve, 2.5% per underlying |

Two details matter more than the numbers.

**Everything is priced at the touch, not the mid.** Alpaca's paper engine fills
against the NBBO, so sizing off mid prices quietly understates real maximum
loss. Every risk calculation uses the price you get when you cross the spread.
Orders still go out at the mid, so any fill is better than the one underwritten.

**G6 is asymmetric on purpose.** Broadcom reports Wednesday after the close and
is a top-five QQQ weight; the August employment report lands Friday at 08:30 ET,
two and a half hours before the judging mark. Writing premium across either is
selling insurance at the moment the accident is scheduled. Buying convexity into
them is the trade, so convex structures pass the gate and credit structures do
not.

Separately, the manager flattens anything expiring that day at 15:00 ET. Alpaca
begins auto-assignment at 15:30 and evaluates each leg independently, so a short
leg finishing in the money is assigned while the out-of-the-money long leg
simply expires, leaving an unhedged stock position.

## The Alpaca infrastructure

Both interfaces are used, and the split is deliberate.

**The CLI is the execution path.** Every order is `alpaca api POST /v2/orders`
with a JSON body, so each trade is reproducible as a shell command, and the
exact command is stored in the journal next to the proposal and the risk verdict
that produced it. Multi-leg spreads go out as one `order_class: "mleg"` package
and close the same way, with a leg-by-leg fallback that always buys back short
legs first so the account is never briefly naked.

**The MCP server is the research path**, running over stdio with read-only
toolsets, which is how the agent reads news and market state. Execution
deliberately does not go through it: MCP v2 has an open bug (#97) where
multi-leg orders arrive with the legs array as a raw string, and every structure
this agent trades is multi-leg.

Market data comes through `alpaca-py`. Greeks come from the snapshot when the
feed provides them and are otherwise solved locally from the mid price with
Black-Scholes, so strike selection never depends on a paid data plan.

## Honest limits

Paper fills are more generous than live ones, especially on wide quotes, so
these results are optimistic by construction. The free indicative feed delays
trades by fifteen minutes. Four and a half sessions is far too short a sample to
distinguish skill from variance, and we would say so whatever the P&L reads on
Friday. The same engine runs on two further paper accounts with different
presets, a convexity-led variant and an income-only control, so the week
produces a comparison rather than a single number.
