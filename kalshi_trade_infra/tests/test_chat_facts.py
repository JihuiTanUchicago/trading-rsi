"""The chat's answers about money and status say what the pages say (audit 2026-11-01).

A chat reply is the model quoting its context (agent._copilot_context), and the panel shows the reply. On a heavy
account (paper and demo books, a deleted strategy still holding a position, research running and queued, a billing
ledger, autopilot on under its $240 cap) the context counted "6 on paper" beside Home's "7 strategies running", had a
2-sessions-running figure beside Home's "1 queued", only the credits left for "how much did autopilot cost me?",
paper cash for "what is my balance?" beside Home's paper portfolio, no trade for "why did this trade lose?", the
seeded example's strategies as the user's dead ends, and "$-0.20" where the pages say "-$0.20". The reply itself
reached the panel as said: "#7 on btc15m", "run #9", a model id, a raw exception. These pin each, without a database
(tests/test_chat_facts_db.py holds them against the real pages)."""
from datetime import datetime

from app.research import agent
from app.services import chat_facts, customer_text
from tests.test_copilot_context import USER, _dep, _wire


def _d(i, sid, mode="paper", status="running", title="t", market="btc15m"):
    return dict(_dep(i, mode=mode, status=status), strategy_id=sid, title=title, market=market)


# -- what is running ------------------------------------------------------------------------------------------


def test_running_counts_strategies_per_book_and_across_them_as_home_and_the_card_do():
    deps = [_d(1, 10), _d(2, 11), _d(3, 10, mode="demo"), _d(4, 12, mode="demo"), _d(5, 13, status="paused", title="Dogs"),
            _d(6, 14, mode="live"), _d(7, 15, market="btc_perp")]
    [line] = chat_facts.running_lines(deps, live_open=False)
    # Home: paper 10, 11 (+ the perp's research-only one left out) and demo 10, 12 -> 3 strategies; live waits.
    assert "3 running across the books; by book: 2 on paper, 2 on demo, 1 live" in line
    assert "the live ones wait: live trading isn’t open yet" in line and "Paused: 1 (“Dogs” on paper)." in line
    [line] = chat_facts.running_lines(deps, live_open=True)
    assert "4 running across the books" in line and "wait" not in line


def test_the_context_counts_demo_deployments_and_strategies_once(monkeypatch):
    deps = [_d(1, 10), _d(2, 11), _d(3, 10, mode="demo")]
    _wire(monkeypatch, deps=deps)
    ctx = agent._copilot_context(None, USER)
    assert "DEPLOYMENTS RUNNING RIGHT NOW: 2 on paper (#1, #2), 1 on demo (#3), 0 live." in ctx
    assert "2 running across the books; by book: 2 on paper, 1 on demo, 0 live" in ctx


