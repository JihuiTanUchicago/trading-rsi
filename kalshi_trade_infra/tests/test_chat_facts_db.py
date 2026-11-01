"""The chat's answers about money and status, held to the pages that show the same thing, against a real MySQL.

One heavy account (audit 2026-11-01): paper and demo books with a strategy on both, a paused one, a deleted strategy
whose stopped deployment still holds an open paper position, a sports market Kalshi cancelled and settled at a fair
price, research sessions running and queued (and one silent past the reaper's limit), a credit ledger with a top-up
and research, check-in and chat debits, autopilot on under its research cap. The agent's context (what a chat reply
quotes) must say each figure as Home, the autopilot card, Funds, Billing and the Research page do, never adding books
or mixing credits with P&L; and a chat run's reply, posted through the real run with the model stubbed, reaches the
panel with no internal id, market key or model id. Opt in as for test_app_engine_db.py (APP_TEST_MYSQL=1).
"""
import json
import os
import re
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
    c.autocommit(True)
    yield c
    c.cursor().execute("UPDATE app_deployments SET status='stopped' WHERE user_id IN "
                       "(SELECT id FROM app_users WHERE google_sub LIKE 'test:%%')")
    c.close()


@pytest.fixture
def quiet(monkeypatch):
    from app.adapters.stripe import billing
    from app.services import book_memo
    from app.trading import engine
    from app.web.routes import dashboard as dashboard_routes

    monkeypatch.setattr(billing, "STRIPE_SECRET_KEY", "")
    monkeypatch.setattr(engine, "LIVE_TRADING_ENABLED", False)
    monkeypatch.setattr(dashboard_routes, "_freshness", lambda c: {})
    book_memo.clear()
    yield
    book_memo.clear()


