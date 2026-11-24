"""Market-making research loop: research-only feedback, one finalist, the held-out gate and its ledger."""
import json
from datetime import datetime, timedelta, timezone

from app.adapters.llm.llm import Usage
from app.services import maker_lab as lab

H0 = datetime(2026, 10, 25, 0, tzinfo=timezone.utc)


def _rows(hours, per_hour, fills=40, fee=0.0):
    return [{"hour": int(h.timestamp()), "fills": fills, "qty": 1000.0, "reward": 0.2, "m10": 0.0,
             "m60": per_hour(i), "fees": 0.0, "fee_stress": fee} for i, h in enumerate(hours)]


def test_split_holds_out_the_newest_hours():
    research, holdout = lab.split(H0 + timedelta(hours=10, minutes=20), 6, 4)
    assert research[0] == H0 and research[-1] == H0 + timedelta(hours=5)
    assert holdout == [H0 + timedelta(hours=k) for k in range(6, 10)]


def test_gate_needs_power_significance_and_survives_the_fee_stress():
    hours = [H0 + timedelta(hours=k) for k in range(20)]
    steady = _rows(hours, lambda i: 5.0 + (i % 3))
    v = lab.judge(steady, 0.05)
    assert v["passed"] and v["p_value"] <= 0.05 and v["mean"] > 0
    assert not lab.judge(steady[:8], 0.05)["passed"]                          # 8 hours: underpowered
    assert "underpowered" in lab.judge(steady[:8], 0.05)["reason"]
    noisy = _rows(hours, lambda i: 40.0 if i % 2 else -38.0)
    assert "not significant" in lab.judge(noisy, 0.05)["reason"]
    fee = _rows(hours, lambda i: 5.0 + (i % 3), fee=9.0)
    assert "fee" in lab.judge(fee, 0.05)["reason"]


def test_lab_shows_research_only_judges_one_finalist_and_splits_alpha_on_reruns():
    research = [H0 + timedelta(hours=k) for k in range(24)]
    holdout = [H0 + timedelta(hours=k) for k in range(24, 40)]
    seen_hours = []

    def evaluate(policy, hours, workers=1):
        seen_hours.append(list(hours))
        good = policy.get("min_edge", 0) >= 0.02
        return _rows(hours, (lambda i: 6.0 + (i % 4)) if good else (lambda i: -20.0 + (i % 3)))

    prompts = []

    def model(system, messages, on_delta=None):
        prompts.append(messages[-1]["content"])
        pols = [{"name": "wide", "why": "x", "fair_source": "spot", "min_edge": 0.03, "back_off": True},
                {"name": "bad", "why": "y", "quote_size": 50}]
        return json.dumps({"policies": pols}), {"input_tokens": 10, "output_tokens": 5}

    ledger = lab.MemoryLedger()
    out = lab.run_lab("KXBTCD", research, holdout, model, Usage(), ledger, evaluate, rounds=1, per_round=2,
                      log=lambda m: None)
    assert out["finalist"] in ("spot_fair_2c", "spot_fair_2c_pull", "r1.1 wide")
    assert out["verdict"]["passed"] and out["verdict"]["alpha"] == 0.05
    assert all(h < holdout[0] for hs in seen_hours[:-1] for h in hs)          # research rounds never see holdout
    assert seen_hours[-1] == holdout and "join_touch" in prompts[0]
    assert len(ledger.entries) == 1
    again = lab.run_lab("KXBTCD", research, holdout, model, Usage(), ledger, evaluate, rounds=1, per_round=2,
                        log=lambda m: None)
    assert again["verdict"]["alpha"] == 0.025                                  # second look at the same window


def test_no_finalist_when_nothing_makes_money_in_research():
    research = [H0 + timedelta(hours=k) for k in range(12)]
    holdout = [H0 + timedelta(hours=k) for k in range(12, 16)]

    def model(system, messages, on_delta=None):
        return json.dumps({"policies": [{"name": "a", "quote_size": 10}]}), {}

    out = lab.run_lab("KXBTC15M", research, holdout, model, Usage(), lab.MemoryLedger(),
                      lambda p, h, workers=1: _rows(h, lambda i: -3.0), rounds=1, per_round=1, log=lambda m: None)
    assert out["finalist"] is None and out["verdict"] is None


def test_each_run_sees_the_summary_of_earlier_runs():
    research = [H0 + timedelta(hours=k) for k in range(12)]
    holdout = [H0 + timedelta(hours=k) for k in range(12, 24)]
    prompts = []

    def model(system, messages, on_delta=None):
        prompts.append(messages[-1]["content"])
        return json.dumps({"policies": [{"name": "a", "fair_source": "spot", "min_edge": 0.04}]}), {}

    first = lab.run_lab("KXBTCD", research, holdout, model, Usage(), lab.MemoryLedger(),
                        lambda p, h, workers=1: _rows(h, lambda i: 4.0 + i % 2), rounds=1, per_round=1,
                        log=lambda m: None)
    lines = lab.summary_lines(json.loads(json.dumps(first)))
    assert lines[0].startswith("- run held out from 2026-10-25 12:00Z") and "finalist" in lines[-1]
    lab.run_lab("KXBTCD", research, holdout, model, Usage(), lab.MemoryLedger(),
                lambda p, h, workers=1: _rows(h, lambda i: 4.0), rounds=1, per_round=1, log=lambda m: None,
                prior=lines)
    assert "Earlier lab runs" not in prompts[0] and "Earlier lab runs" in prompts[1] and lines[-1] in prompts[1]


