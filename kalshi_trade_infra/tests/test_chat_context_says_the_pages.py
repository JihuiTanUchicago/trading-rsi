"""The chat's context (research.agent._copilot_context) says each fact as the page that shows it does: one guard per
fix of the 2026-11-01 audit, which built the context for seeded accounts and held each fact to Home, Funds, Deploy,
the strategy page, Research and Billing (the figures themselves are pinned against MySQL in
test_chat_context_vs_pages_db.py). No database: the repository reads are stubbed."""
import pytest

from app.research import agent
from app.services import chat_facts, deploy_rows
from app.trading import demo_twins
from app.trading.markets import MARKETS
from app.web import views
from app.web.routes import chat as chat_routes

USER = {"id": 7}


def _dep(i, mode="paper", status="running", reason=None, realized=0.0, strategy=None, market="btc15m", title="t"):
    return {"id": i, "strategy_id": strategy or i, "title": title, "market": market, "mode": mode, "status": status,
            "status_reason": reason, "closed_trades": 1, "wins": 0, "realized_pnl": realized, "open_positions": 0,
            "spec": {}, "result": {}, "direction": "momentum", "created_at": None}


def _wire(monkeypatch, deps=(), runs=(), user_extra=None, **over):
    r = agent.repo
    monkeypatch.setattr(r, "list_deployments", lambda cur, uid, **k: list(deps))
    monkeypatch.setattr(r, "list_strategies", lambda cur, uid, status=None, **k: [])
    monkeypatch.setattr(r, "user_risk_config", lambda cur, uid: {})
    monkeypatch.setattr(r, "user_execution_config", lambda cur, uid: {})
    monkeypatch.setattr(r, "list_runs", lambda cur, uid, limit=6: list(runs))
    monkeypatch.setattr(r, "paper_balance", lambda cur, uid: 1000.0)
    monkeypatch.setattr(r, "credit_balance", lambda cur, uid: 42.0)
    monkeypatch.setattr(r, "get_kalshi_dev_key", lambda cur, uid: over.get("dev_key"))
    monkeypatch.setattr(r, "has_kalshi_dev_key", lambda cur, uid: over.get("dev_key") is not None)
    monkeypatch.setattr(r, "has_kalshi_key", lambda cur, uid: over.get("live_key", False))
    monkeypatch.setattr(r, "credit_spent_total", lambda cur, uid, since: 0.0)
    monkeypatch.setattr(r, "usage_by_source", lambda cur, uid, since: dict.fromkeys(
        ("research", "autopilot_research", "autopilot_review", "chat", "other"), 0.0))
    for name in ("closed_book_totals", "demo_equity", "user_open_exposure", "autopilot_paper_week", "research_token_rows"):
        if name in over:
            monkeypatch.setattr(r, name, over[name])


# -- why a deployment is paused: Deploy's row's words -------------------------------------------------------------

def test_a_paused_rows_why_is_deploys_own_line(monkeypatch):
    # The context said the engine's "force-closed: position down 36.7% >= stop 30.0%", a demo copy that "stopped too"
    # while it is paused, and a loss guard without what Resume would do, beside Deploy's row for each.
    deps = [_dep(1, status="paused", reason="force-closed: position down 36.7% >= stop 30.0%"),
            _dep(2, status="paused", reason="Lost $89.31, more than the $20.00 guard (2x its worst backtest drawdown).",
                 realized=-90.79),
            _dep(3, status="paused", reason="By you", strategy=9),
            _dep(4, mode="demo", status="paused", reason=demo_twins.PAUSE_REASON, strategy=9)]
    _wire(monkeypatch, deps=deps, dev_key="k")
    ctx = agent._copilot_context(None, USER)
    books = deploy_rows._paper_books(deps)
    for d in deps[:2] + deps[3:]:
        why = views._deployment_out(d, [], paper_books=books)["status_why"]
        assert "status=paused (why: %s)" % why.rstrip(".") in ctx, why
    assert "Its safety stop sold a position that had lost 37% of its cost" in ctx
    assert "Resume would pause it again at its next check" in ctx
    assert ">= stop" not in ctx and "stopped too" not in ctx
    # A pause the user made stays in the third person: Deploy's "You paused it" would read as the model's own doing.
    assert "status=paused (why: the user paused it)" in ctx


