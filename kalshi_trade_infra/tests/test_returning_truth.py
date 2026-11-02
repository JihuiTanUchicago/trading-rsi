"""What a reader back after days away is told about paper strategies and failed sessions (walk 2026-11-01).

- A paper strategy whose one later judgment (the one-time holdout, or a probation strategy's one judgment on its own
  paper trades) is in and didn't promote it can no longer be cleared to trade (the deployment reads' cleared_never
  column). "Furthest on paper" picks among the others, the story says so when none can be cleared, and a Deploy row's
  road carries `cleared_never`.
- The since-visit strip's failed sessions say how many the autopilot started (`runs_ap_failed`): it starts its next
  session itself, so those need nothing from the reader.
Display only: the gatekeeper, forward_gate and the live gate are unchanged, and no request reads more statements.
"""
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from app.adapters.db import deployment_repository as deprepo
from app.services import customer_text
from app.web.routes import chat, deployments


class FakeCursor:
    def __init__(self, rows):
        self.rows, self.sql, self.params = rows, [], []

    def execute(self, sql, params=()):
        self.sql.append(" ".join(str(sql).split()))
        self.params.append(tuple(params))

    def fetchall(self):
        return list(self.rows)


def _clearance(results, experiment_id=None, mode="paper", experiments=((40, 8),)):
    """CLEARED_NEVER_COL for one deployment of strategy 8 (user 1), on an in-memory database holding `experiments`
    ((id, strategy id)) and `results` ((experiment id, phase, classification))."""
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE app_deployments (id INT, user_id INT, strategy_id INT, mode TEXT);"
                     "CREATE TABLE app_strategies (id INT, user_id INT, experiment_id INT);"
                     "CREATE TABLE app_experiments (id INT, user_id INT, strategy_id INT);"
                     "CREATE TABLE app_experiment_results (id INTEGER PRIMARY KEY, experiment_id INT, phase TEXT, classification TEXT);")
    db.execute("INSERT INTO app_deployments VALUES (5, 1, 8, ?)", (mode,))
    db.execute("INSERT INTO app_strategies VALUES (8, 1, ?)", (experiment_id,))
    db.executemany("INSERT INTO app_experiments VALUES (?, 1, ?)", experiments)
    db.executemany("INSERT INTO app_experiment_results (experiment_id, phase, classification) VALUES (?, ?, ?)", results)
    return db.execute("SELECT " + deprepo.CLEARED_NEVER_COL + " FROM app_deployments d JOIN app_strategies s ON s.id=d.strategy_id").fetchone()[0]


def test_a_judgment_in_without_a_promotion_can_never_be_cleared():
    val = (40, "validation", "inconclusive")
    assert _clearance([val]) is None  # nothing judged after validation yet: its paper record may still clear it
    assert _clearance([val, (40, "paper", "inconclusive")]) == "paper"
    assert _clearance([val, (40, "paper", "rejected")]) == "paper"
    assert _clearance([(40, "validation", "statistically_supported"), (40, "holdout", "inconclusive")]) == "holdout"
    # A pass the diversification check held back stays off the candidate list for good, and says it passed: Deploy read
    # "its one judgment ... didn't clear it" beside its page's "passed its one judgment there ... Held back".
    assert _clearance([(40, "holdout", "forward_validated")]) == "holdout_held"
    assert _clearance([val, (40, "paper", "forward_validated")]) == "paper_held"
    # Promoted, under 'forward' (the gatekeeper's) or as the seed writes it: it can be cleared.
    assert _clearance([(40, "holdout", "forward_validated"), (40, "forward", "deployment_candidate")]) is None
    assert _clearance([(40, "paper", "forward_validated"), (40, "paper", "deployment_candidate")]) is None
    assert _clearance([(40, "paper", "inconclusive")], mode="demo") is None  # a paper row's question only
    assert _clearance([]) is None  # no gatekeeper record at all: not this column's to say
    # Its experiment as experiments_for_strategy resolves it: the stored one first, else the newest linked to it.
    two = ((40, 8), (41, 8))
    assert _clearance([(40, "paper", "inconclusive")], experiments=two) is None
    assert _clearance([(40, "paper", "inconclusive")], experiment_id=40, experiments=two) == "paper"
    assert _clearance([(41, "paper", "inconclusive")], experiments=two) == "paper"


