"""Paper implication-lag catcher: clustering, the LLM reply check, the trigger, paper fills, scoring."""
import pytest

from app import implication_watch_run as wr
from app.services import implication as imp


def _ev(et, st, title, markets, stitle=""):
    return {"event_ticker": et, "series_ticker": st, "title": title, "series_title": stitle, "markets": markets}


def _m(t, vol=50000, rules="r"):
    return {"ticker": t, "volume_fp": str(vol), "rules_primary": rules, "yes_sub_title": t}


# -- universe and clusters ----------------------------------------------------------------------------

def test_news_series_skips_sports_crypto_weather_mentions_and_parlays():
    assert imp.news_series("KXNVDACEO", "Financials")
    assert not imp.news_series("KXNBAGAME", "Sports")
    assert not imp.news_series("KXBTCD", "Crypto")
    assert not imp.news_series("KXHIGHNY", "Climate and Weather")
    assert not imp.news_series("KXTRUMPMENTION", "Politics")
    assert not imp.news_series("KXMVECROSS", "Politics")


def test_clusters_link_events_of_different_series_by_a_rare_word():
    evs = [_ev("KXNVDACEO-29JAN", "KXNVDACEO", "Who will be the next CEO of NVIDIA?", [_m("A1"), _m("A2")]),
           _ev("KXNVDACEODATE-27", "KXNVDACEODATE", "When will NVIDIA announce its next CEO?", [_m("B1")]),
           _ev("KXPOPE-26", "KXPOPE", "Who will be the next Pope?", [_m("C1")])]
    cl = imp.clusters(evs)
    assert len(cl) == 1
    assert {m["ticker"] for m in cl[0]} == {"A1", "A2", "B1"}
    assert {m["_event"] for m in cl[0]} == {"KXNVDACEO-29JAN", "KXNVDACEODATE-27"}


def test_clusters_need_two_series_and_drop_thin_markets():
    evs = [_ev("S-1", "S", "Nvidia board vote", [_m("A")]), _ev("S-2", "S", "Nvidia board seat", [_m("B")])]
    assert imp.clusters(evs) == []                       # one series only
    evs = [_ev("S-1", "S", "Nvidia board vote", [_m("A")]),
           _ev("T-1", "T", "Nvidia board seat", [_m("B", vol=10)])]
    assert imp.clusters(evs) == []                       # B too thin, leaves one event


def test_cluster_key_changes_with_rules_not_order():
    a, b = _m("A", rules="x"), _m("B", rules="y")
    assert imp.cluster_key([a, b]) == imp.cluster_key([b, a])
    assert imp.cluster_key([a, b]) != imp.cluster_key([a, _m("B", rules="z")])


# -- LLM reply check ----------------------------------------------------------------------------------

def _cluster():
    return [dict(_m("A"), _event="E1"), dict(_m("A2"), _event="E1"), dict(_m("B"), _event="E2")]


def test_check_edges_keeps_valid_cross_event_links_only():
    reply = {"edges": [
        {"if": "A", "if_result": "yes", "then": "B", "then_result": "yes", "confidence": 0.97, "why": "rule"},
        {"if": "A", "if_result": "yes", "then": "A2", "then_result": "no", "confidence": 0.99},   # same event
        {"if": "A", "if_result": "yes", "then": "ZZZ", "then_result": "yes", "confidence": 0.99},  # unknown
        {"if": "B", "if_result": "maybe", "then": "A", "then_result": "yes", "confidence": 0.99},  # bad result
        {"if": "B", "if_result": "no", "then": "A", "then_result": "no", "confidence": 0.5},       # unsure
        {"if": "A", "if_result": "yes", "then": "B", "then_result": "yes", "confidence": 0.95},    # duplicate
    ]}
    out = imp.check_edges(reply, _cluster())
    assert [(e["if"], e["then"], e["then_result"]) for e in out] == [("A", "B", "yes")]


def test_check_edges_rejects_the_wrong_shape():
    with pytest.raises(ValueError):
        imp.check_edges({"links": []}, _cluster())


