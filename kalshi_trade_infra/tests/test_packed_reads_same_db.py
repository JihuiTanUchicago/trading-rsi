"""Two reads that now bring their rows back as text answer exactly as the reads they replace, against a real MySQL.
Opt in as for test_app_engine_db.py (APP_TEST_MYSQL=1).

GET /api/billing/receipt read the user row, the balance, the total spent and then every ledger row with its DATETIME
and DECIMAL parsed by pymysql (most of a 9,000-row receipt). repo.credit_history reads the ledger once, its columns
but the memo packed as text, and sums the balance and total spent from the same rows. The file must be byte for byte
the one the old reads wrote, on a ledger holding every awkward case: half-up amounts, a debit under a hundredth of a
cent, usage with no run and with a run that is gone, a Stripe reference with a comma, NULL and multi-line memos,
admin grants both ways, and usage that nets to zero (spent says 0.0000, never -0.0000).

repo.paper_round_trips_by_strategy (the Deploy board's road to live, the research scoreboard and the tournament) now
brings each round trip's sum, times and ticker back packed as text: the same lists, in the same order, of the same
floats, datetimes and strings as the read it replaces and the gate's own per-strategy read.
"""
import os
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(os.getenv("APP_TEST_MYSQL") != "1", reason="needs APP_TEST_MYSQL=1 and a MySQL")

NOW = datetime(2026, 10, 26, 12, 0, 0)
# The receipt's ledger read before (billing_repository.credit_rows).
OLD_LEDGER_SQL = ("SELECT l.kind, l.amount_usd, l.memo, l.created_at, l.run_id, l.stripe_ref, r.kind AS run_kind, "
                  "r.brief AS run_brief FROM app_credit_ledger l LEFT JOIN app_agent_runs r ON r.id = l.run_id "
                  "WHERE l.user_id=%s ORDER BY l.id DESC LIMIT %s")
# paper_round_trips_by_strategy's read before, and how it made each trip.
OLD_TRIPS_SQL = """SELECT d.strategy_id, p.order_ref, SUM(p.pnl) AS pnl, MIN(p.opened_at) AS opened_at,
                          MAX(p.closed_at) AS closed_at, MAX(p.ticker) AS ticker
                   FROM app_deployments d STRAIGHT_JOIN app_positions p FORCE INDEX (idx_pos_dep) ON p.deployment_id=d.id
                   WHERE p.user_id=%s AND p.mode='paper' AND p.order_ref LIKE %s AND d.strategy_id IN ({marks})
                   GROUP BY d.strategy_id, p.order_ref
                   HAVING SUM(CASE WHEN p.status='open' THEN 1 ELSE 0 END)=0
                   ORDER BY d.strategy_id, MIN(p.opened_at)"""


@pytest.fixture(scope="module")
def conn():
    from app import schema
    from data_pipeline.common import get_db_conn

    schema.main()
    c = get_db_conn()
    yield c
    c.cursor().execute("UPDATE app_deployments SET status='stopped' WHERE user_id IN "
                       "(SELECT id FROM app_users WHERE google_sub LIKE 'test:%%')")
    c.close()


def _user(cur, label):
    from app import repository as repo

    return repo.upsert_google_user(cur, "test:" + uuid.uuid4().hex, "%s-%s@example.invalid" % (label, uuid.uuid4().hex[:6]),
                                   label, None)


def _credit(cur, uid, kind, amount, memo, at, run_id=None, ref=None):
    cur.execute("INSERT INTO app_credit_ledger (user_id, kind, amount_usd, memo, stripe_ref, run_id, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)", (uid, kind, amount, memo, ref, run_id, at))