def test_every_deployment_read_a_paper_line_needs_carries_it_in_its_own_statement():
    for read in (deprepo.list_deployments_brief, deprepo.list_deployments_for_display, deprepo.deployment_rows_for_display):
        cur = FakeCursor([])
        read(cur, 1)
        assert len(cur.sql) == 1 and "AS cleared_never" in cur.sql[0], read.__name__
        assert cur.sql[0].count("%s") == len(cur.params[0])


def test_furthest_on_paper_skips_one_that_cant_be_cleared_unless_none_can():
    prog = {8: (4, 8.63), 3: (3, 7.33), 7: (3, 1.10)}
    assert chat._furthest([3, 7, 8], prog, {8: "paper"}) == (3, [3, 7])
    assert chat._furthest([3, 7, 8], prog, {}) == (8, [3, 7, 8])
    assert chat._furthest([8], prog, {8: "paper"}) == (8, [])


def _story(paper, prog):
    d = {"autopilot": True, "paper_running": paper, "live_running": 0, "paper_progress": prog,
         "research": {"active": [], "tested_7d": 0, "passed_7d": 0}}
    return chat._autopilot_story(d)


def test_the_story_never_counts_an_uncleared_strategy_toward_live():
    prog = {"strategy_id": 8, "title": "NBA road dogs", "trades": 4, "trades_needed": 30, "net_pnl": 8.63, "strategies": 1,
            "cleared_never": "paper"}
    lines = _story(1, prog)
    assert ("1 strategy is on paper (pretend money), but it can’t be cleared to trade: its one-time judgment is in and it "
            "wasn’t cleared. It has 4 realistic trades on paper, up $8.63 on them %s." % customer_text.PAPER_NET_SHORT) in lines
    many = _story(4, prog)
    assert any(line.startswith("4 strategies are on paper (pretend money), but none can be cleared to trade: each one’s "
                               "one-time judgment is in and none was cleared. The furthest along has 4 realistic trades on paper")
               for line in many)
    # "didn't clear it" was wrong for a pass the diversification check held back; the line holds for both.
    for line in _story(1, dict(prog, cleared_never="paper_held")) + many:
        assert "didn’t clear" not in line
    for line in lines + many:
        assert "live needs" not in line and "soonest" not in line and "gatekeeper" not in line
    # One that can still be cleared reads as before.
    del prog["cleared_never"]
    assert any("has 4 of the 30 realistic paper trades live needs" in line for line in _story(1, prog))


def test_a_deploy_row_of_a_strategy_that_cant_be_cleared_says_so(monkeypatch):
    deployments._ROADS.clear()
    monkeypatch.setattr(deployments.repo, "paper_round_trips_by_strategy", lambda cur, uid, sids: {})
    deps = [{"id": 5, "strategy_id": 8, "mode": "paper", "status": "running", "created_at": datetime(2026, 10, 25), "cleared_never": "paper"},
            {"id": 1, "strategy_id": 3, "mode": "paper", "status": "running", "created_at": datetime(2026, 10, 25), "cleared_never": None}]
    roads = deployments._deploy_roads(None, 1, deps)
    deployments._ROADS.clear()
    assert roads[8]["cleared_never"] == "paper"
    assert "cleared_never" not in roads[3]
    assert roads[8]["trades_needed"] == roads[3]["trades_needed"]  # the counts stay; the board says the line instead


def test_the_strip_counts_the_failed_sessions_the_autopilot_started_in_its_one_read(monkeypatch):
    monkeypatch.setattr(deprepo, "now_utc", lambda: datetime(2026, 11, 1))
    cur = FakeCursor([{"src": "run", "k": "failed", "n": 2}, {"src": "run", "k": "succeeded", "n": 3},
                      {"src": "apfail", "k": "failed", "n": 1}])
    out = deprepo.activity_since(cur, 1, datetime(2026, 10, 29), silent_before=datetime(2026, 11, 1) - timedelta(minutes=5), active=True)
    assert out["runs"] == {"failed": 2, "succeeded": 3}
    assert out["runs_ap_failed"] == 1
    assert len(cur.sql) == 1  # the same one statement
    sql = cur.sql[0]
    assert "brief=1 AND status='failed'" in sql
    assert len(re.findall(r"%s", sql)) == len(cur.params[0])
    assert deprepo.activity_since(FakeCursor([]), 1, datetime(2026, 10, 29))["runs_ap_failed"] == 0


# -- road to live walk (2026-11-01): the paper forward test and a pass held back --------------------------------------

