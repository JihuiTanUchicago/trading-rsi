"""Every money fact the chat is given equals what the customer's own pages show, against a real MySQL.

Audit 2026-11-01: for one seeded account per case (paper only; paper, demo and live books; a deleted strategy with
open positions; paused strategies; a cancelled market; credits with a reached research cap; a paper forward test; a
big account; a brand-new account) the chat's context (research.agent._copilot_context, what a reply quotes) is
built directly, no model called, and each fact is held to the figure the page that shows it returns: Home
(/api/dashboard), Funds (/api/funds), Deploy (/api/deployments), the strategy page, the autopilot card
(/api/autopilot/status), Research and Billing (/api/billing/figures). Same amount to the cent (money.cents, credits
as Billing rounds them), same book (never summed), same window and its label, the market's and strategy's own names,
no internal market key, ticker, model id or raw error in a customer's words. Table-driven: each case lists the facts
its pages show. Opt in as for test_app_engine_db.py (APP_TEST_MYSQL=1).
"""
import hashlib
import json
import os
import re
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.services import road_ahead

pytestmark = pytest.mark.skipif(os.getenv("APP_TEST_MYSQL") != "1", reason="needs APP_TEST_MYSQL=1 and a MySQL")

TK = {"btc15m": "KXBTC15M-26NOV01%02d00-00", "high_ny": "KXHIGHNY-26NOV%02d-B60.5", "nba_game": "KXNBAGAME-26NOV%02dLALBOS-LAL",
      "btc_daily": "KXBTCD-26NOV01%02d-T110000", "epl_game": "KXEPLGAME-26NOV%02dARSCHE-ARS", "nfl_game": "KXNFLGAME-26NOV%02dKCBUF-KC"}


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


class Seed:
    """One account's rows, as the product writes them (no engine, Kalshi, Stripe or model call)."""

    def __init__(self, cur, tag, days=30):
        from app import repository as repo

        self.cur, self.repo, self.n = cur, repo, 0
        self.now = repo.now_utc().replace(microsecond=0)
        self.uid = repo.upsert_google_user(cur, "test:" + uuid.uuid4().hex, "%s@example.invalid" % tag, tag, None)["id"]
        cur.execute("UPDATE app_users SET created_at=%s WHERE id=%s", (self.now - timedelta(days=days), self.uid))

    def strat(self, title, market):
        from app.trading import spec as specmod

        spec = specmod.validate({"side": "yes", "entry": {"min_edge": 0.05}, "exit": {"stop_loss": 0.3},
                                 "sizing": {"stake": 5.0, "max_positions": 8}})
        res = {"capital": 40.0, "out_of_sample": {"trades": 64, "pnl": 4.2, "max_drawdown": 3.0}}
        return self.repo.insert_strategy(self.cur, self.uid, None, None, market, "custom", title, "t", spec, "passed", res)

    def dep(self, sid, market, mode, status="running", why=None):
        d = self.repo.create_deployment(self.cur, self.uid, sid, market, mode)
        if status != "running":
            self.repo.set_deployment_status(self.cur, self.uid, d, status, why)
        return d

    def closed(self, d, mode, market, qty, entry, exit_, hours_ago, reason="settled"):
        self.n += 1
        fee = round(0.07 * qty * entry * (1 - entry) + 0.0049, 4)
        pnl = round(qty * (exit_ - entry) - fee, 4)
        at = self.now - timedelta(hours=hours_ago)
        self.repo.insert_closed_position(self.cur, d, self.uid, mode, TK[market] % (self.n % 28 + 1), "yes", qty, entry, fee, exit_,
                                         0.0, pnl, reason, at, order_ref="%s:c%d" % ({"paper": "sim"}.get(mode, mode), self.n),
                                         opened_at=at - timedelta(hours=3), closed_at=at)

    def held(self, d, mode, market, qty, entry):
        self.n += 1
        self.repo.insert_position(self.cur, d, self.uid, mode, TK[market] % (self.n % 28 + 1), "yes", qty, entry,
                                  round(0.07 * qty * entry * (1 - entry) + 0.0049, 4), self.now + timedelta(hours=6),
                                  order_ref="%s:o%d" % ({"paper": "sim"}.get(mode, mode), self.n))

    def run(self, market, hours_ago, tokens_in, tokens_out, brief, debits):
        rid = self.repo.create_run(self.cur, self.uid, "research", market, None, "Look for an edge in %s." % market, brief=brief)
        at = self.now - timedelta(hours=hours_ago)
        self.cur.execute("UPDATE app_agent_runs SET status='succeeded', created_at=%s, started_at=%s, finished_at=%s, "
                         "input_tokens=%s, output_tokens=%s, hypotheses_tried=4 WHERE id=%s",
                         (at, at, at + timedelta(hours=1), tokens_in, tokens_out, rid))
        for k, amount in enumerate(debits):
            self.debit(rid, amount, at + timedelta(minutes=10 * (k + 1)))
        return rid

    def debit(self, rid, amount, at):
        self.cur.execute("INSERT INTO app_credit_ledger (user_id, kind, amount_usd, memo, run_id, created_at) "
                         "VALUES (%s,'usage',%s,'u',%s,%s)", (self.uid, -amount, rid, at))