def _ledger_account(cur):
    from app import repository as repo

    user = _user(cur, "ledger")
    uid = user["id"]
    runs = {}
    for name, kind, brief in (("research", "research", False), ("auto", "research", True), ("review", "review", True), ("chat", "chat", False)):
        runs[name] = repo.create_run(cur, uid, kind, "btc15m" if kind == "research" else None, None, "p", brief=brief)
    gone = repo.create_run(cur, uid, "research", "btc15m", None, "p")
    t = NOW - timedelta(days=20)
    model = "Agent usage · us.anthropic.claude-opus-4-6-v1"
    for k, (kind, amount, memo, run, ref) in enumerate([
            ("usage", "-4.666650", model, runs["research"], None), ("usage", "-0.000040", model, runs["auto"], None),
            ("usage", "-0.123450", "exp-4471 · job:hypo_gen_v2", runs["review"], None), ("usage", "-0.010000", model, runs["chat"], None),
            ("usage", "-0.250000", model, None, None), ("usage", "-1.000000", model, gone, None),
            ("usage", "0.500000", "Refund, of a call", runs["research"], None),
            ("topup", "10.000000", "Added $10 credits", None, "pi_%s,with,commas" % uuid.uuid4().hex[:8]),
            ("autoreload", "25.000000", "Auto-reload $25", None, "pi_%s" % uuid.uuid4().hex[:8]),
            ("admin_grant", "5.000000", "Admin grant by staff@example.invalid", None, None),
            ("admin_grant", "-2.000000", "Admin grant by staff@example.invalid", None, None),
            ("promo_credit", "3.000000", None, None, None), ("bonus", "1.000000", "Line one,\nline two", None, None)]):
        _credit(cur, uid, kind, amount, memo, t + timedelta(hours=k), run, ref)
    cur.execute("DELETE FROM app_agent_runs WHERE id=%s", (gone,))  # a debit whose run is gone: no run kind
    return user


def _receipt(user, cur, monkeypatch, old):
    """The receipt download, with the moment it was made (its Generated line and the file name's day) left out."""
    import re

    from app import repository as repo
    from app import server

    if old:  # the old reads: the user row, the two totals and the ledger with pymysql's own types
        monkeypatch.setattr(repo, "credit_history", lambda c, uid, limit: {"rows": repo.credit_rows(c, uid, limit),
                                                                         "balance": None, "spent": None})
        user = repo.get_user(cur, user["id"])
    kind, name, ctype, data = server.billing_receipt(server.Ctx(SimpleNamespace(cursor=lambda: cur), user, (), {}, {},
                                                                SimpleNamespace()))
    monkeypatch.undo()
    return kind, re.sub(r"\d{8}", "<day>", name), ctype, re.sub(r"(?m)^Generated \(UTC\),.*$", "Generated (UTC),<now>", data)


def test_the_receipts_one_read_answers_as_the_four_it_replaces(conn, monkeypatch):
    from app import repository as repo

    cur = conn.cursor()
    user = _ledger_account(cur)
    uid = user["id"]
    got = repo.credit_history(cur, uid, 100000)
    cur.execute(OLD_LEDGER_SQL, (uid, 100000))
    old = cur.fetchall()
    assert len(got["rows"]) == len(old) == 14  # the welcome credits (ensure_welcome_bonus) and the 13 above
    for new, was in zip(got["rows"], old):
        assert new["created_at"] == str(was["created_at"]) and new["amount_usd"] == str(was["amount_usd"])
        assert {k: new[k] for k in ("kind", "memo", "run_id", "stripe_ref", "run_kind", "run_brief")} == \
            {k: was[k] for k in ("kind", "memo", "run_id", "stripe_ref", "run_kind", "run_brief")}
    assert got["balance"] == repo.credit_balance(cur, uid) and got["spent"] == repo.credit_spent_total(cur, uid)
    assert [r["run_kind"] for r in got["rows"] if r["kind"] == "usage"] == ["research", None, None, "chat", "review", "research", "research"]
    # The file, byte for byte, and the old one built from the four reads.
    assert _receipt(user, cur, monkeypatch, old=False) == _receipt(user, cur, monkeypatch, old=True)
    # A history that reaches the cap may be cut: its totals are read, not summed.
    cut = repo.credit_history(cur, uid, 3)
    assert len(cut["rows"]) == 3 and cut["balance"] is None and cut["spent"] is None


def test_usage_that_nets_to_zero_says_nothing_spent(conn, monkeypatch):
    from app import repository as repo

    cur = conn.cursor()
    for amounts in ((), ("-0.500000", "0.500000")):
        user = _user(cur, "zero")
        for k, a in enumerate(amounts):
            _credit(cur, user["id"], "usage", a, "Research session", NOW - timedelta(hours=k + 1))
        got = repo.credit_history(cur, user["id"], 100000)
        assert got["spent"] == repo.credit_spent_total(cur, user["id"]) == 0.0
        assert str(got["spent"]) == "0.0"  # not -0.0, which the receipt prints as -0.0000
        _, _, _, data = _receipt(user, cur, monkeypatch, old=False)
        assert "Total spent (USD),0.0000" in data.splitlines()
        assert _receipt(user, cur, monkeypatch, old=False) == _receipt(user, cur, monkeypatch, old=True)


