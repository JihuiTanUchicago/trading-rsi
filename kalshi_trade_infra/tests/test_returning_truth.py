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
from datetime import datetime, timedelta

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
    # A pass the diversification check held back stays off the candidate list for good.
    assert _clearance([(40, "holdout", "forward_validated")]) == "holdout"
    assert _clearance([val, (40, "paper", "forward_validated")]) == "paper"
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
    assert ("1 strategy is on paper (pretend money), but it can’t be cleared to trade: its one-time judgment didn’t clear "
            "it. It has 4 realistic trades on paper, up $8.63 on them %s." % customer_text.PAPER_NET_SHORT) in lines
    many = _story(4, prog)
    assert any(line.startswith("4 strategies are on paper (pretend money), but none can be cleared to trade: each one’s "
                               "one-time judgment didn’t clear it. The furthest along has 4 realistic trades on paper") for line in many)
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
