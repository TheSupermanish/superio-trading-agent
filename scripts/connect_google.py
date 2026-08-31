#!/usr/bin/env python3
"""Manage connected Google accounts.

    python scripts/connect_google.py connect --label personal
    python scripts/connect_google.py list
    python scripts/connect_google.py events
    python scripts/connect_google.py tasks
    python scripts/connect_google.py disable --label work
    python scripts/connect_google.py remove --label work

Deliberately not named google.py: a script called google.py puts itself on
sys.path ahead of the real `google` namespace package, and every Google import
in the process then fails with "google is not a package".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Runnable as a plain script from anywhere, not only as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import google_accounts as ga  # noqa: E402


def cmd_list(_args: argparse.Namespace) -> int:
    rows = ga.status()
    if not rows:
        print("No Google accounts connected.")
        print("Connect one:  python scripts/connect_google.py connect --label personal")
        return 0
    print(f"{'LABEL':<14} {'EMAIL':<34} {'STATE':<9} {'TOKEN':<8} CALENDARS")
    for r in rows:
        state = "enabled" if r["enabled"] else "disabled"
        print(f"{r['label']:<14} {r['email'] or '-':<34} {state:<9} {r['token']:<8} {r['calendars']}")
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    try:
        account = ga.connect(args.label, port=args.port)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1
    print(f"Connected '{account.label}' as {account.email} ({len(account.calendars)} calendars)")
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    from engine.calendar_gate import classify

    events = ga.calendar_events(hours_ahead=args.hours, label=args.label)
    if not events:
        print("No upcoming events (or no account connected).")
        return 0
    for e in events:
        impact = classify(e["title"], e["description"]) or "ignored"
        print(f"  {str(e['start'])[:16]}  [{impact:<7}] {e['title'][:52]:<52} ({e['account']})")
    return 0


def cmd_tasks(args: argparse.Namespace) -> int:
    items = ga.tasks(label=args.label)
    if not items:
        print("No open tasks (or no account connected).")
        return 0
    for t in items:
        due = str(t.get("due") or "")[:10] or "no due date"
        print(f"  {due:<12} {t['title'][:56]:<56} ({t['account']}/{t['list']})")
    return 0


def cmd_toggle(args: argparse.Namespace) -> int:
    ok = ga.set_enabled(args.label, args.enable)
    print(f"{'enabled' if args.enable else 'disabled'} {args.label}" if ok
          else f"no account labelled {args.label}")
    return 0 if ok else 1


def cmd_remove(args: argparse.Namespace) -> int:
    ok = ga.remove(args.label)
    print(f"removed {args.label}" if ok else f"no account labelled {args.label}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Connected Google accounts")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("connect", help="authorise an account")
    p.add_argument("--label", required=True, help="short name, e.g. personal")
    p.add_argument("--port", type=int, default=0)
    p.set_defaults(fn=cmd_connect)

    sub.add_parser("list", help="show connected accounts").set_defaults(fn=cmd_list)

    p = sub.add_parser("events", help="upcoming calendar events")
    p.add_argument("--label"); p.add_argument("--hours", type=int, default=168)
    p.set_defaults(fn=cmd_events)

    p = sub.add_parser("tasks", help="open tasks")
    p.add_argument("--label"); p.set_defaults(fn=cmd_tasks)

    p = sub.add_parser("enable"); p.add_argument("--label", required=True)
    p.set_defaults(fn=cmd_toggle, enable=True)
    p = sub.add_parser("disable"); p.add_argument("--label", required=True)
    p.set_defaults(fn=cmd_toggle, enable=False)
    p = sub.add_parser("remove"); p.add_argument("--label", required=True)
    p.set_defaults(fn=cmd_remove)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
