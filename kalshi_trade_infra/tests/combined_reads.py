"""The page reads that ask several things in one statement, answered from the single reads a test fakes.

/api/me, the bell, Funds and the dashboard each ask in one statement what they used to ask in two or three
(repo.account_reads, notifications_first_page, kalshi_key_ids, ledger_rows_with_balance, book_versions). A test
that fakes the single reads (repo.get_stripe_customer, credit_balance, kalshi_keys_saved, list_notifications, ...)
calls compose_combined_reads(monkeypatch) so each combined read answers from those fakes, as the statement does
from the rows (the *_db.py tests check each statement against MySQL). Reads the test didn't fake read as before.
"""
from app import repository as repo


def _account_reads(cur, uid):
    live, demo = repo.kalshi_keys_saved(cur, uid)
    return {"live_key": live, "demo_key": demo, "customer": repo.get_stripe_customer(cur, uid),
            "credit_balance": repo.credit_balance(cur, uid)}


def _notifications_first_page(cur, uid, limit):
    return repo.list_notifications(cur, uid, limit=limit), repo.count_unread_notifications(cur, uid)


def _kalshi_key_ids(cur, uid):
    live, demo = repo.get_kalshi_key(cur, uid), repo.get_kalshi_dev_key(cur, uid)
    return {"live": live["key_id"] if live else None, "demo": demo["key_id"] if demo else None}


def _ledger_rows_with_balance(cur, uid, limit=repo.LEDGER_LISTED):
    rows = repo.ledger_rows(cur, uid) if limit == repo.LEDGER_LISTED else repo.ledger_rows(cur, uid, limit)
    return rows, repo.paper_balance(cur, uid)


def _book_versions(cur, uid):
    return repo.positions_version(cur, uid), repo.paper_ledger_version(cur, uid)


def compose_combined_reads(monkeypatch):
    for name, fn in (("account_reads", _account_reads), ("notifications_first_page", _notifications_first_page),
                     ("kalshi_key_ids", _kalshi_key_ids), ("ledger_rows_with_balance", _ledger_rows_with_balance),
                     ("book_versions", _book_versions)):
        monkeypatch.setattr(repo, name, fn)