def test_the_timer_rotates_series_so_each_holdout_window_is_used_once():
    from app.maker_lab_run import auto_series
    picks = [auto_series(H0 + timedelta(hours=h)) for h in range(0, 24, 4)]
    assert picks == ["housekeeping", "KXBTC15M", "KXBTC"] * 2       # KXBTCD searches timed out from 11-12


def test_a_worker_killed_out_of_memory_finishes_the_hours_in_process(monkeypatch):
    from concurrent.futures.process import BrokenProcessPool
    from app.adapters.sim import maker_eval

    class Dies:
        def __init__(self, max_workers):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def map(self, fn, jobs):
            raise BrokenProcessPool("a child process terminated abruptly")

    monkeypatch.setattr(maker_eval, "ProcessPoolExecutor", Dies)
    monkeypatch.setattr(maker_eval, "_eval_args", lambda j: {"hour": j[1]})
    hours = [H0 + timedelta(hours=k) for k in range(3)]
    monkeypatch.setattr(maker_eval, "FALLBACKS", [])
    rows = maker_eval.evaluate({"series": "KXBTCD"}, hours, workers=2)
    assert [r["hour"] for r in rows] == [int(h.timestamp()) for h in hours]
    assert maker_eval.FALLBACKS == [3]                                  # the run's status file reports it


def test_the_fast_series_search_wider_inside_the_slot():
    from app.maker_lab_run import search_size
    assert search_size("KXBTCD") == (3, 4)                        # ~2h20m already; stays
    assert search_size("KXBTC") == (5, 6) and search_size("KXBTC15M") == (4, 5)
    assert search_size("KXBTC", rounds=1, per_round=2) == (1, 2)   # the CLI still overrides


def _run(series, name, p, stressed, hold_end, h="abc"):
    return {"series": series, "finalist": name, "holdout_hours": [hold_end - 3600 * 11, hold_end],
            "results": {name: {"hash": h, "policy": {"series": series, "min_edge": 0.02}}},
            "verdict": {"p_value": p, "fee_stressed_mean": stressed}}


def test_near_miss_finalists_are_judged_once_on_the_24_hours_after_their_holdout():
    """Todd 2026-10-31: one holdout pass in ~10 judged finalists is about chance, so qualifying
    finalists get ONE frozen judgment on the hours after their holdout, in their own ledger."""
    end = int((H0 + timedelta(hours=11)).timestamp())
    runs = [_run("KXBTC15M", "r4.3", 0.0165, 24.7, end, "p1"),
            _run("KXBTC15M", "r4.2", 0.195, 14.8, end + 12 * 3600, "p2"),   # window not archived yet
            _run("KXBTC", "r3.3", 0.56, -4.8, end, "p3"),                  # not a near miss
            _run("KXBTC", "r2.2", 0.10, -1.0, end, "p4"),                  # loses under the fee stress
            {"series": "KXBTCD", "finalist": None, "verdict": None}]
    book = lab.ForwardBook()
    archived_to = H0 + timedelta(hours=12 + 24)                            # 24 hours after r4.3's holdout
    due = lab.forward_due(runs, book, archived_to, limit=5)
    assert [c["name"] for c in due] == ["r4.3"]
    c = due[0]
    assert c["start"] == end + 3600 and c["end"] - c["start"] == 24 * 3600 and c["policy"]["min_edge"] == 0.02

    seen = []

    def evaluate(policy, hours, workers=1):
        seen.append(list(hours))
        return _rows(hours, lambda i: 6.0 + (i % 4))

    e = lab.judge_forward(c, book, evaluate, log=lambda m: None)
    assert e["passed"] and e["holdout_p"] == 0.0165 and e["hours"] == 24
    assert seen[0][0].timestamp() == end + 3600 and len(seen[0]) == 24
    assert lab.forward_due(runs, book, archived_to + timedelta(hours=48), limit=5) == [
        lab.forward_candidate(runs[1])]                                     # r4.3 never again; r4.2 now due
    ledger = lab.MemoryLedger()
    assert ledger.overlapping("KXBTC15M", c["start"], c["end"]) == 0        # holdout alpha untouched


def test_a_clone_of_a_policy_already_judged_forward_is_not_judged_again():
    end = int((H0 + timedelta(hours=11)).timestamp())
    runs = [_run("KXBTC", "a", 0.05, 3.0, end, "same"), _run("KXBTC", "b", 0.08, 2.0, end + 3600, "same")]
    book = lab.ForwardBook()
    assert [c["name"] for c in lab.forward_due(runs, book, H0 + timedelta(days=5), limit=5)] == ["a"]
    book.record({"series": "KXBTC", "hash": "same"})
    assert lab.forward_due(runs, book, H0 + timedelta(days=5), limit=5) == []