def paper_only(s):
    d = s.dep(s.strat("Fade the 15-minute overshoot", "btc15m"), "btc15m", "paper")
    for qty, entry, exit_, ago in ((10, 0.45, 1.0, 1), (7, 0.62, 0.0, 23), (12, 0.3105, 1.0, 30), (9, 0.415, 0.0, 72)):
        s.closed(d, "paper", "btc15m", qty, entry, exit_, ago)
    s.held(d, "paper", "btc15m", 10, 0.42)
    s.held(d, "paper", "btc15m", 6, 0.555)


def three_books(s):
    from app.adapters.kalshi import kalshi_account

    s.repo.set_kalshi_dev_key(s.cur, s.uid, "demo-key", b"enc")
    s.repo.save_kalshi_key(s.cur, s.uid, "live-key", b"enc")
    kalshi_account.remember_balance(s.uid, 2345.675)  # the live cash memo Funds and Home read (never Kalshi here)
    wx = s.strat("Weather forecast lead", "high_ny")
    dp, dd = s.dep(wx, "high_ny", "paper"), s.dep(wx, "high_ny", "demo")
    dl = s.dep(s.strat("Daily Bitcoin ladder", "btc_daily"), "btc_daily", "live")
    for k in range(5):
        s.closed(dp, "paper", "high_ny", 8 + k, 0.35 + k * 0.03, 1.0 if k % 2 == 0 else 0.0, k * 11 + 2)
    for k in range(3):
        s.closed(dd, "demo", "high_ny", 6 + k, 0.36 + k * 0.025, 0.0 if k == 1 else 1.0, k * 40 + 50)
    s.repo.reload_demo(s.cur, s.uid, 500.0)
    s.closed(dd, "demo", "high_ny", 5, 0.41, 1.0, 1)
    for k in range(4):
        s.closed(dl, "live", "btc_daily", 20 + k * 5, 0.52 + k * 0.01, 1.0 if k % 2 else 0.0, k * 9 + 1)
    s.held(dp, "paper", "high_ny", 9, 0.38)
    s.held(dd, "demo", "high_ny", 9, 0.39)
    s.held(dl, "live", "btc_daily", 25, 0.48)


def deleted(s):
    d1 = s.dep(s.strat("Still running fade", "btc15m"), "btc15m", "paper")
    gone = s.strat("Gone NBA dogs", "nba_game")
    dg = s.dep(gone, "nba_game", "paper")
    s.closed(d1, "paper", "btc15m", 10, 0.44, 1.0, 5)
    s.closed(dg, "paper", "nba_game", 12, 0.38, 0.0, 6)
    s.closed(dg, "paper", "nba_game", 11, 0.33, 1.0, 26)
    s.held(dg, "paper", "nba_game", 7, 0.55)
    s.held(dg, "paper", "nba_game", 4, 0.61)
    s.repo.set_deployment_status(s.cur, s.uid, dg, "stopped", "By you")
    s.repo.delete_strategy(s.cur, s.uid, gone)


