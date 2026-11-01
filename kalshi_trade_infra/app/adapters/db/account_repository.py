"""The account's own rows that /api/me answers from, in one statement."""
from __future__ import annotations

from typing import Any, Dict

# The Stripe customer row's columns, as billing_repository.get_stripe_customer reads them.
_CUSTOMER_COLUMNS = ("user_id", "stripe_customer_id", "default_pm_id", "auto_reload", "reload_amount", "reload_threshold")


def account_reads(cur, user_id: int) -> Dict[str, Any]:
    """{"live_key", "demo_key", "customer", "credit_balance"} for /api/me, which every signed-in page's shell
    carries: whether each Kalshi key is saved (kalshi_keys_saved's two primary-key probes, selecting constants,
    never a key's id or its encrypted bytes), the Stripe customer row (get_stripe_customer's columns, None when
    there is none) and the credit balance (credit_balance's sum, a float). Three reads in one statement: each
    answers exactly as its own read does, and none waits on anything but our own database."""
    cur.execute(
        "SELECT EXISTS(SELECT 1 FROM app_kalshi_keys WHERE user_id=%s) AS live_key, "
        "EXISTS(SELECT 1 FROM app_kalshi_dev_keys WHERE user_id=%s) AS demo_key, "
        "(SELECT COALESCE(SUM(amount_usd),0) FROM app_credit_ledger WHERE user_id=%s) AS credit_balance, "
        "c.user_id IS NOT NULL AS has_customer, " + ", ".join("c." + k for k in _CUSTOMER_COLUMNS) + " "
        "FROM (SELECT 1 AS one) x LEFT JOIN app_stripe_customers c ON c.user_id=%s",
        (user_id, user_id, user_id, user_id),
    )
    row = cur.fetchone() or {}
    return {"live_key": bool(row.get("live_key")), "demo_key": bool(row.get("demo_key")),
            "customer": {k: row.get(k) for k in _CUSTOMER_COLUMNS} if row.get("has_customer") else None,
            "credit_balance": float(row.get("credit_balance") or 0)}


def kalshi_key_ids(cur, user_id: int) -> Dict[str, Any]:
    """{"live": the live key's id or None, "demo": the demo key's id or None}, for Funds, which says each key by a
    shortened id: both tables' rows by primary key in one statement, the id alone (never the encrypted key, which
    get_kalshi_key / get_kalshi_dev_key also read, for the paths that use it)."""
    cur.execute("SELECT 'live' AS book, key_id FROM app_kalshi_keys WHERE user_id=%s "
                "UNION ALL SELECT 'demo' AS book, key_id FROM app_kalshi_dev_keys WHERE user_id=%s", (user_id, user_id))
    out: Dict[str, Any] = {"live": None, "demo": None}
    for r in cur.fetchall():
        out[r["book"]] = r["key_id"]
    return out