def _forward(results, min_sample=40, experiment_id=None, mode="paper", experiments=((40, 8),)):
    """FORWARD_SAMPLE_COL for one deployment of strategy 8 (user 1): `experiments` ((id, strategy id)) all with
    `min_sample`, `results` ((experiment id, phase, classification))."""
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE app_deployments (id INT, user_id INT, strategy_id INT, mode TEXT);"
                     "CREATE TABLE app_strategies (id INT, user_id INT, experiment_id INT);"
                     "CREATE TABLE app_experiments (id INT, user_id INT, strategy_id INT, min_sample INT);"
                     "CREATE TABLE app_experiment_results (id INTEGER PRIMARY KEY, experiment_id INT, phase TEXT, classification TEXT);")
    db.execute("INSERT INTO app_deployments VALUES (5, 1, 8, ?)", (mode,))
    db.execute("INSERT INTO app_strategies VALUES (8, 1, ?)", (experiment_id,))
    db.executemany("INSERT INTO app_experiments VALUES (?, 1, ?, ?)", [(e, s, min_sample) for e, s in experiments])
    db.executemany("INSERT INTO app_experiment_results (experiment_id, phase, classification) VALUES (?, ?, ?)", results)
    return db.execute("SELECT " + deprepo.FORWARD_SAMPLE_COL + " FROM app_deployments d JOIN app_strategies s ON s.id=d.strategy_id").fetchone()[0]


def test_a_probation_strategy_still_facing_its_paper_judgment_carries_its_minimum_sample():
    """The judgment gatekeeper.judge_paper_forward makes once: none yet, no holdout, validation not supported, min_sample
    set. Deploy said "toward live: 22/30 trades · enough in about 2 days" for one whose judgment needed 40."""
    from app.services import chat_facts
    val = (40, "validation", "inconclusive")
    assert _forward([val]) == 40
    assert _forward([val], min_sample=0) is None
    assert _forward([val], mode="demo") is None  # a paper row's question only
    assert _forward([val, (40, "paper", "inconclusive")]) is None  # judged: cleared_never says the rest
    assert _forward([val, (40, "paper", "forward_validated")]) is None
    assert _forward([(40, "validation", "statistically_supported")]) is None  # the holdout path, not the paper one
    assert _forward([(40, "validation", "statistically_supported"), (40, "holdout", "forward_validated"),
                     (40, "forward", "deployment_candidate")]) is None
    assert _forward([]) == 40  # the gate judges one with no validation row too
    two = ((40, 8), (41, 8))
    assert _forward([(41, "paper", "inconclusive")], experiments=two) is None  # the newest linked one
    assert _forward([(41, "paper", "inconclusive")], experiment_id=40, experiments=two) == 40  # the stored one first
    # The chat's context judges alike, from the same rows.
    for rows in ([val], [val, (40, "paper", "inconclusive")], [val, (40, "paper", "forward_validated")],
                 [(40, "holdout", "inconclusive")], [(40, "holdout", "forward_validated")],
                 [(40, "holdout", "forward_validated"), (40, "forward", "deployment_candidate")],
                 [(40, "validation", "statistically_supported")], []):
        dicts = [{"phase": ph, "classification": c} for _e, ph, c in rows]
        never, fwd = _clearance(rows), _forward(rows)
        assert chat_facts.paper_stage(dicts, 40) == (never or ("forward" if fwd else "")), rows


def test_every_deployment_read_a_paper_line_needs_carries_the_forward_sample_in_its_own_statement():
    for read in (deprepo.list_deployments_brief, deprepo.list_deployments_for_display, deprepo.deployment_rows_for_display):
        cur = FakeCursor([])
        read(cur, 1)
        assert len(cur.sql) == 1 and "AS forward_sample" in cur.sql[0], read.__name__


