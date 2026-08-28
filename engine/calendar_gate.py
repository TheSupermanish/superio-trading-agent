"""Scheduled-event gate.

Selling defined-risk premium across a known binary event is selling insurance
at the exact moment the insured accident is scheduled. Buying convexity into
one is the opposite trade, and it is the point of the convex sleeve.

So this gate is asymmetric on purpose: it blocks new CREDIT structures whose
expiry sits on the far side of a high-impact event, and it lets convex
structures through.

Two events land inside the competition window:

* Broadcom reports Wednesday 2 September after the close. It is a top-five QQQ
  weight, so a QQQ short structure expiring Thursday or later is short a
  scheduled gap.
* The August employment report lands Friday 4 September at 08:30 ET, ninety
  minutes before the market opens on the final session and roughly two and a
  half hours before the judging mark. Nasdaq-100 one-day straddles have
  underpriced the realized payrolls move in ten of the last twelve reports, so
  the agent wants to be long gamma into it, not short.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Event:
    name: str
    when: datetime
    impact: str               # high | medium
    affects: tuple[str, ...]  # underlyings, or ("*",) for the whole tape

    def touches(self, underlying: str) -> bool:
        return "*" in self.affects or underlying in self.affects


def _et(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ET)


#: Hand-maintained for the competition window. A production system would pull
#: this from an economic calendar API; for a seven-day event, hard-coding the
#: known catalysts is more reliable than depending on a third-party feed.
EVENTS: tuple[Event, ...] = (
    Event("ISM Manufacturing PMI", _et(2026, 9, 1, 10, 0), "medium", ("*",)),
    Event("JOLTS job openings", _et(2026, 9, 1, 10, 0), "medium", ("*",)),
    Event("Broadcom Q3 earnings", _et(2026, 9, 2, 16, 30), "high", ("QQQ",)),
    Event("Initial jobless claims", _et(2026, 9, 3, 8, 30), "medium", ("*",)),
    Event("ISM Services PMI", _et(2026, 9, 3, 10, 0), "medium", ("*",)),
    Event("August employment report", _et(2026, 9, 4, 8, 30), "high", ("*",)),
)

#: How far ahead of a high-impact event new short premium stops being written.
HIGH_IMPACT_BLACKOUT = timedelta(hours=6)
MEDIUM_IMPACT_BLACKOUT = timedelta(hours=2)


def _blackout_for(event: Event) -> timedelta:
    return HIGH_IMPACT_BLACKOUT if event.impact == "high" else MEDIUM_IMPACT_BLACKOUT


def upcoming(now: datetime | None = None, horizon_hours: int = 48) -> list[Event]:
    now = now or datetime.now(ET)
    limit = now + timedelta(hours=horizon_hours)
    return [e for e in EVENTS if now <= e.when <= limit]


def events_before(expiry: date, underlying: str, now: datetime | None = None) -> list[Event]:
    """High and medium impact events between now and the close on `expiry`."""
    now = now or datetime.now(ET)
    expiry_close = datetime.combine(expiry, dt_time(16, 0), tzinfo=ET)
    return [e for e in EVENTS if now < e.when <= expiry_close and e.touches(underlying)]


def check(
    sleeve: str, underlying: str, expiry: date, is_credit: bool, now: datetime | None = None
) -> tuple[bool, str]:
    """Returns (allowed, reason).

    Convex structures are always allowed: being long gamma into a scheduled
    catalyst is the trade, not the hazard.
    """
    now = now or datetime.now(ET)

    if not is_credit or sleeve == "convex":
        pending = events_before(expiry, underlying, now)
        if pending:
            names = ", ".join(e.name for e in pending)
            return True, f"long gamma into {names}"
        return True, "no scheduled catalyst before expiry"

    for event in events_before(expiry, underlying, now):
        if event.impact != "high":
            continue
        if now >= event.when - _blackout_for(event):
            return False, (
                f"{event.name} at {event.when:%a %H:%M} ET lands before this expiry and we are "
                f"inside the {_blackout_for(event).seconds // 3600}h blackout"
            )
        return False, (
            f"{event.name} at {event.when:%a %H:%M} ET lands before this expiry; "
            f"not writing premium across it"
        )

    return True, "no high-impact event before expiry"