def test_views_keeps_the_moved_row_lines():
    # Moved to services.deploy_rows so research can read them; the pages and their tests keep reading views' names.
    for name in ("_status_why", "_demo_copy_pause", "_paper_books", "_demo_key_back", "_pause_clause",
                 "_plain_pause_reason", "_DEMO_KEY_PAUSE", "row_status_why"):
        assert getattr(views, name) is getattr(deploy_rows, name)


# -- live cash: what Funds and Home show, or where to see it ------------------------------------------------------

def test_a_live_account_hears_where_its_kalshi_cash_is(monkeypatch):
    from app.adapters.kalshi import kalshi_account

    _wire(monkeypatch, live_key=True)
    monkeypatch.setattr(kalshi_account, "balance_kept", lambda uid: False)
    ctx = agent._copilot_context(None, USER)
    assert "Live (real money) cash at Kalshi: not in this context." in ctx and "never state or estimate" in ctx
    from datetime import datetime
    monkeypatch.setattr(kalshi_account, "balance_kept", lambda uid: True)
    monkeypatch.setattr(kalshi_account, "balance_memo", lambda uid: (2345.675, datetime(2026, 11, 1, 18, 5)))
    ctx = agent._copilot_context(None, USER)
    assert "Live (real money) cash at Kalshi: $2345.68, as Funds and Home show it (read from Kalshi at Nov 1 18:05 UTC)" in ctx
    _wire(monkeypatch, live_key=False)
    assert "no Kalshi key connected, so there is no live cash to show" in agent._copilot_context(None, USER)


# -- Deploy's board: the removed deployments' part, and demo from the start --------------------------------------

def test_deploys_board_split_is_said_where_it_differs_from_the_dashboards_figure(monkeypatch):
    # "paper $7.85" beside Deploy's "$5.42" with nothing about the removed strategy's $2.43; demo "$5.67 since its
    # last reload" beside Deploy's demo row's $11.17.
    deps = [_dep(1, realized=5.4226), _dep(2, mode="demo", realized=11.1704), _dep(3, mode="live", realized=-1.0251)]
    _wire(monkeypatch, deps=deps, dev_key="k",
          closed_book_totals=lambda cur, uid, day: ({"paper": (7.8501, 0.66), "demo": (11.1704, 2.86), "live": (-1.0251, 0.0)},
                                                    {"paper": 3, "demo": 5, "live": 4}, {}),
          demo_equity=lambda cur, uid, realized=None: {"equity": 505.67, "realized": 5.67, "before_reload": 5.50})
    ctx = agent._copilot_context(None, USER)
    removed = [{"mode": "paper", "realized_pnl": 2.4275}]
    board = views._realized_by_book(deps, removed)
    assert board["paper"] == {"listed": 5.42, "removed": 2.43, "removed_deployments": 1, "all": 7.85}
    assert "paper $5.42 from the deployments it lists plus $2.43 from removed ones" in ctx
    assert "demo $%.2f, every closed demo trade since the book began" % board["demo"]["listed"] in ctx
    assert "live $" not in ctx.split("Deploy's board says")[1].split("\n")[0]  # live has nothing removed: nothing to say
    assert chat_facts.deploy_book_lines(deps[:1], {"paper": (5.4226, 0)}, 0, 0) == []


# -- credits: the autopilot card's research week, and its cap only while autopilot is on ---------------------------

