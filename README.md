# Superio

An autonomous options trading agent on Alpaca. Built for the
[Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)
(28 August - 4 September 2026).

Every strategy it trades is a defined-risk options structure. Every order it
sends goes through the official Alpaca CLI, so any trade it makes is
reproducible as a shell command. Every decision it makes, including the ones
that were rejected, is written to a journal you can read.

## The idea in one paragraph

An LLM is good at reading a market and terrible at respecting a limit. So the
model never touches the limits. A deterministic risk officer sits between the
strategist and the broker and enforces position sizing, defined-risk structure,
liquidity, and kill switches as plain numbers in a single file. The model
proposes; the numbers decide. If the model asks for something reckless, the
worst it can do is get told no and have the refusal logged.

## Architecture

```
  Scout          reads the regime: trend, realized vol, implied vol, the spread between them
    |
  Strategist     builds candidate structures from the live option chain
    |
  Risk officer   deterministic gate. Sizes the trade or refuses it. No model call in this file.
    |
  Executor       sends the order through the Alpaca CLI
    |
  Manager        marks the book, takes profit, stops losses, flattens before assignment
```

The volatility premium decides which sleeve leads. When implied vol sits above
realized vol, selling defined-risk premium is being paid well and the core
sleeve leads. When implied vol sits below realized vol, the market is charging
too little for the movement it is actually delivering, so the convex sleeve
leads instead and buys that cheap optionality.

## Risk gates

All of these live in `engine/config.py` and are enforced in `engine/risk.py`.

| Gate | Limit |
| --- | --- |
| Max loss on one structure | 0.75% of equity |
| Max loss across all open structures | 6% of equity |
| Convex sleeve cap | 3% of equity |
| Max risk per underlying | 2.5% of equity |
| Daily loss kill switch | -3%, flatten and stand down |
| Drawdown kill switch | -8%, stand down for the event |
| Naked short options | Structurally impossible |
| Credit floor | Never sell a spread for less than 18% of its width |
| Debit cap | Never pay more than 45% of width for convexity |

The naked-short gate is structural rather than advisory: a proposal is rejected
unless every short leg has a same-expiry, same-type long leg covering it. There
is no configuration flag that turns it off.

## Assignment safety

Alpaca begins auto-exercise and auto-assignment at 15:30 ET on expiration day,
and it evaluates each leg independently rather than treating a spread as a
package. A short leg that finishes in the money is assigned while an
out-of-the-money long leg simply expires, which leaves an unhedged stock
position. The manager flattens anything expiring that day at 15:00 ET.

## Running it

```bash
uv venv && uv pip install -e .
brew install alpacahq/tap/cli      # the execution path
cp .env.example .env               # add your paper account keys

.venv/bin/python -m engine.loop --once          # one pass
.venv/bin/python -m engine.loop --interval 300  # continuous
.venv/bin/python tests_risk.py                  # risk gate tests
```

`DRY_RUN=true` is the default. It builds and journals every order without
sending it, so you can watch a full session's decisions before risking a cent
of paper money.

## Profiles

Each profile is a separate Alpaca paper account with its own journal, so
strategy variants can be run side by side against the same tape.

```bash
ALPACA_PROFILE=main  STRATEGY_VARIANT=barbell     python -m engine.loop
ALPACA_PROFILE=test2 STRATEGY_VARIANT=convex_tilt python -m engine.loop
ALPACA_PROFILE=test3 STRATEGY_VARIANT=income_only python -m engine.loop
```

## License

MIT. See `LICENSE`.
