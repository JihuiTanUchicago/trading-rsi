"""In-play mention YES seller: live trigger, fills from YES-taker prints, never-cancel stress, verdict."""
from app.services import mention_maker as mm


def nofee(_lots, _p):
    return 0.0


def test_trigger_is_a_jump_to_90c_from_below_70c_not_a_word_that_always_sat_high():
    prints = {"A": [(100, 0.95, 10, True), (200, 0.96, 10, True)],          # high all along: no trigger
              "B": [(150, 0.40, 5, True), (300, 0.92, 5, True)]}            # said at 300
    assert mm.event_trigger(prints) == 300
    assert mm.event_trigger({"A": [(1, 0.5, 1, True)]}) is None


def test_fills_first_qty_yes_taker_contracts_in_band_inside_the_window_only():
    t0 = 1000
    prints = [(t0 + 2, 0.40, 50, True),         # inside the 5s delay: skipped
              (t0 + 10, 0.40, 30, False),       # NO taker: hits bids, not our ask
              (t0 + 20, 0.80, 50, True),        # out of band
              (t0 + 30, 0.40, 60, True),
              (t0 + 40, 0.30, 70, True),        # only 40 left of the 100
              (t0 + mm.HOLD_S + 1, 0.30, 99, True)]
    r = mm.market_result(prints, 0.0, t0, nofee)
    assert r["filled"] == 100
    assert abs(r["pnl"] - (60 * 0.40 + 40 * 0.30)) < 1e-9


def test_said_word_loses_and_the_slow_stress_fills_the_rest_at_the_last_band_price():
    t0 = 1000
    prints = [(t0 + 30, 0.40, 20, True), (t0 + 60, 0.45, 5, False), (t0 + 90, 0.97, 50, True)]
    r = mm.market_result(prints, 1.0, t0, nofee)
    assert r["filled"] == 20 and abs(r["pnl"] - 20 * (0.40 - 1.0)) < 1e-9
    assert abs(r["slow"] - (r["pnl"] + 80 * (0.45 - 1.0))) < 1e-9


def test_maker_fee_is_charged_per_fill():
    r = mm.market_result([(1010, 0.50, 10, True)], 0.0, 1000, lambda lots, p: 0.01)
    assert abs(r["pnl"] - (10 * 0.50 - 0.01)) < 1e-9


def test_event_without_a_trigger_is_skipped_and_totals_add_up():
    assert mm.event_result({"A": [(1, 0.3, 5, True)]}, {"A": 0.0}, nofee) is None
    prints = {"A": [(100, 0.5, 5, True), (200, 0.95, 5, True)],
              "B": [(210, 0.30, 10, True)]}
    r = mm.event_result(prints, {"A": 1.0, "B": 0.0}, nofee)
    assert r["trigger"] == 200 and r["filled"] == 10 and abs(r["pnl"] - 3.0) < 1e-9


def test_verdict_waits_for_the_preregistered_sample_then_judges_once():
    good = [dict(filled=100, pnl=6.0 + (i % 5), slow=4.0) for i in range(30)]
    s = mm.summarize(good, judge_at=50)
    assert s["verdict"] == "accumulating" and s["c"] > 0
    assert mm.summarize(good, judge_at=30)["verdict"] == "pass"
    bad = [dict(filled=100, pnl=(-1) ** i * 20.0, slow=0.0) for i in range(40)]
    assert mm.summarize(bad, judge_at=30)["verdict"] == "fail"
    assert mm.summarize([], judge_at=30)["verdict"] == "accumulating"


def test_runner_falls_back_to_historical_prints_and_parses_dollar_fields(monkeypatch):
    from app import mention_maker_run as run
    calls = []

    def fake(path, params=None):
        calls.append(path)
        if path == "/markets/trades":
            return {"trades": [], "cursor": ""}
        return {"trades": [{"created_time": "2026-11-12T01:00:00Z", "yes_price_dollars": "0.4000",
                            "count_fp": "12.00", "taker_side": "yes"}], "cursor": ""}
    monkeypatch.setattr(run, "_get", fake)
    rows = run.prints_of("KXTRUMPMENTION-26NOV12-TARIFF")
    assert calls == ["/markets/trades", "/historical/trades"]
    assert rows == [(1794445200.0, 0.40, 12.0, True)]
