"""The home page's combined reads say what the reads they stand for say, against a real MySQL. Opt in as for
test_app_engine_db.py (APP_TEST_MYSQL=1).

A signed-in page's shell and the dashboard ask in one statement what they used to ask in two or three:
  - repo.account_reads (/api/me): kalshi_keys_saved, get_stripe_customer and credit_balance;
  - repo.notifications_first_page (the bell, on every shell): list_notifications' first page and count_unread_notifications;
  - repo.kalshi_key_ids (Funds): the key ids get_kalshi_key and get_kalshi_dev_key read;
  - repo.ledger_rows_with_balance (Funds): ledger_rows and paper_balance;
  - repo.book_versions (the dashboard): positions_version and paper_ledger_version;
  - repo.activity_since(active=True) (the dashboard): active_run_counts, with and without the silent line;
  - repo.session_user carries demo_account's capital and baseline.
Each is checked for accounts with nothing, some and more than a page, beside another account's rows. Then the
dashboard shell end to end: what it carries (the account, the dashboard, the autopilot status, the bell, sharing
what /api/me read) is byte for byte what each endpoint answers alone, in fewer statements.
"""
import json
import os
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(os.getenv("APP_TEST_MYSQL") != "1", reason="needs APP_TEST_MYSQL=1 and a MySQL")


@pytest.fixture(scope="module")
def conn():
    from app import schema
    from data_pipeline.common import get_db_conn

    schema.main()
    c = get_db_conn()
    c.autocommit(True)
    yield c
    c.close()


def _user(cur, name="home"):
    from app import repository as repo

    return repo.upsert_google_user(cur, "test:" + uuid.uuid4().hex, "%s@example.invalid" % name, name.title(), None)["id"]


def _book(cur, uid, closed=3, open_=2, mode="paper"):
    """A strategy, a deployment and its positions, with the paper ledger rows they book."""
    from app import repository as repo

    sid = repo.insert_strategy(cur, uid, None, None, "btc15m", "momentum", "Fade tails", "t",
                               {"side": "yes", "sizing": {"stake": 5.0}}, "passed", {"capital": 10.0})
    dep = repo.create_deployment(cur, uid, sid, "btc15m", mode)
    now = repo.now_utc()
    for k in range(closed):
        pid = repo.insert_closed_position(cur, dep, uid, mode, "KXBTC15M-T%d" % k, "yes", 3, 0.4, 0.02, 1.0, 0.0, 1.78,
                                          "settled", now - timedelta(hours=k + 1), order_ref="sim:c%d-%d" % (dep, k),
                                          opened_at=now - timedelta(hours=k + 2), closed_at=now - timedelta(hours=k + 1))
        repo.add_ledger(cur, uid, "trade", -1.22, "Bought", pid)
        repo.add_ledger(cur, uid, "settlement", 3.0, "Settled", pid)
    for k in range(open_):
        repo.insert_position(cur, dep, uid, mode, "KXBTC15M-O%d" % k, "yes", 2, 0.5, 0.01, now + timedelta(hours=1),
                             order_ref="sim:o%d-%d" % (dep, k))
    return sid, dep


def test_account_reads_say_what_the_single_reads_say(conn):
    from app import repository as repo

    cur = conn.cursor()
    other = _user(cur, "other")
    repo.save_kalshi_key(cur, other, "LIVE-placeholder-o", b"placeholder-not-a-key")
    repo.set_kalshi_dev_key(cur, other, "DEMO-placeholder-o", b"placeholder-not-a-key")
    repo.save_stripe_customer(cur, other, "cus_other")
    repo.add_credit(cur, other, "topup", 99, "Card top-up", "pi_%s" % uuid.uuid4().hex)
    for live in (True, False):
        for demo in (True, False):
            for card in (None, "customer", "card", "reload"):
                uid = _user(cur)
                if live:
                    repo.save_kalshi_key(cur, uid, "LIVE-placeholder-%d" % uid, b"placeholder-not-a-key")
                if demo:
                    repo.set_kalshi_dev_key(cur, uid, "DEMO-placeholder-%d" % uid, b"placeholder-not-a-key")
                if card:
                    repo.save_stripe_customer(cur, uid, "cus_%d" % uid)
                if card in ("card", "reload"):
                    repo.set_default_pm(cur, uid, "pm_%d" % uid)
                if card == "reload":
                    repo.set_auto_reload(cur, uid, True, 30, 8)
                    repo.add_credit(cur, uid, "usage", -0.123456, "Research session")
                got = repo.account_reads(cur, uid)
                assert got == {"live_key": repo.kalshi_keys_saved(cur, uid)[0], "demo_key": repo.kalshi_keys_saved(cur, uid)[1],
                               "customer": repo.get_stripe_customer(cur, uid), "credit_balance": repo.credit_balance(cur, uid)}
                assert (got["live_key"], got["demo_key"]) == (live, demo) and (got["customer"] is None) is (card is None)
                assert type(got["credit_balance"]) is float


