"""Long-dated crypto calibration: kinds, look-ahead-free horizons, quotes, clustering and rules."""
from app.services import long_dated_calibration as ldc

DAY = 86400
END = 1_800_000_000


def mk(ticker, event, close, result, st="greater", title="Will BTC be above $90000 by Nov 30?", opened=END - 120 * DAY):
    return {"ticker": ticker, "event_ticker": event, "series_ticker": ticker.split("-")[0], "title": title,
            "strike_type": st, "open_time": opened, "close_time": close, "result": result}


def candle(t, bid, ask):
    return {"end_period_ts": t, "yes_bid": {"close": bid}, "yes_ask": {"close_dollars": ask}}


def test_series_filter_keeps_long_dated_families_only():
    assert ldc.is_long_dated("KXBTCMAXMON") and ldc.is_long_dated("KXETHMINY") and ldc.is_long_dated("KXBTCY")
    assert ldc.is_long_dated("KXBTCPRICE") and ldc.is_long_dated("KXBTC2026200")
    assert not ldc.is_long_dated("KXBTC15M") and not ldc.is_long_dated("KXBTCD") and not ldc.is_long_dated("KXBTC")
    assert ldc.coin_of("KXDOGEMAXY") == "DOGE"


def test_kinds_from_title_and_strike_type():
    assert ldc.kind_of(mk("A", "E", END, "yes")) == "touch_up"
    assert ldc.kind_of(mk("A", "E", END, "yes", st="less", title="below $60000 by Dec 31")) == "touch_down"
    assert ldc.kind_of(mk("A", "E", END, "yes", st="between", title="Bitcoin price range on Jan 1")) == "terminal"
    assert ldc.kind_of(mk("A", "E", END, "yes", st="custom")) is None


def test_horizons_count_back_from_the_event_end_not_an_early_hit_close():
    # strike hit 20 days before the end closes early; it must not be seen at the 14-day horizon
    hit = mk("KXBTCMAXMON-X-90", "EV", END - 20 * DAY, "yes")
    miss = mk("KXBTCMAXMON-X-95", "EV", END, "no")
    cs = {hit["ticker"]: [candle(END - 31 * DAY, 0.10, 0.12), candle(END - 15 * DAY, 0.97, 0.99)],
          miss["ticker"]: [candle(END - 31 * DAY, 0.05, 0.06), candle(END - 15 * DAY, 0.02, 0.03)]}
    rows = ldc.observations([hit, miss], cs, horizons=(30, 14))
    got = {(r["ticker"], r["h"]): r for r in rows}
    assert (hit["ticker"], 30) in got and (hit["ticker"], 14) not in got
    assert got[(miss["ticker"], 14)]["bid"] == 0.02 and got[(miss["ticker"], 30)]["mid"] == 0.055


def test_quote_ignores_future_and_stale_candles():
    cs = [candle(END - 5 * DAY, 0.2, 0.3), candle(END + 60, 0.9, 0.95)]
    assert ldc.quote_at(cs, END) is None                      # newest past candle is 5 days old
    assert ldc.quote_at(cs, END - 4 * DAY) == (0.2, 0.3)
    assert ldc.quote_at([candle(END, 0.3, 0.2)], END) is None  # crossed


def test_sell_pnl_is_bid_minus_fee_minus_payout():
    r = {"bid": 0.10, "ask": 0.11, "yes": 0.0}
    assert abs(ldc.sell_pnl(r) - (0.10 - 0.07 * 0.1 * 0.9)) < 1e-12
    assert abs(ldc.sell_pnl(dict(r, yes=1.0)) - (0.10 - 0.0063 - 1.0)) < 1e-12


def test_rules_cluster_by_end_date_and_need_six_dates():
    rows = []
    for d in range(8):
        for k in range(5):     # five strikes of one date all miss: one cluster, not five votes
            rows.append({"ticker": "T%d-%d" % (d, k), "event": "E%d" % d, "coin": "BTC", "kind": "touch_up", "h": 30,
                         "end_date": "2026-%02d-28" % (d + 1), "bid": 0.10, "ask": 0.11, "mid": 0.105, "yes": 0.0})
    s = ldc.summarize(rows)
    r = next(x for x in s["rules"] if x["name"] == "sell_up_touch_2_30c_30d")
    assert r["n"] == 40 and r["end_dates"] == 8 and r["verdict"] == "edge" and r["sell_c"] > 9
    few = ldc.summarize(rows[:10])
    assert next(x for x in few["rules"] if x["name"] == "sell_up_touch_2_30c_30d")["verdict"] == "too_few_dates"
