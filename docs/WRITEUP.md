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

## Raising the risk cap, and why only after fixing the payoff

The judged account runs a 10% open-risk cap against a 14% total-drawdown kill
switch. It ran 6% and 8% for most of the week, and the order of those two
changes is the point.

At 6% with the income sleeve uncapped, the entire week could produce about 2%
of equity. Deploying more would have bought more of a 0.33x payoff, which is a
bigger hole underneath the same small number. The measured book made this
concrete: three live structures controlling 2.4 million dollars of notional,
risking 2,863 dollars, able to win 937. One of them risked 1,080 to make 120.
Doubling that is not ambition, it is just a worse trade twice.

So the allocation was fixed first. Core is capped, and the budget became
reachable by structures paying 2.2 to 2.6x instead of 0.33x. Only then does
deployment scale something worth scaling.

| | Before | After |
| --- | --- | --- |
| Open-risk cap | 6% | 10% |
| Drawdown kill switch | 8% | 14% |
| Blended payoff | 0.33x | ~2.2x |
| Maximum gain | ~$2,000 | ~$22,300 |
| Worst case | -$6,083 | -$10,138 |

Measured against the live chain immediately after the change, the three carry
structures the agent would open went from one contract each to three, two and
four: 5,507 dollars of risk carrying 13,193 of maximum gain, against 937 for
the book it replaced. Fourteen times the upside for under three times the risk,
because the risk is now being spent on a different shape.

The kill switches moved with the cap and that is not cosmetic. Fully deployed,
the worst case IS the open-risk cap, so a cap above the stand-down threshold
means one gap through every position ends the event while the switch protects
nothing. A test enforces `max_open_risk_pct < total_drawdown_kill_pct` across
every preset, and it caught the `levered` diary book on sight holding 12% of
risk against an 8% switch, which is what its 264% replayed drawdown actually
was.

That invariant is also why this is 10 and not 15. The replay's headline return
argues for 15, and at 15 a single bad gap with two sessions left ends the event
with no time to recover. The cap is set by what the account can survive, not by
what the backtest would have preferred.

Two tests had to be rewritten as part of this, and they were wrong in an
instructive way. `test_sizing_respects_per_trade_cap` asserted `qty == 1` with
a comment explaining that 0.75% of 100,000 divided by 400 gives 1. Raising the
cap broke a test whose subject is whether the cap is respected, which is not
what it was for. Both now derive their expectations from the configuration, so
they test the behaviour rather than the number.

## Why the week was capped at one percent

The judged account finished its first four sessions up 1.38%. That looks like
under-deployment and it is not; it is arithmetic, and it is worth writing out
because it decides what to fix.

Only the convex and carry sleeves had caps of their own. Short premium was
bounded by nothing but the portfolio total, and it is the cheapest structure to
build and the most frequently available, so it filled the budget first and the
sleeves with real payoffs competed for whatever was left.

At the 18% credit-to-width floor, a credit spread wins 0.22 dollars for every
dollar it risks. So a book fully deployed in premium selling looks like this:

| | |
| --- | --- |
| Full deployment at the 6% cap | $6,083 |
| Credit per dollar of risk | 0.22 |
| Best possible week, every spread expiring worthless | **1.32%** of equity |
| At the 55% profit target actually used | **0.72%** of equity |

The account made 1.38%. It had already beaten the theoretical ceiling of the
sleeve holding most of its risk, and it did that because one debit spread
returned 140% on risk and carried the week.

Deploying more does not fix a 0.22x payoff, it scales it. The same $6,083 of
risk, allocated three ways:

| Sleeve | Payoff | Max gain | As % of equity |
| --- | --- | --- | --- |
| core, sell premium | 0.22x | $1,335 | 1.32% |
| convex, buy convexity | ~3.0x | $18,248 | 18.0% |
| carry, financed long delta | ~2.6x | $15,815 | 15.6% |

So the fix is allocation, not leverage. Core is now capped at 2% of equity, and
the 6% portfolio total is unchanged, which means the risk of ruin is exactly
what it was and roughly two thirds of the budget is now reachable by structures
that can pay more than a fifth of what they risk. Measured against the live
chain, that is $4,509 of risk across carry and convex with about $11,000 of
maximum gain, against a previous ceiling of $1,335 for the same risk.

Two invariants are now pinned by tests, because both were violated in code that
ran. Every sleeve in the `Sleeve` type must have a cap named after it, or
sizing cannot bound it: that is how core came to hold most of the budget. And
open risk must stay below the total-drawdown kill switch, or a gap through
every position ends the event while the switch protects nothing. The `levered`
preset failed that second test on sight, holding 12% of risk against an 8%
switch, which is why it replayed at a 113% drawdown.

## The sleeve that holds something

The first version of this agent traded nothing that lived longer than a week.
Every structure was one to seven days out and closed within a day, which has
two consequences worth stating plainly.

It had no exposure to the one edge in equities with decades of evidence behind
it. You are not paid the equity risk premium for being flat overnight.

And selling premium caps its own upside at the credit taken. At six percent of
equity deployed and a credit worth about a quarter of width, the best possible
week is around two percent however well the week is run.

Those are the same complaint from two directions, so there is a third sleeve.

