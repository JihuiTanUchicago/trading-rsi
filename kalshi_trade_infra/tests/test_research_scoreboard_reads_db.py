"""The research loop board (/api/research/scoreboard) answers as it did with fewer reads, against a real MySQL.
Opt in as for test_app_engine_db.py (APP_TEST_MYSQL=1).

One account holds four paper strategies (one also on demo with a clean demo record, one stopped and run again,
one down on paper, one with too few trades), instant fills the realistic record leaves out, a trade with a slice
still open and partial exits that share an order_ref, in 4-place amounts that don't add up evenly as floats.
- The trips' count and net worked out from the round trips already read (rsi.paper_progress_of) are what
  repo.paper_progress sums in SQL, and the board's paper outcomes and tournament, handed the deployments and the
  trips it reads once, are what each gives reading for itself.
- With the Deploy board's checks kept (services.paper_checks, as its startup warm-up and its polls keep them), the
  board's live-ready count asks the gate for none of the strategies the Deploy board kept, and its whole answer is
  the one it gives with nothing kept.
"""
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(os.getenv("APP_TEST_MYSQL") != "1", reason="needs APP_TEST_MYSQL=1 and a MySQL")

NOW = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


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


@pytest.fixture(scope="module")
def book(conn):
    """{uid, user, sids}: four paper strategies' records over ~10 days, the first with a clean demo record."""
    from app import repository as repo
    from app.trading import spec as specmod

    rng = random.Random(9765)
    cur = conn.cursor()
    user = repo.upsert_google_user(cur, "test:" + uuid.uuid4().hex, "board@example.invalid", "Board", None)
    uid = user["id"]
    sids = []
    for k, (edge, n) in enumerate([(0.25, 80), (0.12, 60), (-0.2, 50), (0.2, 12)]):
        spec = specmod.validate({"side": "yes", "entry": {"min_edge": 0.05}, "sizing": {"stake": 5.0, "max_positions": 4}})
        result = {"capital": 40.0, "in_sample": {"trades": 200, "pnl": 9.0, "trades_per_day": 8.0},
                  "out_of_sample": {"trades": 40, "pnl": 6.0}}
        sid = repo.insert_strategy(cur, uid, None, None, "btc15m", "momentum", "Board %d" % k, "t", spec, "passed", result)
        sids.append(sid)
        deps = [repo.create_deployment(cur, uid, sid, "btc15m", "paper")]
        if k == 1:  # stopped, then run again: both deployments' trips are the strategy's record
            repo.set_deployment_status(cur, uid, deps[0], "stopped", "By you")
            deps.append(repo.create_deployment(cur, uid, sid, "btc15m", "paper"))
        start = NOW - timedelta(days=10)
        for i in range(n):
            dep = deps[i % len(deps)]
            opened = start + timedelta(minutes=170 * i + rng.randint(0, 30))
            closed = opened + timedelta(minutes=rng.randint(5, 14))
            won = rng.random() < 0.5 + edge
            qty, entry = rng.randint(2, 9), round(rng.uniform(0.2, 0.8), 2)
            fee = round(0.07 * qty * entry * (1 - entry) + 0.0049, 4)
            pnl = round(qty * ((1.0 if won else 0.0) - entry) - fee, 4)
            ticker = "KXBTC15M-%s-T100" % opened.strftime("%y%b%d%H%M").upper()
            ref = "sim:" + uuid.uuid4().hex
            if i % 7 == 3:  # a partial exit: two slices of one entry fill, summed as one trip
                half = round(pnl / 3, 4)
                for part in (half, round(pnl - half, 4)):
                    repo.insert_closed_position(cur, dep, uid, "paper", ticker, "yes", qty, entry, fee, 1.0 if won else 0.0,
                                                0.0, part, "settled", closed, order_ref=ref, opened_at=opened, closed_at=closed)
            else:
                repo.insert_closed_position(cur, dep, uid, "paper", ticker, "yes", qty, entry, fee, 1.0 if won else 0.0,
                                            0.0, pnl, "settled", closed, order_ref=ref, opened_at=opened, closed_at=closed)
            if i % 20 == 0:  # an instant fill: counted by the legacy check, left out of the realistic record
                repo.insert_closed_position(cur, dep, uid, "paper", ticker, "yes", 1, 0.5, 0.01, 1.0, 0.0, 0.49,
                                            "settled", closed, order_ref="paper:" + uuid.uuid4().hex,
                                            opened_at=opened, closed_at=closed)
        # A trade with one slice closed and one still open: no trip until it closes.
        ref = "sim:" + uuid.uuid4().hex
        repo.insert_closed_position(cur, deps[-1], uid, "paper", "KXBTC15M-OPEN-T1", "yes", 2, 0.5, 0.01, 1.0, 0.0, 0.4851,
                                    "take_profit", NOW, order_ref=ref, opened_at=NOW - timedelta(minutes=9), closed_at=NOW)
        repo.insert_position(cur, deps[-1], uid, "paper", "KXBTC15M-OPEN-T1", "yes", 2, 0.5, 0.01, NOW + timedelta(minutes=5),
                             order_ref=ref)
    # The first strategy's demo record: 25 settled demo orders and a clean sweep an hour ago.
    demo = repo.create_deployment(cur, uid, sids[0], "btc15m", "demo")
    for j in range(25):
        t = NOW - timedelta(days=8) + timedelta(hours=7 * j)
        cur.execute("INSERT INTO app_order_intents (intent_id, deployment_id, user_id, mode, action, side, ticker, qty, "
                    "limit_price, client_order_id, status, fill_qty, fill_price, created_at, updated_at) VALUES "
                    "(%s,%s,%s,'demo','buy','yes',%s,3,0.5,%s,'booked',3,0.5,%s,%s)",
                    (str(uuid.uuid4()), demo, uid, "KXDEMO-%d" % j, uuid.uuid4().hex[:20], t, t))
    cur.execute("INSERT INTO app_demo_reconcile_sweeps (user_id, checked_at, tickers, mismatches) VALUES (%s,%s,3,'[]')",
                (uid, NOW - timedelta(hours=1)))
    return {"uid": uid, "user": repo.get_user(cur, uid), "sids": sids}


