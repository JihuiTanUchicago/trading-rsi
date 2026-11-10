"""Mention-market calibration: series filter, words, look-ahead-free quotes, track record, rules."""
from app.services import mention_calibration as mc

H = 3600
END = 1_800_000_000


def mk(tk, ev, end, result, word, series="KXTRUMPMENTION", opened=None, close=None):
    return {"ticker": tk, "event_ticker": ev, "series_ticker": series, "yes_sub_title": word,
            "open_time": opened if opened is not None else end - 100 * H, "close_time": close or end, "result": result}


def raw(t, bid, ask):
    return {"end_period_ts": t, "yes_bid": {"close_dollars": bid}, "yes_ask": {"close": ask}}


def test_series_filter():
    assert mc.is_mention_series("KXTRUMPMENTION")
    assert mc.is_mention_series("KXFOO", category="Mentions")
    assert mc.is_mention_series("KXFOO", title="What will Powell say at his press conference?")
    assert not mc.is_mention_series("KXBTC15M", category="Crypto", title="Bitcoin price up or down")


def test_word_normalized_from_custom_strike_or_subtitle():
    assert mc.word_of({"custom_strike": {"Word": "Tariff / Tariffs"}}) == "tariff / tariffs"
    assert mc.word_of({"yes_sub_title": "  Crypto!! "}) == "crypto"


def test_compact_and_quotes_respect_time_and_staleness():
    c = mc.compact([raw(END - 30 * H, 0.2, 0.25), raw(END - 23 * H, 0.3, 0.35), raw(END - H, 0.9, 0.95), {"end_period_ts": END}])
    assert len(c) == 3
    q = mc.horizon_quotes(c, END, horizons=(24, 6, 2))
    assert q == {24: (0.2, 0.25)}          # 6h and 2h: newest earlier candle is 17h+ old, too stale
    assert mc.horizon_quotes(c, END, horizons=(0,)) == {0: (0.9, 0.95)}


def test_early_close_on_a_said_word_is_not_seen_after_it_closed():
    said = mk("A", "E1", END, "yes", "tariff", close=END - 10 * H)   # said 10h before the speech ended
    other = mk("B", "E1", END, "no", "bitcoin")
    quotes = {"A": {24: (0.4, 0.45), 6: (0.97, 0.99)}, "B": {24: (0.1, 0.12), 6: (0.05, 0.06)}}
    rows = mc.observations([said, other], quotes, horizons=(24, 6))
    got = {(r["ticker"], r["h"]) for r in rows}
    assert ("A", 24) in got and ("A", 6) not in got and ("B", 6) in got


def test_track_record_uses_only_events_ended_before_the_observation():
    ms, quotes = [], {}
    for i in range(6):     # "tariff" said in 5 of 6 past weekly events
        tk = "T%d" % i
        ms.append(mk(tk, "E%d" % i, END + i * 7 * 24 * H, "yes" if i != 2 else "no", "tariff"))
        quotes[tk] = {24: (0.40, 0.45)}
    rows = {r["ticker"]: r for r in mc.observations(ms, quotes, horizons=(24,))}
    assert rows["T0"]["prior_n"] == 0 and rows["T5"]["prior_n"] == 5
    assert abs(rows["T5"]["prior_p"] - (4 + 1) / (5 + 2)) < 1e-12
    # 5 priors at 71% vs a 45c ask: buy; with only 3 priors: no trade
    assert mc.prior_trade(rows["T5"]) == mc.buy_pnl(rows["T5"])
    assert mc.prior_trade(rows["T3"]) is None


def test_rules_need_30_events_and_cluster_by_event():
    rows = []
    for e in range(40):
        for w in range(3):
            rows.append({"ticker": "%d-%d" % (e, w), "event": "E%d" % e, "series": "S", "word": "w%d" % w, "h": 24,
                         "end": END + e * H, "bid": 0.20, "ask": 0.22, "mid": 0.21, "yes": 0.0, "prior_n": 0, "prior_p": 0.5})
    s = mc.summarize(rows)
    r = next(x for x in s["rules"] if x["name"] == "sell_yes_5_40c_24h")
    assert r["trades"] == 120 and r["events"] == 40 and r["verdict"] == "edge"
    few = mc.summarize(rows[:60])
    assert next(x for x in few["rules"] if x["name"] == "sell_yes_5_40c_24h")["verdict"] == "too_few_events"