def test_the_packed_round_trips_are_the_trips_the_old_read_and_the_gate_read(conn):
    from app import repository as repo

    cur = conn.cursor()
    uid = _user(cur, "trips")["id"]
    sids = []
    t0 = NOW - timedelta(days=9)
    for s in range(3):
        sid = repo.insert_strategy(cur, uid, None, None, "btc15m", "momentum", "S%d" % s, "t", {"market": "btc15m"}, "passed", {})
        sids.append(sid)
        deps = [repo.create_deployment(cur, uid, sid, "btc15m", "paper") for _ in range(2)]
        demo = repo.create_deployment(cur, uid, sid, "btc15m", "demo")
        for k in range(40):
            d = deps[k % 2]
            at = t0 + timedelta(hours=k // 3)  # ties in opened_at across trips
            ref = "sim:%d-%d-%d" % (sid, d, k)
            exit_price = [1.0, 0.0, 0.6243][k % 3]
            pnl = round(3 * (exit_price - 0.41) - 0.0123, 4)
            if k % 7 == 3:  # a part-sold trip: two closed slices of one entry
                repo.insert_closed_position(cur, d, uid, "paper", "KXBTC15M-26OCT%02d-T%d" % (k, s), "yes", 1, 0.41, 0.004, 0.5, 0.001,
                                            0.0890, "take_profit", at + timedelta(minutes=5), order_ref=ref, opened_at=at,
                                            closed_at=at + timedelta(minutes=5))
            pid = repo.insert_closed_position(cur, d, uid, "paper", "KXBTC15M-26OCT%02d-T%d" % (k, s), "yes", 3, 0.41, 0.0123,
                                              exit_price, 0.0, pnl, "settled", at + timedelta(minutes=14), order_ref=ref,
                                              opened_at=at, closed_at=at + timedelta(minutes=14))
            if k % 11 == 5:
                cur.execute("UPDATE app_positions SET pnl=NULL WHERE id=%s", (pid,))
            if k % 9 == 4:  # a slice still open: the trip is left out
                repo.insert_position(cur, d, uid, "paper", "KXBTC15M-26OCT%02d-T%d" % (k, s), "yes", 2, 0.41, 0.003,
                                     NOW + timedelta(hours=1), order_ref=ref)
            if k % 10 == 0:  # an instant fill (no order_ref) and a demo trade: never counted
                repo.insert_closed_position(cur, d, uid, "paper", "KXLEGACY-%d" % k, "yes", 1, 0.5, 0.0, 1.0, 0.0, 0.5,
                                            "settled", at, opened_at=at, closed_at=at)
                repo.insert_closed_position(cur, demo, uid, "demo", "KXDEMO-%d" % k, "yes", 1, 0.5, 0.0, 1.0, 0.0, 0.5,
                                            "settled", at, order_ref="sim:demo-%d" % k, opened_at=at, closed_at=at)
    cur.execute(OLD_TRIPS_SQL.format(marks=",".join(["%s"] * len(sids))), (uid, "sim:%", *sids))
    old = {}
    for r in cur.fetchall():
        old.setdefault(int(r["strategy_id"]), []).append(
            {"pnl": float(r["pnl"] or 0), "opened_at": r["opened_at"], "closed_at": r["closed_at"], "ticker": r.get("ticker")})
    got = repo.paper_round_trips_by_strategy(cur, uid, sids)
    assert got == old and sorted(got) == sids  # the same trips in the same order
    for trips in got.values():
        assert all(type(t["pnl"]) is float and type(t["opened_at"]) is datetime and type(t["closed_at"]) is datetime
                   and type(t["ticker"]) is str for t in trips)
    assert any(t["pnl"] == 0.0 for t in got[sids[0]])  # a NULL sum reads 0.0, as float(None or 0)
    key = lambda t: (t["opened_at"], t["pnl"], t["ticker"])  # noqa: E731 - ties in opened_at have no set order
    for sid in sids:
        assert sorted(got[sid], key=key) == sorted(repo.paper_round_trips(cur, uid, sid), key=key)  # the gate's own read
    assert repo.paper_round_trips_by_strategy(cur, uid, []) == {}
    assert repo.paper_round_trips_by_strategy(cur, _user(cur, "none")["id"], sids) == {}