@pytest.fixture
def frozen(monkeypatch):
    """One clock for the tournament's allocation (seeded by the hour) and the paper pace (hours since created)."""
    import datetime as dt

    from app.research import tournament

    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.replace(tzinfo=dt.timezone.utc).astimezone(tz) if tz else NOW

    monkeypatch.setattr(dt, "datetime", Frozen)
    monkeypatch.setattr(tournament, "_now", lambda: NOW)


def _board(conn, user):
    from app import server

    server._SHARED.clear()
    return server.research_scoreboard(server.Ctx(conn, user, (), {"days": "14"}, {}, None))


def test_the_trips_count_and_net_worked_out_from_the_trips_are_the_sums_read(conn, book):
    from app import repository as repo
    from app.research import rsi

    cur = conn.cursor()
    sids = book["sids"] + [10 ** 9]  # and a strategy with no trip at all
    read = repo.paper_progress(cur, book["uid"], sids)
    assert rsi.paper_progress_of(repo.paper_round_trips_by_strategy(cur, book["uid"], sids)) == read
    assert sorted(read) == sorted(book["sids"]) and read[book["sids"][1]][0] == 60  # both deployments' trips
    assert all(n == len(repo.paper_round_trips(cur, book["uid"], sid)) for sid, (n, _) in read.items())


def test_the_boards_paper_outcomes_and_tournament_read_the_book_once_and_answer_as_alone(conn, book, frozen):
    from app import server
    from app.research import agent, rsi, tournament

    cur = conn.cursor()
    agent._EDGE_P.clear()
    board = _board(conn, book["user"])
    assert board["paper"] == rsi.paper_outcomes(cur, book["uid"])
    assert board["tournament"] == server.customer_text.tournament_standings(tournament.standings(cur, book["uid"]))
    # The book the board describes: four strategies on paper, one of them live-ready (the demo record's).
    assert board["paper"]["total"]["strategies"] == 4 and board["paper"]["total"]["live_ready"] == 1
    assert board["paper"]["total"]["round_trips"] == 80 + 60 + 50 + 12


