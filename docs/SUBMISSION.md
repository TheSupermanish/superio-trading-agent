# Submission copy

Paste-ready fields for the lablab.ai form.

## Title (50 char max)

```
Superio — the model never touches the limits
```

Fallback if the dash is stripped: `Superio: defined-risk options agent`

## Short description (255 char max)

```
An autonomous options agent on Alpaca. A language model reads the market and picks
from structures that already passed seven risk gates; it returns an index, never a
strike, a size, or a limit. Every order is a reproducible shell command.
```

## Long description

```
Superio trades defined-risk option structures on SPY, QQQ and IWM in an Alpaca
paper account. Its design rests on one observation: a language model is good at
reading a market and bad at respecting a limit. So the model never touches the
limits.

Deterministic code reads the volatility surface, builds candidate structures from
the live option chain, sizes them, and refuses anything failing a gate. Only then
does the model get involved, as a real tool-using agent: it reads its remaining
risk budget, pulls the live strike ladder, checks the catalyst calendar and open
positions, and calls propose_structure repeatedly to compare shapes. Every
proposal is built from the chain by our code and run through all seven gates
before the model sees it, so a rejection names the gate that refused it and the
model adapts. Its final answer is an index into structures already approved and
already sized. It never emits a leg, a strike, a limit, or a quantity.

The seven gates are numbered and independently tested. G3 makes a naked short
structurally impossible: every short leg needs same-expiry, same-type cover, and
no flag disables it. G6 refuses to write premium across a scheduled catalyst.
Kill switches flatten at minus five percent on the day and stand down at minus
fourteen percent, which sits deliberately above the ten percent open-risk cap:
fully deployed the worst case IS that cap, so a switch below it would let one
gap end the event having protected nothing. Everything is priced at the touch
rather than the mid, because Alpaca's paper engine fills against the NBBO and
sizing off mid prices understates real maximum loss.

Each sleeve is capped separately, and that is the load-bearing part. Short
premium was bounded only by the portfolio total, and it is the cheapest
structure to build, so it filled the budget first at a payoff of 0.33 dollars
per dollar risked. The live book was controlling 2.4 million dollars of
notional to win 937. Capping it and letting carry and convexity reach the rest
took the same risk from 937 of maximum gain to 13,193.

Both Alpaca interfaces are used, and the split is deliberate. The CLI is the
execution path, so every trade is reproducible as a shell command a judge can
paste into a terminal. The MCP server is the research path, running read-only
over stdio. Execution deliberately avoids MCP because its v2 multi-leg order bug
would break every structure this agent trades.

Reasoning runs on Gemini 2.5 Pro over Vertex with a search-grounded analyst phase
that establishes what is actually happening today; on its first run it surfaced a
Fed speech our hand-maintained calendar had missed.

The same engine runs on three paper accounts with different presets, a barbell,
a convexity-led variant and an income-only control, so the week produces a
comparison rather than a single number. Four further presets run as diary
books: same live chain, same gates, their own journal, no broker account and no
way to reach one. They exist to attack the strategy, and they landed: selling
closer to the money for a fatter credit is a wash, doubling the risk cap
returns 212 percent with a 97 percent drawdown, and a convexity-only book beats
the judged preset on both profit factor and drawdown. 82 tests cover the gates, the order
ladder and every exit rule. The public dashboard shows realized P&L beside every
structure the agent refused and the gate that refused it.
```

## Tags

```
Options Trading, Alpaca, Trading API, MCP, Gemini, Vertex AI, Autonomous Agents,
Risk Management, Python, Next.js
```

## Required fields

| Field | Value |
| --- | --- |
| Alpaca paper account ID | `b585f795-0dac-4e23-83bb-636fc071bb00` |
| Account number | `PA3Z3MMXLBZ0` |
| Public repo | https://github.com/TheSupermanish/superio-trading-agent |
| Demo application URL | http://34.55.253.209:8088 |
| Mirror | https://thesupermanish.github.io/superio-trading-agent/ |
| One-page write-up | `docs/WRITEUP.md` |
| Licence | MIT |

## Video script, 4:45

**0:00–0:30 — the problem, on camera**

> Most AI trading agents let the model decide how much to bet. That's the part
> language models are worst at. Superio inverts it: the model reads the market,
> but it never touches the limits.

**0:30–2:30 — live demo, screen recording**

1. Terminal: `python -m engine.loop --once`. Show preflight going green, including
   the billing guard and the account at $100,000.
2. Show the agent's tool calls scrolling: `get_account_state`, `get_regime` for
   each symbol, `get_calendar`, then `propose_structure`.
3. **Stop on a rejection.** Read it aloud: "G5 pricing: credit 17.0% of width is
   below the 18% floor." Say: nobody else will show you the trades their agent
   refused.
4. Show the order going out as a shell command, then the fill.
5. Cut to the dashboard: the structure appears, then the gate report.

**2:30–3:30 — results**

Realized P&L, win rate, profit factor, max drawdown, trade count, straight off the
dashboard. Then the three-account comparison: barbell versus convexity-led versus
income-only over the same tape.

**3:30–4:15 — architecture**

The seven gates. Say the one line that matters: the model returns an index, so the
worst a confused or prompt-injected model can do is pick a worse safe trade.

**4:15–4:45 — team and what's next**

Real IV history for a proper backtest, and Provisioned Throughput if this ran at
size.

## Honest notes for the write-up

Paper fills are more generous than live ones. The free feed delays trades fifteen
minutes. Four and a half sessions cannot separate skill from variance, which is
exactly why three variants run side by side.
