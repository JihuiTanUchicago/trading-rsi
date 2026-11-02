"""Maker vs taker base-rate scan: fill rules, clustering, FDR and the research-window wiring."""
from datetime import datetime, timedelta

from app import maker_scan_run as run_mod
from app.services import maker_scan as ms
from data_pipeline.kalshi_fees import maker_fee_dollars, taker_fee_dollars

H = 3600


def bars(*rows):
    return [ms.Bar(*r) for r in rows]


def fills(bs, close_ts, result):
    return ms.contract_fills(bs, close_ts, H, result, maker_fee_dollars, taker_fee_dollars)


def test_maker_yes_fills_only_when_the_next_bar_trades_through_the_bid():
    # bar 1 posts at bid 0.40; bar 2's ask only touches 0.40 (no fill), bar 3's ask goes to 0.39 (fill).
    bs = bars((0, 0.40, 0.42), (H, 0.40, 0.42, 0.40, None, 0.40, 0.41), (2 * H, 0.38, 0.39, 0.39, None, None, None))
    out = fills(bs, 10 * H, "yes")
    key = ("yes", "maker", ms.band(0.40, ms.PRICE_BANDS), ms.band(9, ms.TTC_BANDS_H))
    pnl, posts = out[key]
    assert posts == 2       # bar 2 only touched 0.40, so the order rested again and bar 3 filled it
    assert abs(pnl - (1.0 - 0.40 - maker_fee_dollars(10, 0.40) / 10 - ms.COST_STRESS)) < 1e-9


def test_maker_no_fills_when_the_bid_trades_above_the_ask_and_pays_one_minus_ask():
    bs = bars((0, 0.60, 0.63), (H, 0.64, 0.66, None, 0.64, None, None))
    out = fills(bs, 10 * H, "no")
    key = ("no", "maker", ms.band(0.37, ms.PRICE_BANDS), ms.band(10, ms.TTC_BANDS_H))
    pnl, _ = out[key]
    assert abs(pnl - (1.0 - 0.37 - maker_fee_dollars(10, 0.37) / 10 - ms.COST_STRESS)) < 1e-9


def test_taker_pays_the_next_bars_ask_and_skips_99c():
    bs = bars((0, 0.50, 0.52), (H, 0.51, 0.55))
    out = fills(bs, 10 * H, "no")
    key = ("yes", "taker", ms.band(0.52, ms.PRICE_BANDS), ms.band(10, ms.TTC_BANDS_H))
    assert abs(out[key][0] - (0.0 - 0.55 - taker_fee_dollars(10, 0.55) / 10 - ms.COST_STRESS)) < 1e-9
    assert not [k for k in fills(bars((0, 0.97, 0.98), (H, 0.98, 0.99)), 10 * H, "yes")
                if k[0] == "yes" and k[1] == "taker"]