def test_the_boards_live_ready_count_is_the_deploy_boards_kept_checks(conn, book, frozen, monkeypatch):
    from app import server
    from app.research import agent
    from app.services import paper_checks

    calls = []
    gate = agent.paper_evidence
    monkeypatch.setattr(agent, "paper_evidence", lambda cur, u, sid: calls.append(sid) or gate(cur, u, sid))
    paper_checks.KEPT.clear()
    paper_checks.GATE.clear()
    agent._EDGE_P.clear()
    alone = _board(conn, book["user"])
    asked = sorted(calls)
    assert asked == sorted(book["sids"][:2])  # up on paper with enough trips: the gate, for each
    # The Deploy board reads (and keeps) each running paper strategy's checks once its counts are in.
    server._ROADS.clear()
    server.list_deployments(server.Ctx(conn, book["user"], (), {}, {}, None))
    assert {k[1] for k in paper_checks.KEPT if k[0] == book["uid"]} >= set(asked)
    calls.clear()
    log = []
    real = conn.cursor

    class Counting:
        def __init__(self, cur):
            self._cur = cur

        def execute(self, sql, args=None):
            log.append(" ".join(str(sql).split()))
            return self._cur.execute(sql, args)

        def __getattr__(self, name):
            return getattr(self._cur, name)

    monkeypatch.setattr(conn, "cursor", lambda: Counting(real()))
    kept = _board(conn, book["user"])
    monkeypatch.setattr(conn, "cursor", real)
    assert calls == [] and kept == alone and kept["paper"]["total"]["live_ready"] == 1
    assert not [q for q in log if "FROM app_order_intents" in q]  # no demo record read: the gate wasn't asked
    paper_checks.KEPT.clear()
    paper_checks.GATE.clear()


def test_the_first_board_after_the_warm_up_reads_no_gate_and_a_moved_record_is_read_again(conn, book, frozen, monkeypatch):
    """The startup warm-up keeps the board's live-ready answers beside the Deploy board's (keep_scoreboard_checks), so
    the first board after it asks the gate for nothing and answers as it does with nothing kept; the board keeps what
    it reads, so a second board doesn't ask either. A strategy whose record moved (a trade closed) is asked again,
    and only it, and the board answers as it does with nothing kept."""
    from app import repository as repo, server
    from app.research import agent
    from app.services import paper_checks

    uid, user, sids = book["uid"], book["user"], book["sids"]
    calls = []
    gate = agent.paper_evidence
    monkeypatch.setattr(agent, "paper_evidence", lambda cur, u, sid: calls.append(sid) or gate(cur, u, sid))
    paper_checks.KEPT.clear()
    paper_checks.GATE.clear()
    agent._EDGE_P.clear()
    alone = _board(conn, user)
    assert sorted(calls) == sorted(sids[:2]) and _board(conn, user) == alone and len(calls) == 2
    paper_checks.KEPT.clear()  # a fresh process, warmed at startup
    paper_checks.GATE.clear()
    server._ROADS.clear()
    monkeypatch.setattr(server.repo, "paper_board_users", lambda cur, limit: [uid])
    assert server.warm_deploy_boards() == 1
    calls.clear()
    assert _board(conn, user) == alone and calls == []
    cur = conn.cursor()
    ref = "sim:" + uuid.uuid4().hex
    dep = next(d["id"] for d in repo.list_deployments(cur, uid) if d["strategy_id"] == sids[0] and d["mode"] == "paper")
    pid = repo.insert_closed_position(cur, dep, uid, "paper", "KXBTC15M-MOVED-T1", "yes", 3, 0.4, 0.02, 1.0, 0.0, 1.78,
                                      "settled", NOW, order_ref=ref, opened_at=NOW - timedelta(minutes=12), closed_at=NOW)
    try:
        moved = _board(conn, user)
        assert calls == [sids[0]]
        paper_checks.KEPT.clear()
        paper_checks.GATE.clear()
        assert _board(conn, user) == moved and sorted(calls[1:]) == sorted(sids[:2])
    finally:
        cur.execute("DELETE FROM app_positions WHERE id=%s", (pid,))
        paper_checks.KEPT.clear()
        paper_checks.GATE.clear()