def test_the_bells_first_page_carries_the_unread_count_in_one_statement(conn):
    from app import repository as repo
    from app.services import bell

    cur = conn.cursor()
    other = _user(cur, "other")
    repo.add_notification(cur, other, "paused", "Theirs.", "/deploy")
    for n, read in ((0, 0), (3, 0), (3, 3), (5, 2), (bell.READ_ROWS + 12, 7)):
        uid = _user(cur)
        sid = repo.insert_strategy(cur, uid, None, None, "btc15m", "momentum", "Fade tails", "t", {}, "passed", None)
        for k in range(n):
            repo.add_notification(cur, uid, "passed" if k % 2 else "paused", "Notice %d." % k,
                                  "/strategies/%d" % (sid if k % 3 else sid + 100000))
        if read:
            cur.execute("UPDATE app_notifications SET read_at=%s WHERE user_id=%s ORDER BY id LIMIT %s",
                        (repo.now_utc(), uid, read))
        rows, unread = repo.notifications_first_page(cur, uid, bell.READ_ROWS + 1)
        assert rows == repo.list_notifications(cur, uid, limit=bell.READ_ROWS + 1)
        assert unread == repo.count_unread_notifications(cur, uid) == n - read
        assert all("unread_all" not in r for r in rows)


def test_funds_key_ids_and_ledger_with_balance_say_what_the_single_reads_say(conn):
    from app import repository as repo

    cur = conn.cursor()
    other = _user(cur, "other")
    repo.save_kalshi_key(cur, other, "LIVE-placeholder-o", b"placeholder-not-a-key")
    repo.add_ledger(cur, other, "deposit", 500, "Paper money")
    for live in (True, False):
        for demo in (True, False):
            uid = _user(cur)
            cur.execute("DELETE FROM app_ledger WHERE user_id=%s", (uid,))  # a new account's starting deposit, gone
            if live:
                repo.save_kalshi_key(cur, uid, "LIVE-placeholder-%d" % uid, b"placeholder-not-a-key")
            if demo:
                repo.set_kalshi_dev_key(cur, uid, "DEMO-placeholder-%d" % uid, b"placeholder-not-a-key")
            k, d = repo.get_kalshi_key(cur, uid), repo.get_kalshi_dev_key(cur, uid)
            assert repo.kalshi_key_ids(cur, uid) == {"live": k["key_id"] if k else None, "demo": d["key_id"] if d else None}
            empty = repo.ledger_rows_with_balance(cur, uid)
            assert empty == (repo.ledger_rows(cur, uid), repo.paper_balance(cur, uid), 0) and not empty[0] and empty[1] == 0.0
            n = repo.LEDGER_LISTED + 7 if live else 3
            for i in range(n):
                repo.add_ledger(cur, uid, "deposit" if i == 0 else "trade", 1000 if i == 0 else -1.2345, "Row %d" % i)
            rows, balance, entries = repo.ledger_rows_with_balance(cur, uid)
            assert rows == repo.ledger_rows(cur, uid) and balance == repo.paper_balance(cur, uid) and type(balance) is float
            assert len(rows) == min(repo.LEDGER_LISTED, n)
            # Every entry the user's ledger holds, never the page listed nor another account's (`other` has one).
            assert entries == n and type(entries) is int


def test_book_versions_say_what_each_version_says(conn):
    from app import repository as repo

    cur = conn.cursor()
    other = _user(cur, "other")
    _book(cur, other)
    for closed, open_ in ((0, 0), (0, 2), (4, 0), (6, 3)):
        uid = _user(cur)
        cur.execute("DELETE FROM app_ledger WHERE user_id=%s", (uid,))
        if closed or open_:
            _book(cur, uid, closed, open_)
        assert repo.book_versions(cur, uid) == (repo.positions_version(cur, uid), repo.paper_ledger_version(cur, uid))
    assert repo.book_versions(cur, uid)[0] == (9, 6, repo.positions_version(cur, uid)[2])


def test_activity_since_counts_the_runs_at_work_as_active_run_counts_does(conn):
    from app import repository as repo

    cur = conn.cursor()
    other = _user(cur, "other")
    repo.create_run(cur, other, "research", "btc15m")
    now = repo.now_utc()
    uid = _user(cur)
    since = now - timedelta(days=1)
    for silent in (None, now - timedelta(minutes=10)):
        got = repo.activity_since(cur, uid, since, silent_before=silent, active=True)
        assert got["active"] == repo.active_run_counts(cur, uid, silent_before=silent)
    for kind, status, beat in (("research", "running", now), ("research", "running", now - timedelta(hours=1)),
                               ("research", "queued", None), ("research", "paused", None), ("chat", "running", now),
                               ("review", "queued", None), ("research", "succeeded", None), ("research", "failed", None)):
        rid = repo.create_run(cur, uid, kind, "btc15m" if kind == "research" else None)
        cur.execute("UPDATE app_agent_runs SET status=%s, heartbeat_at=%s, started_at=%s WHERE id=%s", (status, beat, beat, rid))
    for silent in (None, now - timedelta(minutes=10)):
        plain = repo.activity_since(cur, uid, since, silent_before=silent)
        got = repo.activity_since(cur, uid, since, silent_before=silent, active=True)
        assert got.pop("active") == repo.active_run_counts(cur, uid, silent_before=silent)
        assert got == plain  # the rest of the strip's read is unchanged
    assert repo.activity_since(cur, uid, since, silent_before=now - timedelta(minutes=10), active=True)["active"] == {
        "research_running": 1, "research_paused": 1, "research_queued": 1, "chat_active": 2}