def paused(s):
    from app.trading import demo_twins

    s.repo.set_kalshi_dev_key(s.cur, s.uid, "demo-key", b"enc")
    wx, me, nfl = s.strat("Loss-guarded weather", "high_ny"), s.strat("Paused by me", "btc15m"), s.strat("Stopped-out game", "nfl_game")
    dr, du, ds = s.dep(wx, "high_ny", "paper"), s.dep(me, "btc15m", "paper"), s.dep(nfl, "nfl_game", "paper")
    dc = s.dep(me, "btc15m", "demo")
    for k in range(4):
        s.closed(dr, "paper", "high_ny", 40, 0.55, 0.0, k + 2)
    s.closed(du, "paper", "btc15m", 10, 0.45, 1.0, 8)
    s.closed(ds, "paper", "nfl_game", 15, 0.6, 0.38, 4, reason="stop")
    s.repo.set_deployment_status(s.cur, s.uid, dr, "paused", "Lost $89.31, more than the $20.00 guard (2x its worst backtest drawdown).")
    s.repo.set_deployment_status(s.cur, s.uid, du, "paused", "By you")
    s.repo.set_deployment_status(s.cur, s.uid, ds, "paused", "force-closed: position down 36.7% >= stop 30.0%")
    s.repo.set_deployment_status(s.cur, s.uid, dc, "paused", demo_twins.PAUSE_REASON)


def voided(s):
    d = s.dep(s.strat("EPL draws", "epl_game"), "epl_game", "paper")
    s.closed(d, "paper", "epl_game", 10, 0.36, 0.5, 7)       # Kalshi cancelled it: paid 50c a contract
    s.closed(d, "paper", "epl_game", 10, 0.62, 0.4815, 9)    # cancelled at a fair price under the entry
    s.closed(d, "paper", "epl_game", 10, 0.3, 1.0, 12)
    s.held(d, "paper", "epl_game", 5, 0.4)


def credits(s):
    from app.adapters.db import billing_repository as brepo

    s.repo.set_autopilot(s.cur, s.uid, True)
    brepo.add_credit(s.cur, s.uid, "topup", 100.0, "Card top-up", None)
    brepo.add_credit(s.cur, s.uid, "autoreload", 20.0, "Auto-reload", None)
    for k, (market, brief, tin, tout, ago) in enumerate((("btc15m", True, 9_000_000, 900_000, 3), ("high_ny", True, 8_000_000, 700_000, 6),
                                                         ("nba_game", False, 6_000_000, 500_000, 10), ("btc15m", True, 4_000_000, 300_000, 40))):
        s.run(market, ago, tin, tout, brief, [round(5.1234 + k + j * 0.3333, 4) for j in range(3)])
    for kind, amount, ago in (("review", 0.2133, 2), ("review", 0.0049, 30), ("chat", 0.0387, 1), ("chat", 0.0021, 50)):
        rid = s.repo.create_run(s.cur, s.uid, kind, brief=(kind == "review"))
        s.cur.execute("UPDATE app_agent_runs SET status='succeeded', created_at=%s WHERE id=%s", (s.now - timedelta(hours=ago), rid))
        s.debit(rid, amount, s.now - timedelta(hours=ago))


def forward(s):
    from app.research import research_repository as rr

    sid = s.strat("Forward-test NYC highs", "high_ny")
    t = int(s.now.timestamp())
    s.cur.execute("INSERT INTO app_data_partitions (market, research_from_ts, research_to_ts, validation_from_ts, validation_to_ts, "
                  "holdout_from_ts, holdout_to_ts, embargo_seconds, purge_seconds, data_fingerprint, frozen_at) "
                  "VALUES ('high_ny',%s,%s,%s,%s,%s,%s,900,900,%s,%s)",
                  (t - 300 * 86400, t - 80 * 86400, t - 80 * 86400, t - 30 * 86400, t - 30 * 86400, t - 2 * 86400, "0" * 64, s.now))
    eid = rr.preregister(s.cur, {"user_id": s.uid, "run_id": None, "market": "high_ny", "strategy_id": sid,
                                 "hypothesis_hash": hashlib.sha256(uuid.uuid4().bytes).hexdigest(), "spec": {"market": "high_ny"},
                                 "economic_rationale": json.dumps({"mechanism": "Forecast leads the book."}),
                                 "primary_metric": "mean_net_return_per_trade", "null_hypothesis": "zero", "alt_hypothesis": "above",
                                 "planned_test": "bootstrap_mean", "falsification_criteria": "at or below zero", "min_sample": 40,
                                 "cost_assumptions": {"fee": "kalshi_taker"}, "research_partition_id": s.cur.lastrowid,
                                 "planned_n_tests": 6, "correction_method": "LORD++"})
    rr.append_result(s.cur, eid, "validation", "inconclusive", 48, {"net_pnl": 0.84, "p_value": 0.09})  # a near miss: on probation
    s.cur.execute("UPDATE app_strategies SET experiment_id=%s WHERE id=%s", (eid, sid))
    d = s.dep(sid, "high_ny", "paper")
    for k in range(12):
        s.closed(d, "paper", "high_ny", 6 + k % 4, 0.33 + (k % 5) * 0.04, 1.0 if k % 3 else 0.0, k * 13 + 3)
    s.held(d, "paper", "high_ny", 7, 0.37)