# -- trigger --------------------------------------------------------------------------------------------

def _feed(tw, t, rows):
    out = None
    for dt, bid, ask, vol in rows:
        r = tw.update(t, dt, bid, ask, vol)
        out = r or out
    return out


def test_trigger_fires_once_after_a_held_jump_on_volume():
    tw = imp.TriggerWatch()
    rows = [(i * 5.0, 0.16, 0.17, 1000) for i in range(60)]                       # 5 min at 17c
    rows += [(300 + i * 5.0, 0.91, 0.93, 400000) for i in range(8)]                # 35s at 91c bid
    assert _feed(tw, "JENS", rows) == "yes"
    assert tw.update("JENS", 345.0, 0.95, 0.96, 400000) is None                   # once only


def test_trigger_waits_for_the_hold():
    tw = imp.TriggerWatch()
    rows = [(i * 5.0, 0.16, 0.17, 1000) for i in range(60)] + [(300.0, 0.91, 0.93, 400000),
                                                               (305.0, 0.92, 0.93, 400000)]
    assert _feed(tw, "X", rows) is None


def test_trigger_ignores_a_spike_that_fades_and_a_thin_book():
    tw = imp.TriggerWatch()
    rows = [(i * 5.0, 0.16, 0.17, 1000) for i in range(60)]
    rows += [(300.0, 0.91, 0.93, 400000), (305.0, 0.92, 0.94, 400000), (310.0, 0.50, 0.85, 400000),
             (315.0, 0.91, 0.93, 400000), (330.0, 0.91, 0.93, 400000)]
    assert _feed(tw, "SPIKE", rows) is None                                       # hold broken at 310
    tw = imp.TriggerWatch()
    rows = [(i * 5.0, 0.16, 0.17, 1000) for i in range(60)] + [(300 + i * 5.0, 0.91, 0.93, 1200) for i in range(8)]
    assert _feed(tw, "THIN", rows) is None                                        # 200 contracts traded


def test_trigger_no_side_mirrors_yes():
    tw = imp.TriggerWatch()
    rows = [(i * 5.0, 0.55, 0.57, 1000) for i in range(60)] + [(300 + i * 5.0, 0.03, 0.06, 90000) for i in range(8)]
    assert _feed(tw, "DROP", rows) == "no"


def test_trigger_needs_a_recent_low_not_a_market_already_at_90():
    tw = imp.TriggerWatch()
    rows = [(i * 5.0, 0.91, 0.93, 1000 + i * 1000) for i in range(80)]
    assert _feed(tw, "FLAT", rows) is None


# -- paper fills and scoring ------------------------------------------------------------------------------

BOOK = {"orderbook_fp": {"yes_dollars": [["0.20", "500"], ["0.22", "100"]],
                         "no_dollars": [["0.05", "1000"], ["0.70", "50"], ["0.77", "120"]]}}


def test_paper_fill_walks_the_asks_up_to_90c():
    f = imp.paper_fill(BOOK, "yes")                    # YES asks: 23c x120, 30c x50, 95c x1000
    assert f["best_ask"] == pytest.approx(0.23)
    assert f["qty"] == 170 and f["avg"] == pytest.approx((120 * 0.23 + 50 * 0.30) / 170, abs=1e-4)
    assert f["depth_at_max"] == 170
    g = imp.paper_fill(BOOK, "no", max_qty=50)         # NO asks: 78c x100, 80c x500
    assert g["qty"] == 50 and g["avg"] == pytest.approx(0.78)


def test_paper_fill_is_empty_when_nothing_is_cheap():
    f = imp.paper_fill({"orderbook_fp": {"no_dollars": [["0.05", "10"]]}}, "yes")
    assert f["qty"] == 0 and f["avg"] is None