def test_a_gap_or_a_bar_after_close_is_never_a_fill():
    bs = bars((0, 0.40, 0.42), (5 * H, 0.30, 0.31, 0.30, None, 0.30, None))
    assert fills(bs, 10 * H, "yes") == {}
    assert fills(bars((0, 0.40, 0.42), (H, 0.30, 0.31, 0.30, None, 0.30, None)), H // 2, "yes") == {}


def test_a_void_market_pays_its_settlement_price():
    bs = bars((0, 0.40, 0.42), (H, 0.38, 0.39, 0.38, None, None, None))
    key = ("yes", "maker", ms.band(0.40, ms.PRICE_BANDS), ms.band(10, ms.TTC_BANDS_H))
    pnl, _ = fills(bs, 10 * H, 0.5)[key]
    assert abs(pnl - (0.5 - 0.40 - maker_fee_dollars(10, 0.40) / 10 - ms.COST_STRESS)) < 1e-9


def _scan_with(pnls_by_cluster, half_split=True):
    s = ms.Scan()
    key = ("yes", "maker", 3, 2)
    for i, xs in enumerate(pnls_by_cluster):
        for x in xs:
            s.add("fam", "c%d" % i, (i % 2) if half_split else 0, {key: (x, 1)})
    return s.finish()


def test_a_steady_edge_over_enough_clusters_is_a_lead():
    rep = _scan_with([[0.05 + 0.01 * (i % 3)] for i in range(60)])
    assert rep["lead_count"] == 1 and rep["leads"][0]["mean_c"] > 4


def test_one_cluster_carrying_many_contracts_is_not_counted_as_many_bets():
    # 29 clusters is below MIN_CLUSTERS however many contracts they hold.
    rep = _scan_with([[0.05] * 40 for _ in range(29)])
    assert rep["tested"] == 0 and rep["lead_count"] == 0


def test_an_edge_that_only_shows_in_one_half_is_not_a_lead():
    rep = _scan_with([[0.10] if i % 2 else [-0.01] for i in range(80)])
    assert rep["lead_count"] == 0
    assert rep["best"][0]["halves_c"][0] < 0 < rep["best"][0]["halves_c"][1]


def test_noise_rarely_survives_fdr():
    import random
    leads = 0
    for seed in range(20):
        rng = random.Random(seed)
        s = ms.Scan()
        for cell in range(35):
            for c in range(40):
                s.add("fam", "c%d" % c, c % 2, {("yes", "taker", cell % 7, cell // 7): (rng.gauss(0, 0.3), 1)})
        leads += s.finish()["lead_count"]
    assert leads <= 3       # 20 scans of 35 pure-noise cells: FDR 10% plus both halves keeps it near 0


def test_summary_lines_name_the_lead():
    rep = _scan_with([[0.05 + 0.01 * (i % 3)] for i in range(60)])
    text = "\n".join(ms.summary_lines(rep))
    assert "1 lead(s)" in text and "fam maker YES" in text


class _Cur:
    def __init__(self, partition=None):
        self.partition = partition
        self.sql = []

    def execute(self, sql, args=()):
        self.sql.append((sql, args))
        self._sql = sql

    def fetchone(self):
        return self.partition

    def fetchall(self):
        return []


def test_window_is_the_frozen_research_slice_or_the_oldest_60_percent():
    from app.trading.markets import Market
    m = Market(key="kxhighchi", series="KXHIGHCHI", label="x", candle_minutes=60, history_days=100,
               underlying=None, category="Climate and Weather")
    now = datetime(2026, 11, 2)
    f, t = run_mod.research_window(_Cur({"research_from_ts": 1000, "research_to_ts": 2000}), m, now)
    assert (f, t) == (datetime(1970, 1, 1, 0, 16, 40), datetime(1970, 1, 1, 0, 33, 20))
    f, t = run_mod.research_window(_Cur(None), m, now)
    assert f == now - timedelta(days=100) and t == f + timedelta(days=60)


def test_weather_pools_cities_and_runs_first():
    from app.trading.markets import Market
    w = Market(key="kxhighchi", series="KXHIGHCHI", label="x", candle_minutes=60, history_days=10,
               underlying=None, category="Climate and Weather")
    s = Market(key="nba_game", series="KXNBAGAME", label="x", candle_minutes=1, history_days=10,
               underlying=None, category="Sports")
    assert run_mod.family_of(w) == ("weather-high", True)
    assert run_mod.family_of(s) == ("nba_game", False)


class _DataCur:
    """hist_markets + market_candles for one weather series: 40 days, one contract a day that the
    YES maker bid gets filled on and that settles YES."""

    def __init__(self, start):
        self.start = start
        self._rows = []

    def execute(self, sql, args=()):
        if "app_data_partitions" in sql:
            self._rows = [None]
        elif "FROM hist_markets" in sql:
            self._rows = [{"ticker": "KXHIGHCHI-D%02d-B60.5" % d, "event_ticker": "KXHIGHCHI-D%02d" % d,
                           "close_time": self.start + timedelta(days=d, hours=23), "result": "yes",
                           "settle_value": None} for d in range(40)]
        else:
            d = int(args[0].split("-D")[1][:2])
            t0 = int((self.start + timedelta(days=d, hours=10)).timestamp())
            self._rows = [{"end_period_ts": t0, "yes_bid_close": "0.40", "yes_ask_close": "0.42", "raw_json": "{}"},
                          {"end_period_ts": t0 + H, "yes_bid_close": "0.38", "yes_ask_close": "0.39",
                           "raw_json": '{"al": "0.3900", "pl": "0.3900"}'}]

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return self._rows


def test_run_scans_the_window_and_reports_the_maker_cell(monkeypatch):
    from app.trading.markets import Market
    from datetime import timezone
    m = Market(key="kxhighchi", series="KXHIGHCHI", label="x", candle_minutes=60, history_days=10,
               underlying=None, category="Climate and Weather")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)
    monkeypatch.setattr(run_mod, "research_window", lambda cur, mk, now: (start, start + timedelta(days=60)))
    rep = run_mod.run(_DataCur(start), [m], log=lambda *_: None)
    assert rep["contracts"] == 40 and rep["markets"][0]["family"] == "weather-high"
    maker = [r for r in rep["best"] if r["mode"] == "maker" and r["side"] == "yes"]
    assert maker and maker[0]["clusters"] == 40 and maker[0]["fill_rate"] == 1.0