def test_a_deploy_row_in_its_paper_forward_test_counts_toward_its_judgment(monkeypatch):
    deployments._ROADS.clear()
    now = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    trips = [{"pnl": 1.0, "opened_at": now - timedelta(days=4) + timedelta(hours=4 * i), "closed_at": now - timedelta(days=4) + timedelta(hours=4 * i + 2),
              "ticker": "KXHIGHNY-26OCT%02d-B%d" % (1 + i % 18, 50 + i)} for i in range(22)]
    monkeypatch.setattr(deployments.repo, "paper_round_trips_by_strategy", lambda cur, uid, sids: {8: trips})
    deps = [{"id": 5, "strategy_id": 8, "mode": "paper", "status": "running", "created_at": now - timedelta(days=4), "forward_sample": 40},
            {"id": 1, "strategy_id": 3, "mode": "paper", "status": "running", "created_at": now - timedelta(days=4), "forward_sample": None}]
    roads = deployments._deploy_roads(None, 1, deps)
    deployments._ROADS.clear()
    r = roads[8]
    assert (r["trades"], r["events"], r["forward_sample"], r["forward_events_needed"]) == (22, 18, 40, 20)
    # At the pace it trades (22 in 4 days): 18 more trades take about 3.3 days; 2 more events about half a day.
    assert r["forward_eta_days"] == round(18 / (22 / 96.0 * 24.0), 1)
    assert "forward_sample" not in roads[3] and "cleared_never" not in r


def test_furthest_on_paper_prefers_one_whose_record_counts_toward_live():
    prog = {8: (40, 29.5), 3: (9, 7.6), 7: (22, 48.9)}
    # 8 and 7 are in their paper forward test: 3 is the furthest toward live, and the only one whose ETA is for live.
    assert chat._furthest([3, 7, 8], prog, {}, {8: 40, 7: 40}) == (3, [3])
    # With none counting toward live, the furthest in its forward test; then one that can't be cleared.
    assert chat._furthest([7, 8], prog, {}, {8: 40, 7: 40}) == (8, [])
    assert chat._furthest([7, 8], prog, {8: "paper"}, {7: 40}) == (7, [])
    # One counting toward live with no trade yet doesn't hide one in its forward test that has some.
    assert chat._furthest([3, 8], {8: (40, 29.5)}, {}, {8: 40}) == (8, [3])


def test_the_story_says_a_forward_test_is_no_step_toward_live():
    prog = {"strategy_id": 8, "title": "NFL probe", "trades": 22, "trades_needed": 40, "net_pnl": 48.92, "strategies": 2,
            "forward_sample": 40, "forward_events_needed": 20, "events": 18, "running": True, "forward_eta_days": 4.6,
            "then_trades_needed": None, "then_eta_days": None}
    lines = _story(2, prog)
    # Deploy's words for the same figure (road_ahead.forward_words; forwardBody in core.js).
    assert ("2 strategies are on paper (pretend money). The furthest along is up $48.92 on its realistic paper trades %s, "
            "and is in its paper forward test: 22 of 40 trades · 18 of 20 separate events · about 5 days at its pace. One "
            "judgment on them decides whether it can be cleared to trade; if it passes, the live check then looks at the "
            "same record’s P&L, edge and demo orders." % customer_text.PAPER_NET_SHORT) in lines
    assert any("its 40 trades from 20+ separate events are in, so its one judgment on them is due" in line
               for line in _story(1, dict(prog, trades=41, events=24)))
    for line in lines:
        assert "live needs" not in line and "soonest" not in line


def test_the_feed_never_names_the_experiment_a_held_back_pass_repeats():
    """forward_gate.run's held-back line carries gatekeeper.marginal_contribution_ok's own reason, "max |corr| 0.81 to
    deployed experiment 17 exceeds 0.70": the feed showed it so. It says the page's words now; the stored line (which
    forward_gate.held_reason reads back for the page) is unchanged."""
    import inspect
    from app.research import gatekeeper
    from app.services import forward_gate
    src = inspect.getsource(gatekeeper.marginal_contribution_ok)
    assert '"max |corr| %.2f to deployed experiment %s exceeds %.2f" % (worst, worst_peer, CORR_MAX)' in src
    reason = "max |corr| %.2f to deployed experiment %s exceeds %.2f" % (0.81, 17, 0.70)
    line = "Paper forward gate: %s passed on paper (%s) but stays off the candidate list: %s." % (
        "NYC lookalike", "40 paper trades, p=0.020, +3.1% per trade", reason)
    said = customer_text.chat_rows([{"role": "system", "content": line}])[0]["content"]
    assert said == ("NYC lookalike passed its paper forward test (40 paper trades, averaging +3.1% of the stake per trade "
                    "after fees) but isn’t cleared to trade: it is too similar to a strategy you already run (a 0.81 "
                    "correlation of daily results; 0.70 or less needed).")
    assert "experiment" not in said and "corr|" not in said and "17" not in said
    assert forward_gate.held_reason(line, "NYC lookalike") == reason
    other = line.replace(reason, "no validation return series recorded for this experiment")
    said = customer_text.chat_rows([{"role": "system", "content": other}])[0]["content"]
    assert said.endswith("isn’t cleared to trade: the diversification check after it didn’t clear it; its page says why.")


