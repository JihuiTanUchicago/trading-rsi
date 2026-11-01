"""The bell's session-finished notice against a real MySQL: written once per session.

The notice is written where a run is marked finished, only by the call whose UPDATE took the run out of
'running' (agent_run_repository.finish_run, and the reaper's stop). A finish run twice, two workers
finishing one run at once, or a worker racing the reaper, add one line between them; ringing the same
session again adds nothing (repository.add_notification_once). Opt in as for test_app_engine_db.py
(APP_TEST_MYSQL=1).
"""
import os
import threading
import uuid
from datetime import timedelta

import pytest

pytestmark = pytest.mark.skipif(os.getenv("APP_TEST_MYSQL") != "1", reason="needs APP_TEST_MYSQL=1 and a MySQL")


@pytest.fixture(scope="module")
def conn():
    from app import schema
    from data_pipeline.common import get_db_conn

    schema.main()
    c = get_db_conn()
    yield c
    c.close()


def _user(cur):
    from app import repository as repo
    return repo.upsert_google_user(cur, "test:" + uuid.uuid4().hex, "rn-%s@example.com" % uuid.uuid4().hex[:8], "R", None)["id"]


def _running(cur, uid, brief=False, forever=False):
    from app import repository as repo
    rid = repo.create_run(cur, uid, "research", "btc15m", "momentum", "Late takers overpay", run_forever=forever, brief=brief)
    cur.execute("UPDATE app_agent_runs SET status='running', worker_id='w1', started_at=UTC_TIMESTAMP(), "
                "heartbeat_at=UTC_TIMESTAMP() WHERE id=%s", (rid,))
    return rid


def _notices(cur, uid):
    cur.execute("SELECT kind, body, link FROM app_notifications WHERE user_id=%s ORDER BY id", (uid,))
    return cur.fetchall()


def _finish(cur, rid, status="succeeded", tried=10, passed=2):
    """The agent's finish: mark the run, and ring only when this call ended it."""
    from app import repository as repo
    from app.services import research_notice
    if repo.finish_run(cur, rid, status, None, tried, passed, 100, 50):
        research_notice.ring(cur, rid)
        return True
    return False


def test_finishing_the_same_session_twice_gives_one_notice(conn):
    from app.services import research_notice

    cur = conn.cursor()
    uid = _user(cur)
    rid = _running(cur, uid)
    assert _finish(cur, rid) is True
    assert _finish(cur, rid) is False  # a retried finish: the run is no longer running
    # Even a second say about the same session (a future path ringing again) adds no line.
    assert research_notice.ring(cur, rid) is False
    assert _notices(cur, uid) == [{"kind": "research_done", "link": "/research/%d" % rid,
                                   "body": "Your research session on Bitcoin up or down in 15 minutes finished. 2 of 10 ideas passed."}]


def test_two_workers_finishing_one_session_at_once_give_one_notice(conn):
    from data_pipeline.common import get_db_conn

    cur = conn.cursor()
    uid = _user(cur)
    rid = _running(cur, uid)
    conns = [get_db_conn() for _ in range(4)]
    start, took = threading.Barrier(len(conns)), []

    def go(c):
        start.wait()
        took.append(_finish(c.cursor(), rid, "succeeded" if len(took) % 2 else "failed"))

    threads = [threading.Thread(target=go, args=(c,)) for c in conns]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for c in conns:
        c.close()
    assert sorted(took) == [False, False, False, True]
    assert len(_notices(cur, uid)) == 1


def test_the_reaper_and_the_worker_racing_give_one_notice_either_way(conn):
    from app import repository as repo
    from app.services import research_notice

    cur = conn.cursor()
    uid = _user(cur)
    # The reaper stops a stale run that used up its resumes; the worker's late finish then takes no row.
    rid = _running(cur, uid)
    cur.execute("UPDATE app_agent_runs SET heartbeat_at=UTC_TIMESTAMP() - INTERVAL 1 HOUR, resume_count=9, "
                "checkpoint_json=%s WHERE id=%s", ('{"tried": [{}, {}, {}], "dropped_n": 1, "total_passed": 1}', rid))
    stopped = []
    repo.fail_abandoned_runs(cur, timedelta(minutes=5), max_resumes=3, stopped=stopped)
    assert rid in stopped
    for s in stopped:
        research_notice.ring(cur, s)
    assert _finish(cur, rid) is False
    mine = _notices(cur, uid)
    assert [(n["link"], n["body"]) for n in mine] == [(
        "/research/%d" % rid,
        "Your research session on Bitcoin up or down in 15 minutes was interrupted and stopped. 1 of 4 ideas passed.")]
    # The other way round: the worker finished first, so the reaper finds nothing running to stop.
    rid2 = _running(cur, uid)
    assert _finish(cur, rid2, tried=4, passed=0) is True
    cur.execute("UPDATE app_agent_runs SET heartbeat_at=UTC_TIMESTAMP() - INTERVAL 1 HOUR, resume_count=9 WHERE id=%s", (rid2,))
    stopped = []
    repo.fail_abandoned_runs(cur, timedelta(minutes=5), max_resumes=3, stopped=stopped)
    assert rid2 not in stopped
    assert [n["link"] for n in _notices(cur, uid)] == ["/research/%d" % rid, "/research/%d" % rid2]


