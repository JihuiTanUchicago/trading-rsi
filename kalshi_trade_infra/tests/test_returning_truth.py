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
            "forward_sample": 40}
    lines = _story(2, prog)
    assert ("2 strategies are on paper (pretend money). The furthest along is in its paper forward test: it has 22 of the 40 "
            "realistic paper trades its one judgment needs and is up $48.92 on them %s. Only a pass there can clear it to "
            "trade, and that judgment is made once." % customer_text.PAPER_NET_SHORT) in lines
    assert any("its one judgment needs 40, from 20 separate events" in line for line in _story(1, dict(prog, trades=41)))
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
    one held back for good. Each now carries its stage, as Deploy and its page say it."""
    import inspect
    from app.research import agent
    from app.services import chat_facts
    line = chat_facts.paper_stage_line("forward", 40, 22, 18)
    assert "paper forward test, NOT cleared to trade" in line and "comes at 40 realistic trades from 20 separate events" in line
    assert "(has 22 trades from 18 events)" in line and "judgment is due" not in line
    assert "(has 40 trades from 24 events, so that judgment is due)" in chat_facts.paper_stage_line("forward", 40, 40, 24)
    assert ("passed its one judgment on its own paper trades but the diversification check held it back"
            in chat_facts.paper_stage_line("paper_held", 40, 44, 26))
    assert "can never be cleared to trade: its one judgment on its own paper trades didn’t pass" in chat_facts.paper_stage_line("paper", 40, 46, 25)
    assert chat_facts.paper_stage_line("", 40, 9, 6) == ""
    assert chat_facts.PAPER_MIN_EVENTS == agent.MIN_PAPER_EVENTS
    src = inspect.getsource(agent._copilot_context)
    assert "chat_facts.paper_stage_line(" in src and "chat_facts.paper_stage(" in src