def test_the_chats_facts_say_where_a_paper_strategy_stands_past_the_live_counts():
    """The context said "paper evidence: 22/30 realistic paper trades ... not live-ready: it needs 30+ closed paper trades
    (has 22)" for a probation strategy whose one paper judgment needs 40 and comes first, and only the demo check left for
    one held back for good. Each now carries its stage, as Deploy and its page say it: one in its paper forward test with
    Deploy's own figure (road_ahead.forward_figure) in Deploy's words, before its live counts."""
    import inspect
    from app.research import agent, gatekeeper
    from app.services import chat_facts, road_ahead
    fig = {"trades": 22, "events": 18, "running": True, "forward_sample": 40, "forward_events_needed": 20, "forward_eta_days": 3.3,
           "then_trades_needed": None, "then_eta_days": None}
    line = chat_facts.paper_stage_line("forward", fig)
    assert "paper forward test, NOT cleared to trade" in line
    assert ("first its paper forward test: 22 of 40 trades · 18 of 20 separate events · about 3 days at its pace. One judgment on "
            "them decides whether it can be cleared to trade; if it passes, the live check then looks at the same record’s P&L, "
            "edge and demo orders.") in line
    assert "judgment is due" not in line
    assert "so its one judgment on them is due" in chat_facts.paper_stage_line("forward", dict(fig, trades=40, events=24))
    assert ("passed its one judgment on its own paper trades but the diversification check held it back"
            in chat_facts.paper_stage_line("paper_held"))
    assert "can never be cleared to trade: its one judgment on its own paper trades didn’t pass" in chat_facts.paper_stage_line("paper")
    assert chat_facts.paper_stage_line("") == ""
    assert road_ahead.PAPER_MIN_EVENTS == agent.MIN_PAPER_EVENTS == gatekeeper.PAPER_MIN_EVENTS
    assert road_ahead.LIVE_MIN_TRADES == agent.MIN_REALISTIC_PAPER_TRADES
    src = inspect.getsource(agent._copilot_context)
    assert "chat_facts.paper_stage_line(" in src and "chat_facts.paper_stage(" in src and "road_ahead.forward_figure(" in src


def test_one_figure_for_a_forward_test_on_deploy_its_page_the_panel_and_the_chat(monkeypatch):
    """Regression walk 2026-11-01: one strategy in its paper forward test read "8 of 40" on its ladder, "8 of 30 ... about 25
    days" in its Road to live and "about 36 days" on Deploy. Deploy's road, the strategy page's live-readiness `forward`, the
    autopilot panel's paper_progress and the chat's facts are one figure (road_ahead.forward_figure) said one way."""
    from app.services import chat_facts, road_ahead
    now = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    deps = [{"id": 5, "strategy_id": 8, "mode": "paper", "status": "running", "created_at": now - timedelta(days=9), "forward_sample": 40,
             "cleared_never": None, "result": {}}]
    trips = [{"pnl": 1.0, "opened_at": now - timedelta(days=8) + timedelta(hours=13 * i), "closed_at": now - timedelta(days=8) + timedelta(hours=13 * i + 3),
              "ticker": "KXHIGHNY-26OCT%02d-B60.5" % (1 + i)} for i in range(8)]
    deployments._ROADS.clear()
    monkeypatch.setattr(deployments.repo, "paper_round_trips_by_strategy", lambda cur, uid, sids: {8: trips})
    road = deployments._deploy_roads(None, 1, deps)[8]
    deployments._ROADS.clear()
    # 8 trips in 9 days: 32 more take 36 days, against the live check's own 22 more (about 25 days), never said as next.
    assert (road["trades"], road["forward_sample"], road["forward_eta_days"], road["eta_days"]) == (8, 40, 36.0, 24.8)
    paper = {"trades": 8, "events": 8, "span_hours": road["span_hours"], "ready": False, "why": "it needs 30+"}
    monkeypatch.setattr(deployments.repo, "get_strategy", lambda cur, uid, sid: {"id": 8, "status": "passed"})
    monkeypatch.setattr(deployments.repo, "list_deployments_brief", lambda cur, uid: deps)
    monkeypatch.setattr(deployments.agent, "_is_deployment_candidate", lambda cur, sid: False)
    monkeypatch.setattr(deployments.agent, "paper_evidence", lambda cur, uid, sid: paper)
    monkeypatch.setattr(deployments.agent, "live_autopilot_ok", lambda cur, user: (False, "off"))

    class _Ctx:
        cur, uid, user, params, query = None, 1, {"id": 1}, ("8",), {}
    page = deployments.strategy_live_readiness(_Ctx())["forward"]
    keys = ("trades", "events", "forward_sample", "forward_events_needed", "forward_eta_days", "then_trades_needed", "then_eta_days", "running")
    assert {k: page[k] for k in keys} == {k: road[k] for k in keys}
    panel = road_ahead.forward_figure(deps, 8, 8, 8, 40, road_ahead.live_eta(deps, 8, 8, 8))
    assert {k: panel[k] for k in keys} == {k: road[k] for k in keys}
    said = road_ahead.forward_words(road)
    assert said.startswith("8 of 40 trades · 8 of 20 separate events · about 36 days at its pace.")
    assert said in chat_facts.paper_stage_line("forward", page)
    assert said in "".join(_story(1, dict(page, strategy_id=8, title="Fwd", trades_needed=40, net_pnl=12.17, strategies=1)))
    # A min_sample under the live check's 30: then its 30 trades, at the live check's own estimate, in that order.
    low = road_ahead.forward_figure(deps, 8, 8, 8, 25, 24.8)
    assert (low["forward_eta_days"], low["then_trades_needed"], low["then_eta_days"]) == (19.1, 30, 24.8)
    assert road_ahead.forward_words(low).endswith("if it passes, the live check then needs 30 trades (about 25 days at its pace) and "
                                                  "looks at their P&L, edge and demo orders.")


