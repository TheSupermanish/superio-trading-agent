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

**Analyst** (Gemini 2.5 Pro with Google Search grounding) establishes what is
actually happening today: session tone, scheduled speakers, anything that moved
the tape in the last few hours. A volatility model cannot know that a central
banker speaks at 10am. On the first run it surfaced a Jackson Hole speech that
our hand-maintained calendar had missed. It has no tools and touches nothing.

**Strategist** (Gemini 2.5 Pro, function calling) is a real tool-using loop, not
a single classification call. It can read the account state and remaining risk
budget, pull the live strike ladder at several delta buckets, read the regime,
check the catalyst calendar, see what is already open, and call
`propose_structure` as many times as it wants to compare shapes. Each proposal
is built from the live chain by our code and run through every gate before the
model is told anything, so a rejection comes back naming the gate that refused
it and why. The model reads that and adapts.

The safety property is structural rather than a matter of prompting. The model
never sees or emits legs, strikes, limits, or quantities. `propose_structure`
takes a ticker and a shape name; our code builds the legs and the risk officer
sizes them. Its final answer is a reference to a proposal that was already
approved. A model that is confused, wrong, or prompt-injected can at worst pick
a worse safe trade or refuse to trade.

On its first live run against the income-only preset it read three regimes,
checked the calendar, proposed two debit spreads, watched both get sized to zero
because that preset carries no convex budget, and stood aside on the grounds
that selling premium would fight the tape. That is the behaviour we wanted:
it argued itself out of a trade.

**Risk officer** (deterministic, no model call anywhere in the file) is below.

**Journalist** (Claude) writes the daily record after the fact. It touches
nothing and carries no risk.

Gemini 2.5 Flash does the high-volume work, reducing a stream of headlines to
sentiment labels so the reasoning model only reads the few that matter. Cheap
model for volume, strong model for judgement, neither trusted with an order.

Vertex authenticates through application-default credentials, so there is no API
key stored anywhere in this project. Three deployment details cost us time and
are worth recording:

Vertex refuses to combine Google Search grounding with function calling in a
single request, which is why research and decision-making are separate phases.
The `global` endpoint accepts a grounded request and then returns an empty
candidate, so grounding is pinned to a region that serves it. And Gemini 2.5
runs on dynamic shared quota, meaning a 429 is contention in a pool shared
across all customers of that model rather than an account limit that can be
raised. Regional pools are independent, so every model call walks a list of
model and region routes and only backs off once a whole region has refused.

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

## Why three accounts

The replay harness cannot tell us what the strategy will earn: with no
historical option prices available, entry and exit are priced with the same
model, so no volatility risk is simulated. What it can do is rank variants
against each other, because every one is scored on the same tape with the same
modelling error, and that error largely cancels in the comparison.

Replayed over five years of SPY, QQQ and IWM, weighted across volatility
regimes:

| Variant | Trades | Win rate | Profit factor |
| --- | --- | --- | --- |
| barbell (judged) | 2,875 | 41.8% | 1.39 |
| convex tilt | 1,441 | 40.1% | 1.39 |
| income only (control) | 1,971 | **64.6%** | **0.65** |

The control arm is the interesting row. It wins nearly two trades in three and
still loses money: the classic premium-selling trap, where a few full-width
losses outweigh a long run of small credits. Its drawdown is the worst of the
three despite the highest win rate.

That is a falsifiable prediction rather than a story, and it is why the same
engine runs on three accounts. If income-only finishes the week ahead of the
barbell, the harness was wrong about something and we will say so.

Two caveats. The harness applies no kill switches, which is why its drawdowns
run far past the 8% the live agent enforces. And a 41% win rate with a profit
factor of 1.39 is the shape of a long-gamma book: it loses often and wins big,
so a four-and-a-half session sample can easily land on the wrong side of that
distribution.
