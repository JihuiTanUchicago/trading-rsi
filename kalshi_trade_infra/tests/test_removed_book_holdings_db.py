"""A strategy whose paper deployment was removed while it still held positions (the money-trust audit of 2026-11-01),
against a real MySQL: every page that names its removed book says the same record and the same open positions.

The account: one strategy found by a research session, deployed on paper, one closed trade (+$0.2725), then removed
with two positions still open (they run to settlement); a second strategy from the same session runs on demo, removed
too, with one closed trade, so a demo book rides beside and is never added to paper.

Before the audit the Strategies list said "Removed from paper: +$0.27 · 1 trade" and nothing of the two positions,
while Home, Deploy and the track record ("1 +2 open") listed them; the research session's page said only "Open it to
deploy on paper", with no record at all. Now the list's `past` carries each book's `open`, and the session endpoint
sends the same `past` (one read, only when a pass has a removed deployment).
Opt in as for test_app_engine_db.py (APP_TEST_MYSQL=1).
"""
import os
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from trading_core.domain.money import cents

pytestmark = pytest.mark.skipif(os.getenv("APP_TEST_MYSQL") != "1", reason="needs APP_TEST_MYSQL=1 and a MySQL")

RESULT = {"capital": 40.0, "out_of_sample": {"trades": 40, "pnl": 6.0, "win_rate": 0.61},
          "in_sample": {"trades": 90, "pnl": 30.0, "win_rate": 0.7}}


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


@pytest.fixture
def pages(monkeypatch):
    from app.services import book_memo
    from app.web.routes import dashboard as dashboard_routes

    monkeypatch.setattr(dashboard_routes, "_freshness", lambda c: {})
    book_memo.clear()
    yield
    book_memo.clear()


def _seed(cur):
    from app import repository as repo
    from app.trading import spec as specmod

    uid = repo.upsert_google_user(cur, "test:" + uuid.uuid4().hex, "removedbook@example.invalid", "Robin", None)["id"]
    now = repo.now_utc()
    tag = uuid.uuid4().hex[:6].upper()
    spec = specmod.validate({"side": "no", "entry": {"min_edge": 0.05}, "sizing": {"stake": 5.0, "max_positions": 4}})
    rid = repo.create_run(cur, uid, "research", "btc15m", None, "Find a fade")
    repo.finish_run(cur, rid, "succeeded", tried=2, passed=2)
    sid = {k: repo.insert_strategy(cur, uid, rid, None, "btc15m", "custom", "Removed " + k, "t", spec, "passed", dict(RESULT))
           for k in ("held", "demo")}
    paper = repo.create_deployment(cur, uid, sid["held"], "btc15m", "paper")
    at = now - timedelta(days=2)
    repo.insert_closed_position(cur, paper, uid, "paper", "KXBTCRB%s-26OCT30-C1" % tag, "no", 2, 0.45, 0.035, 0.62, 0.0325,
                                0.2725, "take_profit", at, order_ref="sim:rb1", opened_at=at - timedelta(minutes=12),
                                closed_at=at)
    for i, (side, qty, price, fee) in enumerate((("no", 4, 0.385, 0.0675), ("yes", 1, 0.215, 0.0175))):
        repo.insert_position(cur, paper, uid, "paper", "KXBTCRB%s-26NOV01-H%d" % (tag, i), side, qty, price, fee,
                             now + timedelta(hours=3), order_ref="sim:rbh%d" % i)
    assert repo.set_deployment_status(cur, uid, paper, "stopped", "By you")
    demo = repo.create_deployment(cur, uid, sid["demo"], "btc15m", "demo")
    repo.insert_closed_position(cur, demo, uid, "demo", "KXBTCRB%s-26OCT30-D1" % tag, "yes", 3, 0.37, 0.0525, 1.0, 0.0,
                                1.8375, "settled", at, order_ref="demo:rb2", opened_at=at - timedelta(minutes=12), closed_at=at)
    assert repo.set_deployment_status(cur, uid, demo, "stopped", "By you")
    return {"uid": uid, "rid": rid, "sid": sid, "paper": paper, "demo": demo}


def test_a_removed_book_says_its_record_and_open_positions_alike_on_the_list_and_its_session(conn, pages):
    from app import repository as repo
    from app import server

    cur = conn.cursor()
    s = _seed(cur)
    uid, sid = s["uid"], s["sid"]
    user = repo.get_user(cur, uid)

    def ctx(*params, **query):
        return server.Ctx(conn, user, tuple(str(p) for p in params), {k: str(v) for k, v in query.items()}, {}, None)

    cur.execute("SELECT * FROM app_positions WHERE user_id=%s ORDER BY id", (uid,))
    rows = cur.fetchall()
    held_open = sum(1 for r in rows if r["deployment_id"] == s["paper"] and r["status"] == "open")
    held_pnl = cents(sum((Decimal(str(r["pnl"])) for r in rows if r["deployment_id"] == s["paper"] and r["status"] == "closed"),
                         Decimal(0)))
    assert held_open == 2 and held_pnl == 0.27

    cards = {c["id"]: c for c in server.list_strategies(ctx())["strategies"]}
    session = {c["id"]: c for c in server.get_run(ctx(s["rid"], after=0))["strategies"]}
    page = server.get_strategy(ctx(sid["held"]))
    dash = server.dashboard(ctx())
    record = server.get_track_record(ctx())["modes"]

    # The list's removed paper book: its closed trade and P&L, and the two positions it still holds.
    assert cards[sid["held"]]["past"] == {"paper": {"pnl": held_pnl, "trades": 1, "deployments": 1, "open": held_open}}
    # The session page says the same, from its own read (it said nothing before).
    assert session[sid["held"]]["past"] == cards[sid["held"]]["past"]
    assert session[sid["demo"]]["past"] == cards[sid["demo"]]["past"] == {"demo": {"pnl": 1.84, "trades": 1, "deployments": 1}}
    # The same count everywhere else that lists them: Home's open positions, the strategy page's holdings, the track record.
    assert sum(1 for p in dash["open_positions"] if p["deployment_id"] == s["paper"]) == held_open
    assert page["holdings_books"]["paper"]["open_positions"] == held_open
    assert page["past_deployments"][0]["realized_pnl"] == held_pnl and page["past_deployments"][0]["closed_trades"] == 1
    row = next(r for r in record["paper"]["strategies"] if r["strategy_id"] == sid["held"])
    assert row["open_trades"] == held_open and row["net_pnl"] == held_pnl and row["trades"] == 1
    # Books never meet: no paper figure carries the demo trade, and the demo book carries no open count.
    assert "paper" not in cards[sid["demo"]]["past"] and "demo" not in cards[sid["held"]]["past"]