def _seed(cur):
    from app import repository as repo
    from app.adapters.db import billing_repository as brepo
    from app.trading import spec as specmod

    uid = repo.upsert_google_user(cur, "test:" + uuid.uuid4().hex, "chatfacts@example.invalid", "Chat Facts", None)["id"]
    now = repo.now_utc().replace(microsecond=0)
    spec = specmod.validate({"side": "yes", "entry": {"min_edge": 0.05}, "exit": {"stop_loss": 0.3},
                             "sizing": {"stake": 5.0, "max_positions": 8}})
    tag = uuid.uuid4().hex[:6].upper()

    def strat(title, market):
        return repo.insert_strategy(cur, uid, None, None, market, "custom", title, "t", spec, "passed", {"capital": 40.0})

    def dep(sid, market, mode):
        return repo.create_deployment(cur, uid, sid, market, mode)

    n = [0]

    def closed(d, mode, entry, exit_, pnl, reason, at):
        n[0] += 1
        repo.insert_closed_position(cur, d, uid, mode, "KXCF%s-%d" % (tag, n[0]), "yes", 10, entry, 0.07, exit_, 0.0, pnl,
                                    reason, at, order_ref="%s:cf%d" % ("sim" if mode == "paper" else mode, n[0]),
                                    opened_at=at - timedelta(hours=2), closed_at=at)

    def held(d, mode, qty, price):
        n[0] += 1
        repo.insert_position(cur, d, uid, mode, "KXCF%s-H%d" % (tag, n[0]), "yes", qty, price, 0.03, now + timedelta(hours=6),
                             order_ref="%s:ch%d" % ("sim" if mode == "paper" else mode, n[0]))

    s = {k: strat("CF " + k, m) for k, m in (("fade", "btc15m"), ("wx", "high_ny"), ("dogs", "nba_game"), ("gone", "btc_daily"))}
    d = {"fade_p": dep(s["fade"], "btc15m", "paper"), "fade_d": dep(s["fade"], "btc15m", "demo"),
         "wx_p": dep(s["wx"], "high_ny", "paper"), "dogs_p": dep(s["dogs"], "nba_game", "paper"),
         "dogs_d": dep(s["dogs"], "nba_game", "demo"), "gone_p": dep(s["gone"], "btc_daily", "paper")}
    closed(d["fade_p"], "paper", 0.45, 1.0, 5.3218, "settled", now - timedelta(hours=2))
    closed(d["fade_d"], "demo", 0.44, 1.0, 5.4226, "settled", now - timedelta(hours=3))
    closed(d["wx_p"], "paper", 0.40, 0.27, -1.6107, "stop_loss", now - timedelta(hours=20))
    closed(d["dogs_p"], "paper", 0.36, 0.50, 1.2348, "settled", now - timedelta(hours=30))  # Kalshi cancelled it: paid 50c
    closed(d["dogs_p"], "paper", 0.38, 0.0, -3.9698, "settled", now - timedelta(hours=10))
    closed(d["dogs_d"], "demo", 0.37, 0.50, 1.1252, "settled", now - timedelta(hours=30))
    held(d["fade_p"], "paper", 10, 0.42)
    held(d["wx_p"], "paper", 12, 0.3105)
    held(d["fade_d"], "demo", 10, 0.23)
    held(d["gone_p"], "paper", 7, 0.55)
    repo.set_deployment_status(cur, uid, d["dogs_p"], "paused", "loss guard: 3 losses in a row")
    repo.set_deployment_status(cur, uid, d["gone_p"], "stopped", "By you")
    cur.execute("DELETE FROM app_strategies WHERE id=%s", (s["gone"],))
    repo.set_autopilot(cur, uid, True)
    brepo.add_credit(cur, uid, "topup", 300.0, "Card top-up", None)
    # Research: one running (beating), one queued, one silent past the reaper's limit; each autopilot's (brief).
    runs = {}
    for k, market, status, beat in (("run", "high_ny", "running", now), ("queued", "epl_game", "queued", None),
                                    ("silent", "nfl_game", "running", now - timedelta(hours=3))):
        runs[k] = repo.create_run(cur, uid, "research", market, None, "p", brief=True)
        cur.execute("UPDATE app_agent_runs SET status=%s, started_at=%s, heartbeat_at=%s, input_tokens=1000000, "
                    "output_tokens=100000 WHERE id=%s", (status, beat, beat, runs[k]))
    for kind, amount, at in (("research", 45.0004, 50), ("research", 14.9951, 60 * 30), ("review", 0.2133, 120),
                             ("chat", 0.0387, 30), ("chat", 0.0021, 31)):
        rid = runs["run"] if kind == "research" and at < 60 else (runs["silent"] if kind == "research" else repo.create_run(cur, uid, kind))
        cur.execute("INSERT INTO app_credit_ledger (user_id, kind, amount_usd, memo, run_id, created_at) VALUES (%s,'usage',%s,'u',%s,%s)",
                    (uid, -amount, rid, now - timedelta(minutes=at)))
        if kind != "research":
            cur.execute("UPDATE app_agent_runs SET status='succeeded' WHERE id=%s", (rid,))
    return {"uid": uid, "s": s, "d": d, "now": now}


def _money(v) -> str:
    return ("-$%.2f" % -v) if v < 0 else "$%.2f" % v