def test_a_forward_pass_is_confirmed_once_on_72_hours_and_needs_trading_pnl_too():
    """Fixed 2026-11-16 after the first forward passes: markouts must pass again on the 72 hours after
    the forward window, at alpha split by forward passes, and the held inventory must not lose."""
    end = int((H0 + timedelta(hours=11)).timestamp())
    runs = [_run("KXBTC", "r5.1", 0.0, 40.0, end, "p1"), _run("KXBTC", "r5.3", 0.0, 30.0, end, "p2")]
    fwd = lab.ForwardBook([
        {"series": "KXBTC", "name": "r5.1", "hash": "p1", "passed": True, "p_value": 0.0,
         "start": end + 3600, "end": end + 25 * 3600},
        {"series": "KXBTC", "name": "r5.3", "hash": "p2", "passed": True, "p_value": 0.0,
         "start": end + 13 * 3600, "end": end + 37 * 3600},
        {"series": "KXBTC", "name": "old", "hash": "p9", "passed": False, "start": 0, "end": end}])
    book = lab.ConfirmBook()
    first_due = datetime.fromtimestamp(end + 97 * 3600, timezone.utc)
    due = lab.confirm_due(runs, fwd, book, first_due, limit=5)
    assert [c["name"] for c in due] == ["r5.1"]                              # r5.3's window not archived yet
    c = due[0]
    assert c["start"] == end + 25 * 3600 and c["end"] - c["start"] == 72 * 3600 and c["policy"]["min_edge"] == 0.02

    seen = []

    def evaluate(policy, hours, workers=1):
        seen.append(list(hours))
        rows = _rows(hours, lambda i: 6.0 + (i % 4))
        for r in rows:
            r["trading_pnl"] = -5.0                                          # markouts win, inventory loses
        return rows

    e = lab.judge_confirm(c, fwd, book, evaluate, log=lambda m: None)
    assert not e["passed"] and "held inventory lost" in e["reason"] and e["trading_pnl"] == -360.0
    assert e["alpha"] == 0.025 and e["hours"] == 72 and len(seen[0]) == 72
    later = first_due + timedelta(hours=24)
    assert [c["name"] for c in lab.confirm_due(runs, fwd, book, later, limit=5)] == ["r5.3"]   # r5.1 never again

    def good(policy, hours, workers=1):
        rows = _rows(hours, lambda i: 6.0 + (i % 4))
        for r in rows:
            r["trading_pnl"] = 3.0
        return rows

    e2 = lab.judge_confirm(lab.confirm_due(runs, fwd, book, later, limit=5)[0], fwd, book, good, log=lambda m: None)
    assert e2["passed"] and e2["trading_pnl"] == 216.0 and e2["losing_hours"] == 0


def test_a_confirmed_policy_is_tracked_on_settled_pnl_at_tradable_size():
    """Added 2026-11-22: r5.1's 78 hours after its confirm made +$819 markouts but -$128 settled at
    paper size, so confirmed policies are also scored each hour after confirm at a size we could trade."""
    end = int((H0 + timedelta(hours=11)).timestamp())
    runs = [_run("KXBTC", "r5.1", 0.0, 40.0, end, "p1"), _run("KXBTC", "r5.3", 0.0, 30.0, end, "p2")]
    conf = lab.ConfirmBook([{"series": "KXBTC", "name": "r5.1", "hash": "p1", "passed": True, "start": end, "end": end},
                            {"series": "KXBTC", "name": "r5.3", "hash": "p2", "passed": False, "start": end, "end": end}])
    book = lab.TrackBook()
    sizes = []

    def evaluate(policy, hours, workers=1):
        sizes.append((policy["quote_size"], policy["max_inventory"]))
        rows = _rows(hours, lambda i: 1.0)
        for i, r in enumerate(rows):
            r["trading_pnl"], r["peak_collateral"] = (2.0 if i % 2 else -1.0), 150.0
        return rows

    at = datetime.fromtimestamp(end + 5 * 3600, timezone.utc)
    out = lab.track(runs, conf, book, evaluate, at, limit=3, log=lambda m: None)
    assert [o["added"] for o in out] == [3] and sizes == [(10.0, 10.0)]       # r5.3 failed confirm: not tracked
    out = lab.track(runs, conf, book, evaluate, at, limit=3, log=lambda m: None)
    s = book.doc["tracks"]["KXBTC|p1|10"]["summary"]
    assert [o["added"] for o in out] == [2] and s["hours"] == 5 and s["losing_hours"] == 3
    assert s["trading_pnl"] == 1.0 and s["peak_collateral"] == 150.0 and s["settled_c_per_contract"] == 0.02
    assert lab.track(runs, conf, book, evaluate, at, limit=3, log=lambda m: None) == []   # nothing new archived
