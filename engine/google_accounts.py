"""Connected Google accounts.

Several accounts, each under a label you choose, so a personal calendar and a
work calendar can both feed the agent and every event still says where it came
from. Calendar drives the catalyst blackout gate; Tasks carries operational
to-dos onto the dashboard.

Read-only scopes only. This never writes to a calendar and never sends mail.

Tokens live in `data/google/<label>.json` with owner-only permissions and are
git-ignored. The OAuth client itself is shared across accounts: authorise it
once per account and each gets its own refresh token.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from engine.config import ROOT

log = logging.getLogger(__name__)

STORE = ROOT / "data" / "google"
REGISTRY = STORE / "accounts.json"

#: Read-only throughout. Widening this list is a deliberate act, not a default.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

#: Where the OAuth client lives. One desktop client serves every account.
CLIENT_SECRET = ROOT / "client_secret.json"


@dataclass
class Account:
    label: str
    email: str = ""
    enabled: bool = True
    connected_at: str = ""
    calendars: list[str] = field(default_factory=list)

    @property
    def token_path(self) -> Path:
        return STORE / f"{self.label}.json"

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "email": self.email,
            "enabled": self.enabled,
            "connected_at": self.connected_at,
            "calendars": self.calendars,
        }


# --- registry --------------------------------------------------------------

def _load_registry() -> list[Account]:
    if not REGISTRY.exists():
        return []
    try:
        raw = json.loads(REGISTRY.read_text())
    except json.JSONDecodeError:
        log.warning("google account registry is corrupt; treating as empty")
        return []
    return [Account(**entry) for entry in raw.get("accounts", [])]


def _save_registry(accounts: list[Account]) -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(
        json.dumps({"accounts": [a.as_dict() for a in accounts]}, indent=2)
    )
    os.chmod(REGISTRY, stat.S_IRUSR | stat.S_IWUSR)


def accounts(enabled_only: bool = False) -> list[Account]:
    found = _load_registry()
    return [a for a in found if a.enabled] if enabled_only else found


def get(label: str) -> Account | None:
    return next((a for a in _load_registry() if a.label == label), None)


def set_enabled(label: str, enabled: bool) -> bool:
    found = _load_registry()
    for account in found:
        if account.label == label:
            account.enabled = enabled
            _save_registry(found)
            return True
    return False


def remove(label: str) -> bool:
    """Forget an account and delete its token."""
    found = _load_registry()
    target = next((a for a in found if a.label == label), None)
    if target is None:
        return False
    target.token_path.unlink(missing_ok=True)
    _save_registry([a for a in found if a.label != label])
    return True


# --- credentials -----------------------------------------------------------

def _credentials(account: Account):
    """Load and refresh one account's credentials, or None if unusable."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not account.token_path.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(account.token_path), SCOPES)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not load credentials for %s: %s", account.label, exc)
        return None

    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _write_token(account, creds)
            return creds
        except Exception as exc:  # noqa: BLE001
            log.warning("refresh failed for %s: %s", account.label, exc)
            return None
    return None


def _write_token(account: Account, creds: Any) -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    account.token_path.write_text(creds.to_json())
    os.chmod(account.token_path, stat.S_IRUSR | stat.S_IWUSR)


def connect(label: str, port: int = 0) -> Account:
    """Authorise one account. Opens a browser; needs a human once per account."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CLIENT_SECRET.exists():
        raise FileNotFoundError(
            f"No OAuth client at {CLIENT_SECRET}. Create a Desktop OAuth client in the "
            f"Google Cloud console (APIs & Services -> Credentials), enable the Google "
            f"Calendar and Google Tasks APIs, then save the download as client_secret.json"
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=port, prompt="consent")

    account = get(label) or Account(label=label)
    _write_token(account, creds)
    account.connected_at = datetime.now(timezone.utc).isoformat()
    account.email = _whoami(creds) or account.email
    account.calendars = [c["id"] for c in _calendar_list(creds)]
    account.enabled = True

    found = [a for a in _load_registry() if a.label != label]
    found.append(account)
    _save_registry(found)
    log.info("connected %s as %s", label, account.email or "unknown")
    return account


def _whoami(creds: Any) -> str:
    try:
        from googleapiclient.discovery import build

        info = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        return str(info.userinfo().get().execute().get("email", ""))
    except Exception:  # noqa: BLE001
        return ""


def _calendar_list(creds: Any) -> list[dict[str, Any]]:
    try:
        from googleapiclient.discovery import build

        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return service.calendarList().list().execute().get("items", [])
    except Exception as exc:  # noqa: BLE001
        log.warning("could not list calendars: %s", exc)
        return []


# --- reads -----------------------------------------------------------------

def calendar_events(
    hours_ahead: int = 168, label: str | None = None
) -> list[dict[str, Any]]:
    """Upcoming events across every enabled account, each tagged with its label."""
    from googleapiclient.discovery import build

    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=hours_ahead)
    out: list[dict[str, Any]] = []

    for account in accounts(enabled_only=True):
        if label and account.label != label:
            continue
        creds = _credentials(account)
        if creds is None:
            continue
        try:
            service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            calendars = account.calendars or ["primary"]
            for calendar_id in calendars:
                items = service.events().list(
                    calendarId=calendar_id,
                    timeMin=now.isoformat(),
                    timeMax=window_end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=50,
                ).execute().get("items", [])
                for item in items:
                    start = item.get("start", {})
                    out.append(
                        {
                            "account": account.label,
                            "calendar": calendar_id,
                            "title": item.get("summary", "(untitled)"),
                            "description": (item.get("description") or "")[:300],
                            "start": start.get("dateTime") or start.get("date"),
                            "all_day": "date" in start and "dateTime" not in start,
                        }
                    )
        except Exception as exc:  # noqa: BLE001 - never break the trading loop
            log.warning("calendar read failed for %s: %s", account.label, exc)

    out.sort(key=lambda e: str(e.get("start") or ""))
    return out


def tasks(label: str | None = None) -> list[dict[str, Any]]:
    """Open tasks across every enabled account."""
    from googleapiclient.discovery import build

    out: list[dict[str, Any]] = []
    for account in accounts(enabled_only=True):
        if label and account.label != label:
            continue
        creds = _credentials(account)
        if creds is None:
            continue
        try:
            service = build("tasks", "v1", credentials=creds, cache_discovery=False)
            for task_list in service.tasklists().list(maxResults=20).execute().get("items", []):
                items = service.tasks().list(
                    tasklist=task_list["id"], showCompleted=False, maxResults=50
                ).execute().get("items", [])
                for item in items:
                    out.append(
                        {
                            "account": account.label,
                            "list": task_list.get("title", ""),
                            "title": item.get("title", "(untitled)"),
                            "due": item.get("due"),
                            "notes": (item.get("notes") or "")[:200],
                        }
                    )
        except Exception as exc:  # noqa: BLE001
            log.warning("tasks read failed for %s: %s", account.label, exc)

    out.sort(key=lambda t: str(t.get("due") or "9999"))
    return out


def status() -> list[dict[str, Any]]:
    """What is connected and whether each token still works."""
    rows = []
    for account in accounts():
        creds = _credentials(account) if account.token_path.exists() else None
        rows.append(
            {
                "label": account.label,
                "email": account.email,
                "enabled": account.enabled,
                "token": "valid" if creds else ("expired" if account.token_path.exists() else "missing"),
                "calendars": len(account.calendars),
                "connected_at": account.connected_at,
            }
        )
    return rows
