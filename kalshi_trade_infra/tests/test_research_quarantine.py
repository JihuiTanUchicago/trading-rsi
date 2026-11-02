"""Sports research memory learned on the leaky close-time backtests (PR #839) is hidden, not deleted."""
import json
from datetime import datetime, timedelta, timezone

from app import repository as repo
from app import sports_rescore_run as rescore
from app.research import quarantine, rsi_playbook, rsi_sleep
from app.trading import backtest

CUT = quarantine.since("sports")
BEFORE, AFTER = CUT - timedelta(hours=1), CUT + timedelta(hours=1)


def test_hides_only_quarantined_families_before_the_cut():
    assert quarantine.hides("sports", BEFORE) and not quarantine.hides("sports", AFTER)
    assert not quarantine.hides("weather-high", BEFORE) and quarantine.since("crypto") is None
    assert quarantine.hides("sports", BEFORE.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=-5))))
    assert quarantine.hides("sports", BEFORE.isoformat() + "Z") and not quarantine.hides("sports", None)
    assert not quarantine.hides("sports", "not a date")


class _Cur:
    def __init__(self, rows):
        self.rows, self.sql = list(rows), []

    def execute(self, sql, args=()):
        self.sql.append((sql, args))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


def _knowledge(version, created):
    return {"id": version, "user_id": 1, "family": "sports", "version": version, "through_run_id": 2600,
            "runs": 5, "ideas": 40, "ledger_json": "{}", "lessons_json": json.dumps([{"text": "late-game fades win"}]),
            "changes": "", "created_at": created}


def test_sleep_memory_from_before_the_cut_reads_as_no_memory():
    assert rsi_sleep.latest(_Cur([_knowledge(7, BEFORE)]), 1, "sports") is None
    assert rsi_sleep.latest(_Cur([_knowledge(7, BEFORE)]), 1, "sports", include_quarantined=True)["version"] == 7
    assert rsi_sleep.latest(_Cur([_knowledge(8, AFTER)]), 1, "sports")["lessons"][0]["text"] == "late-game fades win"


def test_sleep_relearns_from_clean_runs_and_numbers_past_the_hidden_version(monkeypatch):
    rows = [_knowledge(7, BEFORE)]
    saved = []
    events = [{"run_id": 2600 + i, "market": "nba_game", "title": "idea %d" % i, "outcome": "loses",
               "message": "", "data": {"status": "abandoned"}, "day": "2026-11-0%d" % (1 + (i >= 5)),
               "created_at": BEFORE if i < 5 else AFTER} for i in range(10)]
    monkeypatch.setattr(rsi_sleep, "_save", lambda cur, u, f, version, through, ledger, lessons, changes:
                        saved.append((version, ledger.get("ideas"), through)))
    monkeypatch.setattr(repo, "research_verdicts_after", lambda *a, **k: events)
    monkeypatch.setattr(rsi_sleep, "consolidate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    rsi_sleep.sleep(_Cur(rows), None, None, 1, "sports", ["nba_game"], 2609)
    assert saved and saved[0][:2] == (8, 5)              # the 5 leaky runs' ideas are not folded in


def test_a_playbook_written_before_the_cut_reads_as_empty_text():
    row = {"id": 3, "user_id": 1, "family": "sports", "version": 4, "text": "fade favourites in the 4th quarter",
           "created_at": BEFORE}
    assert rsi_playbook._row(_Cur([row]), "id=%s", (3,))["text"] == ""
    assert rsi_playbook._row(_Cur([dict(row, created_at=AFTER)]), "id=%s", (3,))["text"].startswith("fade")
    assert rsi_playbook._row(_Cur([dict(row, family="weather-high")]), "id=%s", (3,))["text"].startswith("fade")


def test_drop_lists_can_skip_runs_from_before_the_cut():
    cur = _Cur([])
    repo.dropped_theses(cur, 1, "nba_game", since=CUT)
    assert "created_at >= %s" in cur.sql[0][0] and cur.sql[0][1] == (1, "nba_game", CUT, repo.DROPPED_RECENT_RUNS)
    cur = _Cur([])
    repo.sibling_dropped_theses(cur, 1, ["nba_game", "nfl_game"], since=CUT)
    sql, args = cur.sql[0]
    assert "market IN (%s,%s) AND created_at >= %s ORDER BY" in sql
    assert args == (1, "nba_game", "nfl_game", CUT, repo.SIBLING_RECENT_RUNS)
    cur = _Cur([])
    repo.dropped_theses(cur, 1, "nba_game")
    assert "created_at" not in cur.sql[0][0]


def test_rescore_summary_and_leaky_vs_fixed_replay(monkeypatch):
    T = backtest.Trade
    s = rescore.summarize([T("A", "yes", 10, 0, 0.4, 1, 1.0, 0.1, "settle", 0.0),
                           T("B", "yes", 10, 0, 0.4, 1, 0.0, 0.1, "settle", 0.0)])
    assert s["n"] == 2 and s["net_pnl"] == round(5.9 - 4.1, 2) and s["win_rate"] == 0.5
    assert rescore.summarize([]) == {"n": 0}

    seen = []

    def dataset(market, part, history_days=None):
        seen.append(backtest.outcome_event_closes([{"ticker": "E-1", "close_time": datetime(2026, 1, 1)}]))
        return backtest.Dataset(market=None, frames=[], results={}, start_ts=0, end_ts=1, contracts=0)

    monkeypatch.setattr(rescore.partitions, "research_dataset", dataset)
    monkeypatch.setattr(rescore.partitions, "validation_dataset", dataset)
    monkeypatch.setattr(backtest, "research_fold_trades", lambda spec, ds, emb: [])
    monkeypatch.setattr(backtest, "simulate", lambda spec, ds, a, b: [])
    monkeypatch.setattr(rescore.rr, "results_for_experiment",
                        lambda cur, eid: [{"phase": "validation", "classification": "stat_supported", "n_trades": 40}]
                        if eid == 1 else [])
    monkeypatch.setattr(rescore.rr, "partition_by_id", lambda cur, pid: {"embargo_seconds": 0})
    cur = _Cur([{"id": 1, "user_id": 2, "run_id": 2622, "market": "nba_game", "spec_json": '{"name": "x"}',
                 "research_partition_id": 9},
                {"id": 2, "user_id": 2, "run_id": 2622, "market": "nba_game", "spec_json": "{}",
                 "research_partition_id": 9}])
    rep = rescore.run(cur, [2622, 2646], log=lambda *_: None)
    assert [e["experiment_id"] for e in rep["experiments"]] == [1]
    e = rep["experiments"][0]
    assert e["stored_validation"]["classification"] == "stat_supported" and e["leaky"]["validation"] == {"n": 0}
    assert seen[:2] == [{}, {}] and seen[2] == {"E": datetime(2026, 1, 1, tzinfo=timezone.utc)}   # leaky replay, then fixed
    assert backtest.outcome_event_closes.__name__ == "outcome_event_closes"      # restored after the leaky run