def test_the_chats_figures_are_the_pages(conn, quiet, monkeypatch):
    from app import repository as repo
    from app import server
    from app.research import agent
    from app.web.routes import billing as billing_routes

    cur = conn.cursor()
    seed = _seed(cur)
    uid, d = seed["uid"], seed["d"]
    user = repo.get_user(cur, uid)

    def ctx(*params, **query):
        return server.Ctx(conn, user, tuple(str(p) for p in params), {k: str(v) for k, v in query.items()}, {}, None)

    dash = server.dashboard(ctx())
    funds = server.funds(ctx())
    ap = server.autopilot_status(ctx())
    figs = billing_routes.billing_figures(ctx())
    brief = agent._copilot_context(cur, user)

    # -- what's running: Home's "N strategies running", the card's per book, paused apart -------------------------
    # Home counts strategies (core.js stratCount): CF fade on paper and its demo copy is one, CF dogs runs on demo only.
    home = len({r["strategy_id"] for r in dash["deployments"] if r["status"] == "running"})
    assert home == ap["trading_strategies"] == 3
    assert ("%d running across the books; by book: %d on paper, %d on demo, %d live"
            % (home, ap["paper_running"], ap["demo_running"], ap["live_running"])) in brief
    assert "Paused: 1 (“CF dogs” on paper)." in brief
    assert re.search(r"DEPLOYMENTS RUNNING RIGHT NOW: 2 on paper \([^)]*\), 2 on demo \([^)]*\), 0 live\.", brief)

    # -- research: the Research page's and Home's counts, a silent session in neither -------------------------------
    agents = dash["agents"]
    assert (agents["research_running"], agents["research_queued"]) == (1, 1)
    assert "RESEARCH RUNNING RIGHT NOW: 1 running, 1 queued to start, 0 paused (the Research page's count)." in brief

    # -- credits: Billing's figures, the card's cap ------------------------------------------------------------------
    by = figs["spent_7d_by"]
    assert ("last 24 hours $%.2f; last 7 days $%.2f, of which your research sessions (the user's own) $%.2f, autopilot "
            "research $%.2f, autopilot check-ins $%.2f, chat $%.2f, other agent calls $%.2f"
            % (figs["spent_24h"], figs["spent_7d"], by["research"], by["autopilot_research"], by["autopilot_review"],
               by["chat"], by["other"])) in brief
    cap = ap["research_cap"]
    assert "Autopilot research cap (the autopilot card's): $%.2f per 24 hours" % cap["usd"] in brief
    assert "cost $%.2f by the cap's count" % cap["spent_24h"] in brief
    from trading_core.domain.credits import credit_usd
    assert "research credits $%.2f left" % credit_usd(figs["balance"]) in brief

    # -- balance: Home's and Funds' paper headline; demo's closed trades -------------------------------------------
    p = funds["paper"]
    assert dash["paper"]["equity"] == p["equity"]
    assert ("Paper portfolio %s (Home's and Funds' headline: available cash %s plus open positions at cost %s"
            % (_money(p["equity"]), _money(p["cash"]), _money(p["open_at_cost"]))) in brief
    assert "Demo has %d closed trades all time." % dash["demo"]["closed_trades"] in brief

    # -- a deleted strategy's open position, and the trades with the page's reason line -----------------------------
    orphans = [x for x in dash["open_positions"] if x["title"] == "Deleted strategy"]
    assert len(orphans) == 1 and "DELETED strategy" in brief and "paper %d." % len(orphans) in brief
    for row in dash["recent_closed"]:
        assert "%s · %s" % (_money(row["pnl"]), row["why"]) in brief, row
    assert "Kalshi cancelled this market and paid 50¢ a contract" in brief

    # -- never books together, never credits in P&L ------------------------------------------------------------------
    assert "$-" not in brief
    paper, demo = dash["paper"]["realized_pnl"], dash["demo"]["realized_pnl"]
    assert _money(round(paper + demo, 2)) not in brief

    # -- a chat turn through the real run, the model stubbed: the panel's line names things in words ----------------
    monkeypatch.setattr(agent, "claude", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model call")))
    monkeypatch.setattr(agent.billing, "charge_usage", lambda *a, **k: None)
    repo.add_chat(cur, uid, "user", "how is CF fade doing?")
    rid = repo.create_run(cur, uid, "chat")
    cur.execute("UPDATE app_agent_runs SET status='running' WHERE id=%s", (rid,))
    cur.execute("SELECT * FROM app_agent_runs WHERE id=%s", (rid,))
    run = cur.fetchone()
    said = "#%d made $5.32 on btc15m and deployment_id=%d made $5.42; strategy_id=%d is paused. run #%d is queued." % (
        d["fade_p"], d["fade_d"], seed["s"]["dogs"], rid)
    agent.run_copilot(conn, run, model=lambda s, m, on_delta=None: (json.dumps({"reply": said, "actions": []}), {}))
    [line] = [m["content"] for m in server.get_chat(ctx())["messages"] if m["role"] == "agent"]
    assert line == ("“CF fade” on paper made $5.32 on Bitcoin up or down in 15 minutes and “CF fade” on demo made $5.42; "
                    "“CF dogs” is paused. A research session is queued.")
