"""Riskless-set scan: set arithmetic, fees, completeness and the runner's wiring."""
from datetime import datetime, timedelta

from app import arb_scan_run as run_mod
from app.services import arb_scan as arb
from data_pipeline.kalshi_fees import taker_fee_dollars

H = 3600
NOFEE = lambda q, p: 0.0  # noqa: E731


def test_buy_all_profits_when_asks_sum_below_a_dollar_after_fees():
    buy, sell = arb.edges_at([(0.30, 0.31), (0.30, 0.31), (0.30, 0.32)], NOFEE)
    assert abs(buy - 0.06) < 1e-9 and abs(sell - (-0.10)) < 1e-9
    buy_f, _ = arb.edges_at([(0.30, 0.31), (0.30, 0.31), (0.30, 0.32)], taker_fee_dollars)
    assert buy_f < buy                                     # fees eat into it


def test_sell_all_profits_when_bids_sum_above_a_dollar():
    _, sell = arb.edges_at([(0.40, 0.41), (0.35, 0.36), (0.30, 0.31)], NOFEE)
    assert abs(sell - 0.05) < 1e-9


def test_a_missing_ask_voids_the_stamp_and_a_missing_bid_blocks_the_sale():
    assert arb.edges_at([(0.3, 0.0), (0.3, 0.31)], NOFEE) is None
    assert arb.edges_at([(0.3, None), (0.3, 0.31)], NOFEE) is None
    _, sell = arb.edges_at([(None, 0.02), (0.99, 0.995)], NOFEE)
    assert sell == -1.0


def test_only_one_yes_makes_an_exclusive_event():
    assert arb.exclusive_event(["no", "yes", "no"])
    assert not arb.exclusive_event(["yes", "yes", "no"]) and not arb.exclusive_event(["no", "no"])
    assert not arb.exclusive_event(["yes", "scalar"])


def test_scan_counts_hits_persistence_and_skips_the_settling_bar():
    s = arb.Scan(NOFEE)
    legs = [arb.Leg("E-A", {0: (0.30, 0.31), H: (0.30, 0.31), 2 * H: (0.30, 0.31), 3 * H: (0.2, 0.21)}, 3 * H),
            arb.Leg("E-B", {0: (0.30, 0.31), H: (0.30, 0.31), 2 * H: (0.30, 0.31), 3 * H: (0.2, 0.21)}, 4 * H),
            arb.Leg("E-C", {0: (0.40, 0.45), H: (0.30, 0.32), 2 * H: (0.30, 0.32), 3 * H: (0.2, 0.21)}, 4 * H)]
    s.add_event("fam", "E", legs, H)
    rep = s.finish()["families"][0]
    # ts 0: asks 1.07 no hit; H and 2H: asks 0.94 -> buy hits (2H persists); E-A closes at 3H, so
    # the bar ending there is its settling bar and no stamp after 2H counts
    assert rep["stamps"] == 3 and rep["hits"] == 2 and rep["persisting"] == 1 and rep["best_buy_c"] == 6.0


class _Cur:
    """hist_markets + market_candles for one 3-bracket weather event with a cheap set, and one event
    missing a leg (its listed size is 3 but only 2 have candles)."""

    def __init__(self, start):
        self.start, self._rows = start, []

    def execute(self, sql, args=()):
        close = self.start + timedelta(days=1)
        if "COUNT(*)" in sql:
            self._rows = [{"event_ticker": "KXLOWTCHI-D1", "n": 3}, {"event_ticker": "KXLOWTCHI-D2", "n": 3}]
        elif "FROM hist_markets" in sql:
            self._rows = [{"ticker": "KXLOWTCHI-D1-B%d" % i, "event_ticker": "KXLOWTCHI-D1", "close_time": close,
                           "result": "yes" if i == 1 else "no", "settle_value": None} for i in range(3)] + \
                         [{"ticker": "KXLOWTCHI-D2-B%d" % i, "event_ticker": "KXLOWTCHI-D2", "close_time": close,
                           "result": "yes" if i == 0 else "no", "settle_value": None} for i in range(2)]
        else:
            t0 = int(self.start.timestamp())
            self._rows = [{"end_period_ts": t0 + k * H, "yes_bid_close": "0.29", "yes_ask_close": "0.30",
                           "raw_json": "{}"} for k in range(3)]

    def fetchall(self):
        return self._rows


def test_run_scans_complete_exclusive_sets_only(monkeypatch):
    from app.trading.markets import Market
    m = Market(key="kxlowtchi", series="KXLOWTCHI", label="x", candle_minutes=60, history_days=400,
               underlying=None, category="Climate and Weather")
    start = datetime.now().replace(microsecond=0) - timedelta(days=5)
    rep = run_mod.run(_Cur(start), [m], log=lambda *_: None)
    fam = rep["families"][0]
    assert rep["markets"][0]["events"] == 1 and fam["family"] == "weather-low"
    assert fam["hits"] == 3 and fam["best_buy_c"] > 0     # 3 x 0.30 + fees < $1 on every bar