def test_autopilot_sessions_and_cancelled_ones_ring_nothing_and_credits_say_so(conn):
    from app import repository as repo

    cur = conn.cursor()
    uid = _user(cur)
    auto = _running(cur, uid, brief=True)
    assert _finish(cur, auto) is True
    cancelled = _running(cur, uid)
    assert repo.cancel_run(cur, uid, cancelled)
    assert _finish(cur, cancelled) is False  # the worker notices the cancel; nothing is said
    assert list(_notices(cur, uid)) == []
    # A run-until-stopped session that spent the credits: its log holds the agent's step, and the notice says why.
    credits = _running(cur, uid, forever=True)
    repo.add_event(cur, credits, "step", repo.OUT_OF_CREDITS_STEP + ". Add more (or turn on auto-reload) and start a new session.")
    assert _finish(cur, credits, tried=30, passed=0) is True
    assert [n["body"] for n in _notices(cur, uid)] == [
        "Your research session on Bitcoin up or down in 15 minutes stopped because your credits ran out. "
        "None of the 30 ideas it tested passed."]


def test_the_bell_lists_it_and_the_once_check_reads_the_users_kind_index(conn):
    from app import repository as repo
    from app.services import bell

    cur = conn.cursor()
    uid = _user(cur)
    rid = _running(cur, uid)
    _finish(cur, rid)
    items = bell.grouped(repo.list_notifications(cur, uid))
    assert [(n["kind"], n["link"], n["read"]) for n in items] == [("research_done", "/research/%d" % rid, False)]
    assert repo.count_unread_notifications(cur, uid) == 1
    cur.execute("ANALYZE TABLE app_notifications")
    cur.fetchall()
    cur.execute("EXPLAIN SELECT 1 FROM app_notifications WHERE user_id=%s AND kind=%s AND link=%s",
                (uid, "research_done", "/research/%d" % rid))
    plan = cur.fetchall()
    assert plan[0]["key"] in ("idx_notif_user_kind", "idx_notif_user"), plan


def test_a_session_that_errors_after_a_pass_says_the_same_count_on_the_list_the_page_and_the_bell(conn, monkeypatch):
    """agent.run_research's own error path against MySQL: a run until stopped gates a first round that saves a
    passed strategy (the checkpoint counts it, as the gate's transaction does), then its next round ends on an
    error. The model, the market data, the gate and the bill are faked (no model, Kalshi or billing call); the
    finish, the row the list and the session page read, and the bell's line are the real ones. The row said
    0 passed while the strategy existed, and the bell (handed the checkpoint's count) said 1."""
    from app import repository as repo
    from app.research import agent
    from tests.test_app_backtest import _dataset

    cur = conn.cursor()
    uid = _user(cur)
    rid = _running(cur, uid, forever=True)
    calls = []

    def explore(cur, run, ds, partition, memory, model, usage, log, checkpoint, budget, tried, *a, mark_gated, **k):
        calls.append(1)
        if len(calls) > 1:
            raise agent.LLMError("The model's reply could not be read.")
        tried += [{"title": "H0"}, {"title": "H1"}]
        repo.insert_strategy(cur, uid, rid, None, "btc15m", "momentum", "Late takers overpay", "Because.",
                             {"side": "yes"}, "passed", {"in_sample": {}})
        mark_gated(cur, 1)
        return 2, 1

    monkeypatch.setattr(agent, "_explore_and_gate", explore)
    monkeypatch.setattr(agent.partitions, "ensure_partition", lambda cur, market: {"id": 1})
    monkeypatch.setattr(agent, "_plan_lookback", lambda run, market, *a: agent.MARKETS[market].history_days)
    monkeypatch.setattr(agent, "_rsi_choose", lambda *a, **k: "")
    monkeypatch.setattr(agent, "_rsi_memory", lambda *a, **k: "")
    monkeypatch.setattr(agent, "_bill", lambda *a, **k: None)
    monkeypatch.setattr(agent.repo, "credit_balance", lambda *a, **k: 100.0)
    monkeypatch.setattr(agent, "DREAM_EVERY_CHUNKS", 99)
    run = {**repo.get_run(cur, uid, rid), "user_id": uid, "worker_id": "w1"}
    agent.run_research(conn, run, model=lambda system, messages, on_delta=None: ("", {}),
                       dataset_fn=lambda m, h=None: _dataset(n_contracts=60))
    assert len(calls) == 2
    row = repo.get_run(cur, uid, rid)  # the session page's row
    assert (row["status"], row["hypotheses_tried"], row["strategies_passed"]) == ("failed", 2, 1)
    listed = [r for r in repo.list_runs(cur, uid) if r["id"] == rid]  # the Research list's row
    assert [(r["status"], r["strategies_passed"]) for r in listed] == [("failed", 1)]
    assert [n["body"] for n in _notices(cur, uid)] == [
        "Your research session on Bitcoin up or down in 15 minutes stopped on an error. 1 of 2 ideas passed."]