class _Repo:
    def __init__(self, by):
        self.by = by

    def now_utc(self):
        from datetime import datetime
        return datetime(2026, 11, 1, 12)

    def credit_spent_total(self, cur, uid, since):
        return sum(self.by.values())

    def usage_by_source(self, cur, uid, since):
        return dict(self.by)


def test_the_research_week_is_the_autopilot_cards_figure(monkeypatch):
    by = {"research": 40.936666, "autopilot_research": 59.066899, "autopilot_review": 0.2133, "chat": 0.0408, "other": 0.0}
    monkeypatch.setattr(chat_facts, "repo", _Repo(by))
    [spent] = chat_facts.credits_lines(None, 1)
    card = chat_routes._research_cents({"research": by["research"] + by["autopilot_research"], "by_source": by})
    assert card == 100.01
    assert "Research in all over the last 7 days, your sessions and the autopilot's together, $100.01" in spent
    assert "your research sessions (the user's own) $40.94, autopilot research $59.07" in spent


def test_the_cap_and_the_autopilots_week_ride_only_while_autopilot_is_on(monkeypatch):
    rows = []
    _wire(monkeypatch, research_token_rows=lambda cur, uid, since: rows.append(uid) or [],
          autopilot_paper_week=lambda cur, uid, since: {"started": 2, "pnl": -3.525, "trades": 9, "fills": 18})
    off = agent._copilot_context(None, USER)
    assert "Autopilot research cap" not in off and "autopilot's own paper strategies" not in off and rows == []
    on = agent._copilot_context(None, dict(USER, autopilot=1))
    assert "Autopilot research cap (the autopilot card's): $%.2f per 24 hours" % agent.AUTOPILOT_RESEARCH_USD_PER_DAY in on
    assert ("The autopilot's own paper strategies, last 7 days (the autopilot card's figures; strategies the user "
            "deployed aren't in them): 2 put on paper, -$3.53 realized over 9 closed paper trades.") in on


# -- names: the long market name every page uses; the example session isn't the user's -----------------------------

def test_markets_are_named_by_their_customer_label(monkeypatch):
    _wire(monkeypatch, deps=[_dep(1, market="btc_daily", mode="live")])
    ctx = agent._copilot_context(None, USER)
    label = MARKETS["btc_daily"].customer_label
    assert "$X" in MARKETS["btc_daily"].label and "$X" not in label
    assert 'market=btc_daily (%s) mode=live' % label in ctx
    assert "btc_daily (%s)" % label in ctx.split("Markets you can research AND deploy")[1]
    assert "Bitcoin above $X" not in ctx


def test_the_example_session_is_not_listed_as_the_users_research(monkeypatch):
    from app import repository as repo
    _wire(monkeypatch, runs=[{"id": 2, "market": "btc15m", "status": "succeeded", "hypotheses_tried": 2,
                              "strategies_passed": 0, "prompt": "Generate a random walk", "direction": repo.DEMO_DIRECTION}])
    ctx = agent._copilot_context(None, USER)
    assert "Recent research lines" not in ctx and "random walk" not in ctx


def test_the_scorecards_dollars_say_they_are_estimates_not_what_research_cost():
    card = {"runs": 13, "cost_usd": 60.97, "gated": 357, "passed": 81, "adherence": None,
            "families": [{"family": "crypto", "runs": 6, "cost_usd": 42.21, "gated": 343, "usd_per_gated": 0.12}],
            "burning": []}
    head = agent.autopilot_scorecard_lines(card)[0]
    assert "estimates priced from each run's AI usage" in head and "never quote them to the user as a cost" in head


@pytest.mark.parametrize("net,said", [(3.525, "$3.53"), (-3.525, "-$3.53"), (8.3349, "$8.33")])
def test_paper_evidence_net_is_rounded_as_the_pages_round(net, said):
    ev = {"trades": 5, "trades_needed": 30, "net_pnl": net, "span_hours": 47.0, "ready": False, "why": "x"}
    assert agent.paper_evidence_line(ev).split("; ")[1] == "%s net of fees and spread" % said
