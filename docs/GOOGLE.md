# Connecting Google accounts

Several accounts, each under a label you choose. Calendar entries become
catalysts in gate G6; Tasks appear on the dashboard.

Read-only throughout. The agent never writes to a calendar and never sends mail.

## One-time setup

1. In the [Google Cloud console](https://console.cloud.google.com/apis/credentials),
   pick a project and enable the **Google Calendar API** and the **Google Tasks API**.
2. Credentials → Create credentials → **OAuth client ID** → **Desktop app**.
3. Download the JSON and save it as `client_secret.json` in the repo root.
   It is git-ignored.

That client is shared by every account. You do this once, not once per account.

## Connecting accounts

```bash
python scripts/connect_google.py connect --label personal
python scripts/connect_google.py connect --label work
python scripts/connect_google.py list
```

Each opens a browser once. Tokens land in `data/google/<label>.json`, owner-read
only, git-ignored.

```
LABEL          EMAIL                              STATE     TOKEN    CALENDARS
personal       you@gmail.com                      enabled   valid    4
work           you@company.com                    enabled   valid    9
```

## How calendar entries become catalysts

Not every meeting should gate a trade, so entries are filtered. An entry counts
as a catalyst if its title or description matches a known market event, and you
can always override that explicitly.

| In the entry | Result |
| --- | --- |
| `[high]` anywhere | High impact, whatever else it says |
| `[medium]` anywhere | Medium impact |
| `[ignore]` anywhere | Ignored, even if it matches a keyword |
| FOMC, CPI, NFP, payrolls, earnings, opex, Powell | High impact |
| ISM, PMI, JOLTS, claims, GDP, PCE, retail sales | Medium impact |
| Anything else | Ignored |

Name a ticker in the title and the catalyst applies only to that underlying:
`QQQ opex` gates QQQ alone, while `FOMC decision` gates everything.

Then gate G6 does what it already did: refuses to write premium into a
high-impact catalyst, and lets convex structures through, because being long
gamma into a scheduled event is the trade rather than the hazard.

## Day to day

```bash
python scripts/connect_google.py events      # what the agent will treat as catalysts
python scripts/connect_google.py tasks       # open tasks across all accounts
python scripts/connect_google.py disable --label work
python scripts/connect_google.py remove --label work
```

## Failure behaviour

Deliberate and one-directional: connected calendars can only ever **add** gates.
If no account is connected, a token expires, or Google is unreachable, the
built-in catalyst calendar still applies in full. An outage can never remove a
risk gate, only fail to add to it. Reads are cached for twenty minutes so the
trading loop never waits on Google.