def test_summary_scores_settled_fills_and_judges_once_at_15():
    trig = [{"ticker": "A%d" % i, "side": "yes", "ts": i,
             "fills": [{"then": "B%d" % i, "side": "yes", "qty": 100.0, "avg": 0.40}]} for i in range(15)]
    s = imp.summary(trig[:5], {"B%d" % i: "yes" for i in range(15)})
    assert s["verdict"] == "collecting" and s["settled_triggers"] == 5
    assert s["net_per_contract"] == pytest.approx(0.60 - 0.02, abs=0.005)        # fee ~1.68c, ceil to cents
    s = imp.summary(trig, {"B%d" % i: ("yes" if i < 5 else "no") for i in range(15)})
    assert s["settled_triggers"] == 15 and s["verdict"] == "kill"
    s = imp.summary(trig, {"B%d" % i: "yes" for i in range(15)})
    assert s["verdict"] == "pass"


def test_summary_waits_for_settlement():
    trig = [{"ticker": "A", "side": "yes", "ts": 1, "fills": [{"then": "B", "side": "yes", "qty": 10.0, "avg": 0.3}]}]
    s = imp.summary(trig, {})
    assert s["paper_fills"] == 0 and s["net_per_contract"] is None and s["triggers_with_fill"] == 1


# -- watcher wiring --------------------------------------------------------------------------------------

class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_watcher_fires_records_paper_fills_and_follows_up(tmp_path):
    clock = FakeClock()
    price = {"bid": "0.16", "ask": "0.17", "vol": "1000"}
    calls = []

    def get(path, params=None):
        calls.append(path)
        if path == "/markets":
            assert params["tickers"] == "JENS"
            return {"markets": [{"ticker": "JENS", "yes_bid_dollars": price["bid"], "yes_ask_dollars": price["ask"],
                                 "volume_fp": price["vol"]}]}
        return BOOK

    saved = {}
    mp = {"watch": ["JENS"], "edges": [{"if": "JENS", "if_result": "yes", "then": "DEC01", "then_result": "yes",
                                        "confidence": 0.97, "why": "rule"}]}
    w = wr.Watcher(mp, get=get, now=clock, put=lambda k, o: saved.__setitem__(k, o), sleep=clock.sleep, log=lambda m: None)
    for _ in range(60):
        w.poll(); clock.t += 5
    price.update(bid="0.91", ask="0.93", vol="400000")
    fired = []
    for _ in range(9):
        fired += w.poll(); clock.t += 5
    assert len(fired) == 1 and fired[0]["fills"][0]["then"] == "DEC01" and fired[0]["fills"][0]["qty"] == 170
    clock.t += 301
    w.follow_up()
    t = list(saved.values())[-1]
    assert set(t["fills"][0]["follow"]) == {"60", "300"} and not w.pending
    w.dump_state(str(tmp_path / "s.json"))
    w2 = wr.Watcher(mp, get=get, now=clock, sleep=clock.sleep, log=lambda m: None)
    w2.load_state(str(tmp_path / "s.json"))
    assert ("JENS", "yes") in w2.tw.fired and len(w2.tw.hist["JENS"]) > 10


def test_top_of_reads_dollars_and_old_cents():
    assert wr.top_of({"yes_bid_dollars": "0.41", "yes_ask_dollars": "0.43", "volume_fp": "12.5"}) == (0.41, 0.43, 12.5)
    assert wr.top_of({"yes_bid": 41, "yes_ask": 43, "volume": 7}) == (0.41, 0.43, 7.0)
    assert wr.top_of({"yes_bid_dollars": "0", "yes_ask_dollars": "1"}) == (None, None, 0.0)


def test_watcher_ignores_rows_it_did_not_ask_for():
    # If the API ever ignores ?tickers= and returns arbitrary markets, none of them may be scored.
    clock = FakeClock()
    get = lambda path, params=None: {"markets": [{"ticker": "OTHER", "yes_bid_dollars": "0.95",
                                                  "yes_ask_dollars": "0.96", "volume_fp": "1e6"}]}
    logs = []
    w = wr.Watcher({"watch": ["JENS"], "edges": []}, get=get, now=clock, sleep=clock.sleep, log=logs.append)
    w.poll()
    assert "OTHER" not in w.tw.hist and logs == ["poll 1: 0 of 1 watched markets returned"]