def test_the_chats_live_check_reason_is_the_count_the_pages_give():
    """Walk 2026-11-01: the chat's facts said "paper evidence: 3/30 realistic paper trades ... not live-ready: it needs 8+
    closed paper trades first (has 3)", the live gate's first (legacy) check, beside a forward test of "3 of 40 trades";
    no page says it, since the 30 realistic trades after it are always more. The reason is now the realistic count's own
    (or the check between them), and one in its paper forward test says the live check comes after that judgment."""
    import inspect
    from app.research import agent
    from app.services import chat_facts
    ev = {"ready": False, "why": "it needs 8+ closed paper trades first (has 3)", "trades": 3, "trades_needed": 30, "legacy_pnl": 2.1,
          "checks": {"legacy_trades": False, "legacy_pnl": True, "realistic_trades": False, "events": False}}
    assert chat_facts.live_check_why(ev) == "it needs 30+ closed paper trades on realistic fills first (has 3)"
    # The gate's own words for its next checks, so the reason reads as the gate gives it.
    src = inspect.getsource(agent.paper_evidence)
    assert '"it needs %d+ closed paper trades on realistic fills first (has %d)"' in src
    assert '"it\'s still down $%.2f on paper"' in src
    down = dict(ev, legacy_pnl=-1.5, checks=dict(ev["checks"], legacy_pnl=False))
    assert chat_facts.live_check_why(down) == "it's still down $1.50 on paper"
    fwd = {"trades": 3, "events": 3, "running": True, "forward_sample": 40, "forward_events_needed": 20, "forward_eta_days": 12.0,
           "then_trades_needed": None, "then_eta_days": None}
    said = chat_facts.live_check_why(ev, fwd)
    assert said.startswith("the live check counts this record only after its paper forward test passes") and "8+" not in said
    # Past the legacy count, the gate's own reason stands; a ready record has none.
    later = dict(ev, why="its paper edge isn't statistically clear yet (p=0.21, needs 0.10 or lower)",
                 checks=dict(ev["checks"], legacy_trades=True, realistic_trades=True))
    assert chat_facts.live_check_why(later) == later["why"]
    assert chat_facts.live_check_why(dict(ev, ready=True)) == ""
    line = agent.paper_evidence_line(dict(ev, net_pnl=1.0, span_hours=12.0, why=chat_facts.live_check_why(ev)))
    assert "8+" not in line and line.endswith("not live-ready: it needs 30+ closed paper trades on realistic fills first (has 3)")
    assert "chat_facts.live_check_why(" in inspect.getsource(agent._copilot_context)