**Carry.** Sell a thirty delta put spread, use the credit to buy a wider call
spread, same expiry, five to nine weeks out. Long delta, so the position is
paid for holding the exposure rather than for predicting a move. Defined risk
on both sides, so a crash costs the put spread's width and G3 still holds with
no exception carved for it. And an upside that is a multiple of its risk rather
than a fraction of its width: on a live SPY chain, 631 dollars of risk against
1,669 of maximum gain, 2.6x, where a credit spread of the same risk can win
about 190.

The financing comes from the shape of the surface, not from a view. Index puts
trade at a higher implied volatility than calls the same distance away, so the
structure sells the expensive side of the skew to fund the cheap side. That
holds whether implied volatility overall is rich or cheap, which is why this
sleeve is deliberately not routed by the variance risk premium the way the
other two are.

Four things in the engine assumed a position lives for a day. Each would have
broken this sleeve without ever failing loudly.

G6 refuses credit structures with a high-impact catalyst before expiry. A
forty-five day structure spans every catalyst on the calendar by construction,
so the gate would have refused the sleeve outright rather than protected it.
Holding through events is what a multi-week position is for.

G7 routes on the level of implied volatility. A risk reversal is short put
volatility and long call volatility at the same time, so a single premium
reading cannot say which side of it is right. Routed as a credit structure it
would have been refused in exactly the cheap-volatility regimes where a
financed long position is most attractive.

G5 compared the net debit to `proposal.width`, which for this package is the
put width. That asks whether the premium paid is a sensible fraction of a
number the premium did not buy: it read as a debit of 110 percent of width and
refused every structure the sleeve could build. `Proposal.pricing_width` now
states the denominator properly, as what the structure can pay out gross, and
reduces to exactly the strike width for every vertical.

The manager dispatches exits on the sign of the entry price. A risk reversal
opens for a small credit or a small debit depending on where the skew sits that
morning, so run through the credit branch a ten cent credit would be judged
against fifty-five percent of ten cents: target hit on the first tick, position
closed for nothing, sleeve useless.

Two more came out of getting it wrong. Maximum loss was left in points where
the rest of the engine uses per-contract dollars, and the risk officer read six
dollars of risk where there were 631 and sized 118 contracts into a 750 dollar
cap. And the P&L sign was inverted, which throws nothing at all: it reports the
best possible outcome as the worst, stops out every winner and holds every
loser to expiry while all eight gates still pass. There is a test for each.

## Why three accounts

The replay harness cannot tell us what the strategy will earn: with no
historical option prices available, entry and exit are priced with the same
model, so no volatility risk is simulated. What it can do is rank variants
against each other, because every one is scored on the same tape with the same
modelling error, and that error largely cancels in the comparison.

Replayed over five years of SPY, QQQ and IWM, weighted across volatility
regimes:

| Variant | Book | Trades | Win rate | Profit factor | Max drawdown |
| --- | --- | --- | --- | --- | --- |
| long gamma | diary | 1,914 | 46.6% | **1.41** | **16.8%** |
| levered | diary | 2,875 | 40.9% | 1.39 | 97.0% |
| barbell (judged) | live | 2,874 | 40.9% | 1.38 | 61.5% |
| convex tilt | live | 1,440 | 39.6% | 1.34 | 18.0% |
| vrp router | diary | 3,854 | 47.1% | 1.30 | 71.5% |
| income only (control) | live | 1,975 | **64.7%** | **0.66** | 82.7% |
| fat credit | diary | 1,318 | 54.5% | 0.65 | 61.8% |

The control arm is the interesting row. It wins nearly two trades in three and
still loses money: the classic premium-selling trap, where a few full-width
losses outweigh a long run of small credits. Its drawdown is the worst of the
three live presets despite the highest win rate.

That is a falsifiable prediction rather than a story, and it is why the same
engine runs on three accounts. If income-only finishes the week ahead of the
barbell, the harness was wrong about something and we will say so.

Three of the diary presets exist to attack the strategy rather than defend it,
and all three answered.

`fat_credit` sells at 38 delta for a much fatter credit, testing whether the
18% credit-to-width floor is protecting us or costing us. It lands at a profit
factor of 0.65, indistinguishable from income_only. Selling closer to the money
buys a higher win rate and pays for it exactly.

`levered` runs the judged preset at twice the risk. It returns 212% against the
barbell's 76%, and takes a 97% drawdown getting there. The return scales
roughly with deployment; the drawdown scales worse and lands somewhere no
account survives. That is the whole answer to "should we deploy more", and it
is why the 6% cap has not moved.

`long_gamma` is the one that beat the judged preset: the best profit factor of
the seven and by far the smallest drawdown. Read alongside the sleeve split, it
is the same finding in every single row of the table, including the losing
ones: the convex sleeve carries the P&L and the core sleeve is flat to
negative. Under this harness, premium selling never pays for itself.

The honest caveat is that this is exactly the result a Black-Scholes replay is
most likely to produce. Entry and exit share a pricing model, so a long option
is never marked down for the volatility risk premium it really pays. Sweeping
the premium across regimes softens that but does not remove it. The finding
worth acting on is the ranking, not the level, and the live accounts are the
test: main runs the barbell, and if the convex sleeve carries it there too,
long_gamma has earned a real account.

Two caveats. The harness applies no kill switches, which is why its drawdowns
run far past the 8% the live agent enforces. And a 41% win rate with a profit
factor of 1.39 is the shape of a long-gamma book: it loses often and wins big,
so a four-and-a-half session sample can easily land on the wrong side of that
distribution.