def test_research_now_says_running_queued_and_paused_apart(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(agent.repo, "active_run_counts", lambda cur, uid, silent_before=None: {
        "research_running": 1, "research_queued": 1, "research_paused": 0, "chat_active": 0})
    monkeypatch.setattr(agent.repo, "count_runs", lambda *a, **k: 2)
    ctx = agent._copilot_context(None, USER)
    assert "RESEARCH RUNNING RIGHT NOW: 1 running, 1 queued to start, 0 paused (the Research page's count)." in ctx
    assert "RESEARCH RUNNING RIGHT NOW: 2." not in ctx


def test_the_context_says_live_autopilot_and_that_only_the_user_flips_either(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(agent, "_live_switch_open", lambda: False)
    ctx = agent._copilot_context(None, dict(USER, autopilot=1, autopilot_live=0))
    assert ctx.startswith("Autopilot is ON.\nLive autopilot is OFF; live trading isn’t open on this server yet. Only the "
                          "user turns autopilot or live autopilot on or off")
    assert "you can't" in ctx and "You can't turn autopilot or live autopilot on or off" in agent.ACTIONS_GUIDE


def test_dead_ends_leave_out_the_seeded_example_session(monkeypatch):
    _wire(monkeypatch, strat={"rejected": [{"title": "Todd's random walk", "market": "btc15m", "status": "rejected",
                                            "direction": "random_walk"},
                                           {"title": "Momentum chase", "market": "eth_daily", "status": "rejected",
                                            "direction": "custom"}]})
    ctx = agent._copilot_context(None, USER)
    assert "Momentum chase" in ctx and "Todd's random walk" not in ctx


def test_a_loss_reads_with_the_sign_before_the_dollar_everywhere_in_the_context(monkeypatch):
    from app.research import tournament
    _wire(monkeypatch)
    st = {"entrants": [dict(strategy_id=8, title="EPL draws", market="epl_game", weight=0.1, trips=1, net_pnl=-0.2,
                            p_positive=0.49, verdict=None)],
          "pooled": {"trips": 1, "target": 400, "mean_return": None, "p_value": None, "per_day": 0, "days_to_target": None},
          "book": {"capital": 10000.0, "pnl": 0.0, "return_pct": 0.0, "max_drawdown": 0.0, "trips": 0}}
    monkeypatch.setattr(tournament, "standings", lambda cur, uid: st)
    ctx = agent._copilot_context(None, USER)
    assert "- strategy_id=8 EPL draws (epl_game): 10% of the book, 1 events, net -$0.20" in ctx
    assert "$-" not in ctx and "- #8 " not in ctx


# -- money: credits, balance, trades ----------------------------------------------------------------------------


class _Repo:
    def __init__(self, week, day, by):
        self.week, self.day, self.by = week, day, by

    @staticmethod
    def now_utc():
        return datetime(2026, 11, 1, 18, 0)

    def credit_spent_total(self, cur, uid, since):
        return self.day if (self.now_utc() - since).days < 2 else self.week

    def usage_by_source(self, cur, uid, since):
        return dict(self.by)


def test_credits_lines_are_billings_figures_and_the_cards_cap(monkeypatch):
    by = {"research": 0.0, "autopilot_research": 255.0, "autopilot_review": 0.40, "chat": 0.0804, "other": 0.003}
    monkeypatch.setattr(chat_facts, "repo", _Repo(255.4834, 180.2867, by))
    spent, cap = chat_facts.credits_lines(None, 1, 240.0, 180.0049)
    # Billing: spent_24h, spent_7d and spent_7d_by (credit_split, the parts adding up to the week as shown).
    assert ("last 24 hours $180.29; last 7 days $255.48, of which your research sessions (the user's own) $0.00, "
            "autopilot research $255.00, autopilot check-ins $0.40, chat $0.08, other agent calls under $0.01") in spent
    assert "NOT trading P&L" in spent
    assert "$240.00 per 24 hours" in cap and "cost $180.00 by the cap's count" in cap and "can still start research" in cap
    _, cap = chat_facts.credits_lines(None, 1, 240.0, 241.0)
    assert "REACHED the cap" in cap
    assert len(chat_facts.credits_lines(None, 1, 0.0, None)) == 1  # no cap: no cap line


def test_the_paper_headline_is_homes_cash_plus_open_at_cost_rounded_once():
    line = chat_facts.paper_portfolio_line(2000.0049, chat_facts.open_notional({"notional": 30.0751}))
    assert line.startswith("Paper portfolio $2030.08 (Home's and Funds' headline: available cash $2000.00 plus open "
                           "positions at cost $30.08")


def test_a_deleted_strategys_open_position_is_named_as_the_pages_name_it():
    rows = [{"mode": "paper", "title": None}, {"mode": "paper", "title": "Late fade"}, {"mode": "demo", "title": None}]
    [line] = chat_facts.orphan_lines(rows)
    assert "“Deleted strategy”" in line and "paper 1; demo 1" in line and "no longer sells or settles" in line
    assert chat_facts.orphan_lines(rows[1:2]) == []


def test_recent_trades_carry_the_pages_reason_line_and_a_cancelled_markets_payout():
    deps = [_d(5, 7, title="NBA road dogs", market="nba_game")]
    closed = [{"deployment_id": 5, "status": "closed", "mode": "paper", "title": "NBA road dogs", "side": "yes", "qty": 10, "entry_price": 0.36,
               "exit_price": 0.5, "close_reason": "settled", "pnl": 1.2348, "closed_at": datetime(2026, 10, 31, 3, 0)},
              {"deployment_id": 99, "status": "closed", "mode": "paper", "title": None, "side": "no", "qty": 5, "entry_price": 0.4,
               "exit_price": 0.27, "close_reason": "stop_loss", "pnl": -0.7450, "closed_at": datetime(2026, 10, 30, 3, 0)}]
    head, void, gone = chat_facts.recent_trades_lines(closed, deps, lambda k: {"nba_game": "NBA game winner"}[k])
    assert "never add books together" in head
    assert void == ("- Oct 31 03:00 UTC · paper · “NBA road dogs” · NBA game winner · YES 10 · $1.23 · Held to settlement: "
                    "Kalshi cancelled this market and paid 50¢ a contract")
    assert "“Deleted strategy”" in gone and "-$0.75" in gone and "stop-loss" in gone


# -- the reply as the panel shows it ----------------------------------------------------------------------------

DEPS = {7: {"title": "Late fade", "mode": "paper"}, 11: {"title": "Late fade", "mode": "demo"},
        5: {"title": "NBA road dogs", "mode": "paper"}}
STRATS = {3: "NYC morning forecast edge"}
MARKETS = {"btc15m": "Bitcoin up or down in 15 minutes", "KXHIGHCHI": "Chicago daily high temperature"}


def test_a_reply_names_deployments_strategies_sessions_and_markets_in_words():
    said = customer_text.agent_reply("#7 is up $5.32 on btc15m and deployment_id=5 (NBA road dogs) is paused; "
                                     "strategy_id=3 leads. run #9 on KXHIGHCHI failed. #11 made $5.42.",
                                     DEPS, STRATS, MARKETS)
    assert said == ("“Late fade” on paper is up $5.32 on Bitcoin up or down in 15 minutes and “NBA road dogs” is "
                    "paused; “NYC morning forecast edge” leads. A research session on Chicago daily high temperature "
                    "failed. “Late fade” on demo made $5.42.")
    assert not customer_text.leaks(said) and "#" not in said
    # A title already beside its id keeps one name; a number that isn't one of the user's ids stays (a list number).
    assert customer_text.agent_reply('"Late fade" (#7) is up.', DEPS) == '"Late fade" is up.'
    assert customer_text.agent_reply("Top 3: #1 is the spread.", DEPS) == "Top 3: #1 is the spread."
    assert customer_text.agent_reply("experiment #12 held out.", DEPS) == "Its research record held out."


def test_a_reply_never_shows_a_model_id_or_a_raw_error():
    assert customer_text.agent_reply("I run on us.anthropic.claude-opus-4-6-v1.") == "I run on the AI model."
    assert customer_text.agent_reply("It failed: botocore.exceptions.ReadTimeoutError: x") == customer_text.CHAT_FALLBACK
    # And a reply stored before agent_reply existed is cleaned on the way out.
    rows = customer_text.chat_rows([{"role": "agent", "content": "Model: us.anthropic.claude-opus-4-6-v1"},
                                    {"role": "user", "content": "us.anthropic.claude-opus-4-6-v1?"}])
    assert rows[0]["content"] == "Model: the AI model" and rows[1]["content"] == "us.anthropic.claude-opus-4-6-v1?"


def test_the_chat_run_posts_the_reply_in_words(monkeypatch):
    from tests.test_app_agent import Conn, Store
    Store(monkeypatch)
    saved = []
    _wire(monkeypatch, deps=[_d(7, 9, title="Late fade"), _d(11, 9, mode="demo", title="Late fade")])
    monkeypatch.setattr(agent.billing, "charge_usage", lambda *a: None)
    monkeypatch.setattr(agent.repo, "get_user", lambda cur, uid: {"id": uid})
    monkeypatch.setattr(agent.repo, "chat_messages", lambda *a, **k: [{"role": "user", "content": "how is Late fade?"}])
    monkeypatch.setattr(agent.repo, "add_chat", lambda *a: saved.append(a))
    reply = '{"reply":"#7 made $5.32 on btc15m and #11 made $5.42.","actions":[]}'
    agent.run_copilot(Conn(), {"id": 1, "user_id": 7, "kind": "chat"},
                      model=lambda s, m, on_delta=None: (reply, {"input_tokens": 10, "output_tokens": 5}))
    assert [a[3] for a in saved if a[2] == "agent"] == [
        "“Late fade” on paper made $5.32 on Bitcoin up or down in 15 minutes and “Late fade” on demo made $5.42."]
