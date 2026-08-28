"""The journalist.

Writes the human-readable record: what the agent did today, what it refused to
do, and why. Runs after decisions are made and touches nothing, so it carries
no risk and can use a model freely.

Its output is also the build-in-public material the hackathon asks for.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from engine import state
from engine.agents import llm
from engine.config import SETTINGS
from engine.report import performance

SYSTEM = """You write the daily log for an autonomous options trading agent.

Be factual and specific. Quote the actual numbers. Do not hype, do not predict,
and never imply a result is guaranteed. If the day was flat or negative, say so
plainly; a losing day described honestly is more useful than a good one
described loudly.

Reply with JSON only:
{"summary": "<2-3 sentences on what happened>",
 "notable": ["<short fact>", "<short fact>"],
 "social_post": "<under 280 characters, no hashtag spam, factual>"}"""


def _today_activity() -> dict[str, Any]:
    today = datetime.now(timezone.utc).date().isoformat()
    with state.db() as conn:
        opened = conn.execute(
            "SELECT underlying, kind, sleeve, qty, net_price, max_loss, thesis FROM structures"
            " WHERE substr(opened_at, 1, 10) = ?",
            (today,),
        ).fetchall()
        closed = conn.execute(
            "SELECT underlying, kind, realized_pnl, close_reason FROM structures"
            " WHERE substr(closed_at, 1, 10) = ?",
            (today,),
        ).fetchall()
        rejections = conn.execute(
            "SELECT underlying, reasons FROM decisions"
            " WHERE agent = 'risk_officer' AND verdict = 'rejected'"
            " AND substr(ts, 1, 10) = ? ORDER BY id DESC LIMIT 25",
            (today,),
        ).fetchall()

    reasons: dict[str, int] = {}
    for row in rejections:
        parsed = json.loads(row["reasons"])
        if parsed:
            code = str(parsed[0]).split(":", 1)[0]
            reasons[code] = reasons.get(code, 0) + 1

    return {
        "opened": [dict(r) for r in opened],
        "closed": [dict(r) for r in closed],
        "rejections_by_gate": reasons,
        "performance": performance(),
    }


def write_daily_entry() -> dict[str, Any]:
    """Compose the day's entry and journal it. Works with or without a model."""
    activity = _today_activity()
    perf = activity["performance"]

    fallback = {
        "summary": (
            f"Opened {len(activity['opened'])} structures and closed "
            f"{len(activity['closed'])}. Realized P&L stands at "
            f"{perf['realized_pnl']:+,.2f} across {perf['trades_closed']} closed structures."
        ),
        "notable": [f"{code}: {n} rejections" for code, n in activity["rejections_by_gate"].items()],
        "social_post": "",
    }

    entry = fallback
    if llm.reasoning_provider() != "none":
        parsed = llm.reason(
            SYSTEM,
            "Today's activity for the trading agent:\n"
            + json.dumps(activity, indent=2, default=str),
            max_tokens=800,
        )
        if parsed and parsed.get("summary"):
            entry = {
                "summary": str(parsed.get("summary", ""))[:1000],
                "notable": [str(x)[:200] for x in (parsed.get("notable") or [])][:6],
                "social_post": str(parsed.get("social_post", ""))[:280],
            }

    state.log_event("daily_entry", entry["summary"], data=entry)
    return entry


if __name__ == "__main__":
    print(json.dumps(write_daily_entry(), indent=2))