def test_the_session_row_carries_the_demo_account(conn):
    from app import repository as repo

    cur = conn.cursor()
    uid = _user(cur)
    for capital in (None, 250.0):
        if capital is not None:
            repo.reload_demo(cur, uid, capital)
        token = "home-" + uuid.uuid4().hex
        repo.create_session(cur, uid, token, timedelta(hours=1))
        row = repo.session_user(cur, token)
        assert (float(row["demo_capital"]), float(row["demo_pnl_base"])) == repo.demo_account(cur, uid)


class _Counting:
    def __init__(self, c, sent):
        self._c, self._sent = c, sent

    def execute(self, sql, args=None):
        self._sent.append(" ".join(str(sql).split()))
        return self._c.execute(sql, args)

    def __getattr__(self, name):
        return getattr(self._c, name)


def test_the_dashboard_shell_carries_what_each_endpoint_answers_alone_in_fewer_statements(conn, monkeypatch):
    from app import repository as repo, server
    from app.adapters.stripe import billing
    from app.services import book_memo
    from app.web import shell_reads
    from app.web.routes import dashboard as dashboard_routes

    import sys

    from app.adapters.db import sql

    # One clock for every module that took sql.now_utc (the facade's patch would miss the split modules).
    # Compared with the real clock taken first: compared with sql.now_utc, only sql itself was frozen (patched first,
    # it no longer matched the rest), so "since" read the wall clock and differed by a second when the endpoints
    # answered alone in one second and the shell in the next.
    real = sql.now_utc
    fixed = real().replace(microsecond=0)
    for m in list(sys.modules.values()):
        if m is not None and getattr(m, "now_utc", None) is real:
            monkeypatch.setattr(m, "now_utc", lambda: fixed)
    assert repo.now_utc() == fixed  # the facade the routes read, frozen too
    monkeypatch.setattr(dashboard_routes, "_freshness", lambda cur: {"engine": None})
    monkeypatch.setattr(billing, "STRIPE_SECRET_KEY", "sk_test_home")
    cur = conn.cursor()
    uid = _user(cur)
    cur.execute("UPDATE app_users SET autopilot=1 WHERE id=%s", (uid,))
    _book(cur, uid, closed=5, open_=2)
    _book(cur, uid, closed=2, open_=1, mode="demo")
    repo.set_kalshi_dev_key(cur, uid, "DEMO-placeholder-%d" % uid, b"placeholder-not-a-key")
    repo.save_stripe_customer(cur, uid, "cus_%d" % uid)
    repo.set_default_pm(cur, uid, "pm_%d" % uid)
    for k in range(4):
        repo.add_notification(cur, uid, "paused", "Notice %d." % k, "/deploy")
    token = "home-" + uuid.uuid4().hex
    repo.create_session(cur, uid, token, timedelta(hours=1))
    real = conn.cursor
    sent = []
    monkeypatch.setattr(conn, "cursor", lambda: _Counting(real(), sent))
    handler = SimpleNamespace(headers={}, client_address=("127.0.0.1", 0))

    def alone(fn):
        book_memo.clear()
        user = repo.session_user(conn.cursor(), token)
        return json.dumps(fn(server.Ctx(conn, user, (), {}, {}, handler)), default=server._json_default, sort_keys=True)

    want = {"me": alone(server.me), "/api/dashboard": alone(server.dashboard),
            "/api/autopilot/status": alone(server.autopilot_status), "/api/notifications": alone(server.notifications)}
    book_memo.clear()
    sent.clear()
    user = repo.session_user(conn.cursor(), token)
    shared = {}
    account = server.me(server.Ctx(conn, user, (), {}, {}, handler, shared=shared))
    first, _ = shell_reads.first_reads("/", "", {}, lambda p, q: server.Ctx(conn, user, p, q, {}, handler, shared=shared))
    got = {"me": json.dumps(account, default=server._json_default, sort_keys=True)}
    got.update({r["url"]: json.dumps(r["body"], default=server._json_default, sort_keys=True) for r in first})
    assert got == want
    # The account read once: no key, card or balance statement after it, and no repeated statement at all.
    assert len([s for s in sent if "app_stripe_customers" in s]) == 1, sent
    assert not [s for s in sent if s.startswith("SELECT 1 AS present FROM app_kalshi")], sent
    assert not [s for s in sent if s.startswith("SELECT demo_capital")], sent
    assert len(sent) == len(set(sent)), sorted(s for s in sent if sent.count(s) > 1)
