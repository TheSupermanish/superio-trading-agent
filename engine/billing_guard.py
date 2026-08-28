"""Billing guard.

A previous project on this account ran up real charges against a pay-as-you-go
billing account while a credits account sat unused. Attaching a project to the
wrong billing account is a single click in a console, it produces no error, and
nothing in an application would normally notice.

So the agent checks. Before it trades, it confirms that the Google Cloud project
it is about to send model calls to is attached to the exact billing account it
is supposed to spend from. A mismatch is fatal: the run stops rather than
quietly spending money somewhere unintended.

This is deliberately a hard failure and not a warning. A warning scrolls past.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass

from engine.config import SETTINGS

log = logging.getLogger(__name__)


@dataclass
class BillingStatus:
    project: str
    account_id: str | None
    expected: str
    enabled: bool
    checked: bool
    detail: str

    @property
    def ok(self) -> bool:
        """Unverifiable is not the same as wrong, but wrong is always wrong."""
        if not self.checked:
            return True
        if not self.enabled:
            return False
        return self.account_id == self.expected


def check(project: str | None = None, expected: str | None = None) -> BillingStatus:
    project = project or SETTINGS.vertex_project
    expected = (expected or SETTINGS.expected_billing_account or "").strip()

    if not project:
        return BillingStatus(
            project="", account_id=None, expected=expected, enabled=False, checked=False,
            detail="no Vertex project configured, so no cloud spend is possible",
        )
    if not expected:
        return BillingStatus(
            project=project, account_id=None, expected="", enabled=False, checked=False,
            detail="EXPECTED_BILLING_ACCOUNT is unset, so billing is not being enforced",
        )
    if shutil.which("gcloud") is None:
        return BillingStatus(
            project=project, account_id=None, expected=expected, enabled=False, checked=False,
            detail="gcloud not on PATH, cannot verify the billing account",
        )

    try:
        proc = subprocess.run(
            [
                "gcloud", "billing", "projects", "describe", project,
                "--format=value(billingAccountName,billingEnabled)",
            ],
            capture_output=True, text=True, timeout=45,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return BillingStatus(
            project=project, account_id=None, expected=expected, enabled=False, checked=False,
            detail=f"billing lookup failed: {exc}",
        )

    if proc.returncode != 0:
        return BillingStatus(
            project=project, account_id=None, expected=expected, enabled=False, checked=False,
            detail=f"billing lookup failed: {proc.stderr.strip()[:120]}",
        )

    parts = proc.stdout.strip().split()
    account = parts[0].replace("billingAccounts/", "") if parts else ""
    enabled = len(parts) > 1 and parts[1].lower() == "true"

    if not enabled:
        detail = f"billing is disabled on {project}; model calls would fail"
    elif account != expected:
        detail = (
            f"{project} bills to {account}, but this agent is only permitted to spend "
            f"from {expected}. Refusing to run."
        )
    else:
        detail = f"{project} bills to {account} as expected"

    return BillingStatus(
        project=project, account_id=account or None, expected=expected,
        enabled=enabled, checked=True, detail=detail,
    )
