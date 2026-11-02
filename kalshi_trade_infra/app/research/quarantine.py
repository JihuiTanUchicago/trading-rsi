"""Research memory learned on data we later found leaky, kept out of what the proposer reads.

A family listed here had its backtests wrong before the given time (naive UTC). Its long-term
memory (rsi_sleep lessons and ledger), its playbook text and its recent drop lists from runs created
before then are hidden from the proposer and from the next consolidation, so the loop relearns that
family from clean runs only. Nothing is deleted: the rows stay in the database for audit, and
removing a family here brings them back.

    sports  2026-11-02: each sports contract was timed against its own settled close, which for
            early-closing totals, spreads and props is the moment its outcome was decided, so every
            spec's time-to-close window leaked the result (PR #839). The cut is after #839's deploy.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

QUARANTINE = {"sports": datetime(2026, 11, 2, 8, 0)}


def since(family: Optional[str]) -> Optional[datetime]:
    """The earliest time a run of this family may be learned from, or None when nothing is hidden."""
    return QUARANTINE.get(family or "")


def hides(family: Optional[str], created_at) -> bool:
    """True when something of `family` created at `created_at` was learned on leaky data."""
    cut = since(family)
    if cut is None or created_at is None:
        return False
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return False
    if getattr(created_at, "tzinfo", None) is not None:
        created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
    return created_at < cut