def big(s):
    """Many books' worth: 24 deployments over three books (some paused, two removed, one strategy deleted), a few
    hundred closed trades, research sessions with their debits, autopilot on."""
    s.repo.set_autopilot(s.cur, s.uid, True)
    s.repo.set_kalshi_dev_key(s.cur, s.uid, "demo-key", b"enc")
    s.repo.save_kalshi_key(s.cur, s.uid, "live-key", b"enc")
    markets = ["btc15m", "high_ny", "nba_game", "btc_daily", "epl_game", "nfl_game"]
    rows = []
    for i in range(24):
        mode, market = ("paper", "demo", "live")[0 if i < 14 else (1 if i < 20 else 2)], markets[i % 6]
        sid = s.strat("Idea %d on %s" % (i, market), market)
        d = s.dep(sid, market, mode, *(("paused", "By you") if i in (3, 21) else
                                      ("paused", "Lost $21.37, more than the $20.00 guard (2x its worst backtest drawdown).") if i == 6 else ()))
        for k in range(18 + i % 7):
            s.n += 1
            entry, qty = 0.3 + (k % 9) * 0.05, 3 + (k * 7 + i) % 11
            exit_ = 1.0 if (k * 5 + i) % 9 < 5 else 0.0
            fee = round(0.07 * qty * entry * (1 - entry) + 0.0049, 4)
            at = s.now - timedelta(hours=1 + k * 7 + i)
            rows.append((d, s.uid, mode, TK[market] % (s.n % 28 + 1), "yes", qty, entry, fee, at, at - timedelta(hours=2), "closed",
                         exit_, 0.0, round(qty * (exit_ - entry) - fee, 4), "settled", at, "%s:b%d" % ({"paper": "sim"}.get(mode, mode), s.n)))
        if i % 4 == 0:
            s.held(d, mode, market, 4, 0.5)
        if i in (9, 22):
            s.repo.set_deployment_status(s.cur, s.uid, d, "stopped", "By you")
        if i == 9:
            s.repo.delete_strategy(s.cur, s.uid, sid)
    s.cur.executemany("INSERT INTO app_positions (deployment_id, user_id, mode, ticker, side, qty, entry_price, entry_fee, "
                      "market_close_time, opened_at, status, exit_price, exit_fee, pnl, close_reason, closed_at, order_ref) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    for k in range(30):
        s.run(markets[k % 6], 2 + k * 5, 400_000 + k * 1000, 40_000, k % 3 == 0, [0.1234 + (k % 7) * 0.05, 0.0871])


def new(s):
    pass


CASES = {"paper_only": paper_only, "three_books": three_books, "deleted": deleted, "paused": paused, "voided": voided,
         "credits": credits, "forward": forward, "big": big, "new": new}


def _usd(v) -> str:
    c = Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return ("-$%.2f" % -c) if c < 0 else "$%.2f" % c


_USER_PAUSES = {"You paused it": "the user paused it", "You pressed Kill": "the user pressed Kill",
                "You approved the agent’s proposal to pause it": "the user approved a proposal to pause it"}


def _facts(pages, brief):
    """[(fact, the words the context must hold, from the page's figure)] for one account."""
    from app.trading.markets import customer_name

    d, f, ap, bill, dep = pages["dashboard"], pages["funds"], pages["autopilot"], pages["billing"], pages["deployments"]
    out = [
        ("Home: realized all time, paper", "paper %s over %d closed trades, all time" % (_usd(d["paper"]["realized_pnl"]), d["paper"]["closed_trades"])),
        ("Home: realized all time, live", "live (real money) %s over %d closed trades, all time" % (_usd(d["live"]["realized_pnl"]), d["live"]["closed_trades"])),
        ("Home: realized, demo", "demo %s %s" % (_usd(d["demo"]["realized_pnl"]), "since its last reload" if d["demo"]["before_reload"] else "all time")),
        ("Home: demo closed trades", "Demo has %d closed trades all time." % d["demo"]["closed_trades"]),
        # Home's label is "realized today (UTC)"; the window is 00:00 UTC to now on both.
        ("Home: realized today (UTC) per book", "Realized today (UTC, since 00:00 UTC): paper %s; demo %s; live (real money) %s." % (
            _usd(d["paper"]["today_pnl"]), _usd(d["demo"]["today_pnl"]), _usd(d["live"]["today_pnl"]))),
        ("Funds: paper headline", "Paper portfolio %s (Home's and Funds' headline: available cash %s plus open positions at cost %s" % (
            _usd(f["paper"]["equity"]), _usd(f["paper"]["cash"]), _usd(f["paper"]["open_at_cost"]))),
        ("Funds: paper cash", "paper cash %s," % _usd(f["paper"]["cash"])),
        ("Funds: open at cost per book", "paper %d open, %s entry notional; demo %d open, %s entry notional; live %d open, %s entry notional" % (
            f["paper"]["open_positions"], _usd(f["paper"]["open_at_cost"]), f["demo"]["open_positions"], _usd(f["demo"]["open_at_cost"]),
            f["kalshi"]["open_positions"], _usd(f["kalshi"]["open_at_cost"]))),
        ("Card: strategies running", "%d running across the books; by book: %d on paper, %d on demo, %d live" % (
            ap["trading_strategies"], ap["paper_running"], ap["demo_running"], ap["live_running"])),
        ("Home: research now", "%d running, %d queued to start, %d paused (the Research page's count)" % (
            d["agents"]["research_running"], d["agents"]["research_queued"], d["agents"]["research_paused"])),
        ("Billing: spent 24h and 7 days, split", "last 24 hours $%.2f; last 7 days $%.2f, of which your research sessions (the user's own) "
         "$%.2f, autopilot research $%.2f, autopilot check-ins $%.2f, chat $%.2f" % (
             bill["spent_24h"], bill["spent_7d"], bill["spent_7d_by"]["research"], bill["spent_7d_by"]["autopilot_research"],
             bill["spent_7d_by"]["autopilot_review"], bill["spent_7d_by"]["chat"])),
        ("Billing: credits left", "research credits %s left" % _usd(bill["balance"])),
    ]
    if f["kalshi_dev"]["connected"]:
        out.append(("Home: demo book", "Tau's demo book %s" % _usd(d["demo"]["equity"])))
    if f["kalshi"]["connected"]:
        k = f["kalshi"]
        out.append(("Funds: live cash at Kalshi", "Live (real money) cash at Kalshi: %s, as Funds and Home show it" % _usd(k["balance"])
                    if k.get("balance") is not None else "Live (real money) cash at Kalshi: not in this context."))
    else:
        out.append(("Funds: no live key", "no Kalshi key connected, so there is no live cash to show"))
    if ap.get("research_cap"):
        cap = ap["research_cap"]
        out += [("Card: research cap", "Autopilot research cap (the autopilot card's): $%.2f per 24 hours" % cap["usd"]),
                ("Card: cap's count", "cost $%.2f by the cap's count" % cap["spent_24h"]),
                ("Card: cap reached", "REACHED the cap" if cap["reached"] else "it can still start research")]
    else:
        out.append(("Card: no cap shown", ("Autopilot research cap", False)))
    if ap.get("week"):
        w = ap["week"]
        out += [("Card: research this week", "together, $%.2f (the autopilot card's Research figure)" % w["research_credits"]),
                ("Card: autopilot's paper week", "%d put on paper, %s realized over %d closed paper trades." % (
                    w["paper_started"], _usd(w["paper_pnl"]), w["paper_trades"]))]
    paused_rows = [r for r in dep["deployments"] if r["status"] == "paused"]
    out.append(("Home: paused count", "Paused: %s" % ("none." if not paused_rows else "%d (" % len(paused_rows))))
    for r in dep["deployments"]:
        line = next((x for x in brief.splitlines() if x.startswith("- deployment_id=%d " % r["id"])), "")
        name = "Deploy: “%s” on %s" % (r["title"], r["mode"])
        out.append((name + ", name and market", ("- deployment_id=%d \"%s\" market=%s (%s) mode=%s" % (
            r["id"], r["title"], r["market"], customer_name(r["market"]), r["mode"]), line.startswith)))
        out.append((name + ", realized and trades", ("%d closed trades, %d wins, realized %s, %d open" % (
            r["closed_trades"], r["wins"], _usd(r["realized_pnl"]), r["open_positions"]), line.__contains__)))
        if r["status_why"]:
            why = _USER_PAUSES.get(r["status_why"], r["status_why"]).rstrip(".")
            out.append((name + ", why paused", ("status=%s (why: %s)" % (r["status"], why), line.__contains__)))
        road = r.get("road") or {}
        if road.get("forward_sample"):
            # Deploy's figure, in Deploy's words (road_ahead.forward_words): its N of M and its estimate, word for word.
            out.append((name + ", forward test", "first its paper forward test: " + road_ahead.forward_words(road)))
    for sid, page in pages["strategies"].items():
        for mode, tot in (page.get("book_totals") or {}).items():
            out.append(("Strategy page: “%s” %s" % (page["title"], mode), "\"%s\" market=" % page["title"]))
            out.append(("Strategy page: “%s” %s realized" % (page["title"], mode), "%s record: %d closed trades" % (mode, tot["trades"])))
    books = dep.get("realized_by_book") or {}
    for mode, b in books.items():
        if b["removed"]:
            out.append(("Deploy: %s listed and removed" % mode, "%s %s from the deployments it lists plus %s from removed ones" % (
                mode, _usd(b["listed"]), _usd(b["removed"]))))
    if "demo" in books and not books["demo"]["removed"] and books["demo"]["listed"] != d["demo"]["realized_pnl"]:
        out.append(("Deploy: demo since the start", "demo %s, every closed demo trade since the book began" % _usd(books["demo"]["listed"])))
    orphans = [p for p in d["open_positions"] if p["title"] == "Deleted strategy"]
    if orphans:
        out.append(("Home: a deleted strategy's open positions", "“Deleted strategy”; they are in the open counts above but not in the "
                    "Deployments list): paper %d." % len(orphans)))
    listed = {x["id"]: x["market"] for x in dep["deployments"]}
    for row in d["recent_closed"]:
        where = " · " + customer_name(listed[row["deployment_id"]]) if row["deployment_id"] in listed else ""
        out.append(("Home: trade %s “%s” %s" % (row["mode"], row["title"], _usd(row["pnl"])), "· “%s”%s · %s %d · %s · %s" % (
            row["title"], where, row["side"].upper(), row["qty"], _usd(row["pnl"]), row["why"])))
    return out


def _pages(conn, user, cur):
    from app import server
    from app.web.routes import billing as billing_routes

    def ctx(*params):
        return server.Ctx(conn, user, tuple(str(p) for p in params), {}, {}, None)

    pages = {"dashboard": server.dashboard(ctx()), "funds": server.funds(ctx()), "autopilot": server.autopilot_status(ctx()),
             "billing": billing_routes.billing_figures(ctx()), "deployments": server.list_deployments(ctx())}
    pages["strategies"] = {r["strategy_id"]: server.get_strategy(ctx(r["strategy_id"])) for r in pages["deployments"]["deployments"]}
    return pages


@pytest.mark.parametrize("case", sorted(CASES))
def test_each_fact_the_chat_is_given_is_the_pages(conn, quiet, case):
    from app import repository as repo
    from app.research import agent

    cur = conn.cursor()
    s = Seed(cur, case, days=4 if case == "credits" else 30)
    CASES[case](s)
    user = repo.get_user(cur, s.uid)
    pages = _pages(conn, user, cur)
    brief = agent._copilot_context(cur, user)
    wrong = []
    for fact, want in _facts(pages, brief):
        if isinstance(want, tuple) and want[1] is False:
            ok = want[0] not in brief
        elif isinstance(want, tuple):
            ok = want[1](want[0])
        else:
            ok = want in brief
        if not ok:
            wrong.append("%s: wants %r" % (fact, want[0] if isinstance(want, tuple) else want))
    assert not wrong, "\n".join(wrong)
    # Never books together, never credits in P&L, no internal words in what a reply quotes.
    paper, demo, live = (pages["dashboard"][m]["realized_pnl"] for m in ("paper", "demo", "live"))
    for total in {round(paper + live, 2), round(paper + demo + live, 2), round(demo + live, 2)} - {paper, demo, live, 0.0}:
        assert "realized %s" % _usd(total) not in brief and "P&L %s" % _usd(total) not in brief
    assert "$-" not in brief
    assert not re.search(r"KX[A-Z0-9]+-", brief), "a raw contract ticker"
    assert not re.search(r"claude|anthropic|sonnet|opus|haiku|Traceback", brief, re.I)
    assert "Bitcoin above $X" not in brief  # the research model's label, never the long name


def test_the_contexts_reads_stay_bounded_as_the_account_grows(conn, quiet):
    # The chat's context is no hot endpoint, but its reads must not grow with an account's trades, debits or
    # sessions: each new fact is one aggregate read (one per paper deployment for its road, as before).
    import pymysql.cursors as pc

    from app import repository as repo
    from app.research import agent

    cur = conn.cursor()
    s = Seed(cur, "bounded")
    three_books(s)
    s.repo.set_autopilot(cur, s.uid, True)
    for k in range(2):  # recent research turns on the cooldown and long-term memory lines, a fixed set of reads
        s.run("high_ny", 3 + k, 100_000, 10_000, True, [0.5])
    dp = next(r["id"] for r in repo.list_deployments(cur, s.uid) if r["mode"] == "paper")
    for k in range(40):  # a paper record past the live check's floor reads its gate's few rows, a fixed set too
        s.closed(dp, "paper", "high_ny", 5, 0.4, 1.0 if k % 2 else 0.0, 200 + k)

    def count():
        seen = []
        orig = pc.Cursor.execute

        def ex(self, q, a=None):
            seen.append(q)
            return orig(self, q, a)
        pc.Cursor.execute = ex
        try:
            agent._copilot_context(cur, repo.get_user(cur, s.uid))
        finally:
            pc.Cursor.execute = orig
        return [" ".join(str(q).split())[:100] for q in seen]

    agent._copilot_context(cur, repo.get_user(cur, s.uid))  # module caches warm
    before = count()
    assert len(before) <= 160, len(before)
    for k in range(80):
        s.closed(dp, "paper", "high_ny", 5, 0.4, 1.0 if k % 2 else 0.0, 60 + k)
    for k in range(5):  # more of the same: a new market family adds its own fixed reads, more sessions none
        s.run("high_ny", 1 + k, 100_000, 10_000, True, [0.25, 0.125])
    after = count()
    from collections import Counter
    assert len(after) == len(before), Counter(after) - Counter(before)


def test_a_paper_forward_test_is_one_figure_on_deploy_its_page_the_panel_and_the_chat(conn, quiet):
    """Regression walk 2026-11-01: one strategy in its paper forward test read "8 of 40" on its ladder, "8 of 30 ... about 25
    days" in its Road to live a few lines lower, and "about 36 days" on Deploy. Deploy's road, its page's live-readiness
    `forward`, the autopilot panel's paper_progress and the chat's facts now carry one figure (road_ahead.forward_figure),
    and the chat says it in Deploy's words."""
    from app import repository as repo
    from app import server
    from app.research import agent

    cur = conn.cursor()
    s = Seed(cur, "forward1")
    CASES["forward"](s)
    # Nine days on paper, so its pace (12 trips in 9 days) gives an estimate in days, as the walk's did.
    cur.execute("UPDATE app_deployments SET created_at=%s WHERE user_id=%s", (s.now - timedelta(days=9), s.uid))
    user = repo.get_user(cur, s.uid)
    pages = _pages(conn, user, cur)
    row = next(r for r in pages["deployments"]["deployments"] if (r.get("road") or {}).get("forward_sample"))
    road = row["road"]
    page = server.strategy_live_readiness(server.Ctx(conn, user, (str(row["strategy_id"]),), {}, {}, None))["forward"]
    prog = pages["autopilot"]["paper_progress"]
    keys = ("trades", "events", "forward_sample", "forward_events_needed", "forward_eta_days", "then_trades_needed", "then_eta_days", "running")
    assert {k: page[k] for k in keys} == {k: road[k] for k in keys} == {k: prog[k] for k in keys}
    assert (road["trades"], road["forward_sample"], road["then_trades_needed"]) == (12, 40, None)
    # 28 more trips at 12 in 9 days: 21 days, never the live check's own 18 more (about 14 days).
    assert road["forward_eta_days"] == 21.0 and road["eta_days"] < road["forward_eta_days"]
    said = road_ahead.forward_words(road)
    assert said.startswith("12 of 40 trades · ") and "about 21 days at its pace" in said
    brief = agent._copilot_context(cur, user)
    assert "first its paper forward test: " + said in brief
    # Its forward test comes first in the facts, its live counts after it, said so.
    assert brief.index("first its paper forward test: ") < brief.index("then the live check, paper evidence: 12/30")
