"""Bitcoin maker pilot: live inputs built as the replay built them, the dry-run shadow venue, and the guards."""
import math
import time
from datetime import datetime, timezone

from app import btc_maker_pilot_run as run
from app.services import btc_maker_pilot as bp
from trading_core.domain import InstrumentId
from trading_core.domain.orders import Side

POLICY = {"series": "KXBTC", "quote_size": 5000.0, "max_inventory": 500.0, "fair_source": "spot", "move_window_s": 10,
          "rewarded_only": False, "back_off": True, "contract_loss_limit": 2.0, "improve_min_spread_ticks": 10,
          "max_secs_to_close": 3600.0, "max_touch_size": 900.0, "mid_band": [0.03, 0.97], "min_edge": 0.02,
          "pull_before_close_s": 1200.0, "pull_cooldown_s": 20.0, "pull_move_bp": 15.0, "skew_ticks": 10}


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def test_spot_moves_and_realized_vol_match_the_feature_definitions():
    s = bp.SpotTracker()
    for i in range(301):
        s.add(1000 + i, 100.0 * math.exp(0.0001 * (1 if i % 2 else -1) * (i > 0)))
    assert s.rv() is not None and abs(s.rv() - 0.0002) < 0.00002          # alternating +-1bp returns
    t = bp.SpotTracker()
    t.add(0, 100.0)
    t.add(10, 100.0 * math.exp(0.002))
    assert abs(t.move_bp(10) - 20.0) < 1e-6 and t.move_bp(30) is None


def test_fair_value_only_for_single_strike_contracts():
    assert bp.tail_fair(100.0, 100.0, None, 0.001, 100) == 0.5
    above = bp.tail_fair(101.0, 100.0, None, 0.001, 100)
    assert 0.5 < above < 1.0 and abs(bp.tail_fair(99.0, None, 100.0, 0.001, 100) - above) < 0.02
    assert bp.tail_fair(100.0, 99.0, 101.0, 0.001, 100) is None              # a range is priced off the mid


def test_books_markets_and_pilot_size():
    now = 1_800_000_000.0
    page = {"markets": [{"ticker": "KXBTC-X-B1", "close_time": _iso(now + 1800), "floor_strike": 97000, "cap_strike": 97500,
                         "yes_bid_dollars": "0.10", "yes_ask_dollars": "0.14"},
                        {"ticker": "KXBTC-Y-B1", "close_time": _iso(now + 7200)},
                        {"ticker": "KXBTCD-X-T1", "close_time": _iso(now + 1800)}]}
    ms = bp.markets_from(page, now, 3600)
    assert [m.ticker for m in ms] == ["KXBTC-X-B1"] and abs(ms[0].listed_mid - 0.12) < 1e-9
    q = bp.quote_from_book("KXBTC-X-B1", {"orderbook_fp": {"yes_dollars": [["0.38", "5"], ["0.40", "100"]],
                                                           "no_dollars": [["0.55", "70"]]}}, now, 3.0)
    assert (q.bid, q.ask, q.bid_size, q.ask_size, q.features["spot_move_bp"]) == (0.40, 0.45, 100.0, 70.0, 3.0)
    assert bp.quote_from_book("KXBTC-X-B1", {"orderbook_fp": {"yes_dollars": [["0.4", "1"]], "no_dollars": []}}, now, None) is None
    cfg = bp.pilot_config(POLICY, 3, 3)
    assert (cfg.max_inventory, cfg.quote_size, cfg.pull_move_bp, cfg.mid_band) == (3.0, 3.0, 15.0, (0.03, 0.97))


def test_guards_stop_on_loss_and_pause_over_the_collateral_cap():
    assert bp.guard(-15.0, 1.0, 15, 20).stop
    v = bp.guard(-1.0, 25.0, 15, 20)
    assert v.pause and not v.stop and "cap" in v.reason
    assert not bp.guard(2.0, 5.0, 15, 20).pause


def test_shadow_fills_only_on_prints_strictly_through_our_price():
    v = run.ShadowVenue()
    yes = InstrumentId("kalshi", "KXBTC-X-B1", "yes")
    no = InstrumentId("kalshi", "KXBTC-X-B1", "no")
    v.post(yes, Side.BUY, 0.40, 3, "a")
    v.post(no, Side.BUY, 0.55, 3, "b")
    assert not v.capabilities.sends_real_orders and abs(v.resting_collateral() - (1.2 + 1.65)) < 1e-9
    v.on_print("KXBTC-X-B1", 0.40, 10, "no", 1.0)                           # at our price: no queue credit
    assert v.take_fills() == []
    v.on_print("KXBTC-X-B1", 0.39, 2, "no", 2.0)                            # through our YES bid
    v.on_print("KXBTC-X-B1", 0.44, 9, "yes", 2.5)                           # NO at 0.56: above our 0.55 bid
    v.on_print("KXBTC-X-B1", 0.46, 9, "yes", 3.0)                           # NO at 0.54: through it
    fills = v.take_fills()
    assert [(f.instrument.outcome, f.qty) for f in fills] == [("yes", 2.0), ("no", 3.0)]
    assert [o.qty for o in v.open_orders()] == [1.0]


class FakeKalshi:
    def __init__(self, now):
        self.now = now
        self.trades = []
        self.markets = 1

    def __call__(self, path, params=None):
        if path == "/markets":
            return {"markets": [{"ticker": "KXBTC-X-B%d" % i if i else "KXBTC-X-B1", "close_time": _iso(self.now + 2400),
                                 "floor_strike": 97000 + 500 * i, "cap_strike": 97500 + 500 * i,
                                 "yes_bid_dollars": "0.40", "yes_ask_dollars": "0.45"}
                                for i in range(self.markets)]}
        if path.endswith("/orderbook"):
            return {"orderbook_fp": {"yes_dollars": [["0.40", "100"]], "no_dollars": [["0.55", "100"]]}}
        if path == "/markets/trades":
            out, self.trades = self.trades, []
            return {"trades": out}
        raise AssertionError(path)


def test_a_dry_run_step_quotes_both_sides_books_shadow_fills_and_stops_on_loss(monkeypatch):
    now = time.time()
    k = FakeKalshi(now)
    p = run.Pilot(k, run.ShadowVenue(), POLICY, bp.pilot_config(POLICY, 3, 3),
                  spot_get=lambda url: {"price": "97200"}, log=lambda m: None)
    assert not p.live
    p.step()
    sides = sorted(o.instrument.outcome for o in p.venue.open_orders())
    assert sides == ["no", "yes"] and all(o.qty <= 3 for o in p.venue.open_orders())
    yes_px = [o.price for o in p.venue.open_orders() if o.instrument.outcome == "yes"][0]
    k.trades = [{"ticker": "KXBTC-X-B1", "yes_price_dollars": str(round(yes_px - 0.02, 2)), "count_fp": "5",
                 "taker_side": "no", "created_time": _iso(now + 1)}]
    p.step()
    assert p.counts["fills"] == 1 and p.counts["filled_qty"] == 3.0
    rep = p.report()
    assert rep["mode"] == "dry" and rep["counts"]["fills"] == 1 and rep["fills"][0]["side"] == "yes"
    monkeypatch.setenv("TAU_BTC_MAKER_PILOT_LOSS_STOP", "0.0001")
    p.mids["KXBTC-X-B1"] = 0.01                                             # the held YES marks far below cost
    k2 = FakeKalshi(now)
    k2.__class__ = type("Crash", (FakeKalshi,), {"__call__": lambda self, path, params=None: (
        {"orderbook_fp": {"yes_dollars": [["0.01", "100"]], "no_dollars": [["0.98", "100"]]}}
        if path.endswith("/orderbook") else FakeKalshi.__call__(self, path, params))})
    p.get = k2
    p.step()
    assert p.stopped and p.venue.open_orders() == []


def test_runs_end_just_after_an_hourly_close():
    h = 1_800_000_000 - 1_800_000_000 % 3600
    assert run.run_deadline(h + 140) == h + 3600 + 120
    assert run.run_deadline(h + 3600 - 100) == h + 7200 + 120                # under 10 minutes left: the next one


def test_the_universe_fits_the_collateral_budget_held_contracts_first():
    def q(t, mid):
        return bp.Quote(InstrumentId("kalshi", t, "yes"), 0.0, mid - 0.01, mid + 0.01, 10, 10)
    quotes = {x.instrument: x for x in (q("A", 0.10), q("B", 0.48), q("C", 0.55), q("D", 0.90), q("E", 0.30))}
    u = bp.pick_universe(quotes, {"D"}, 10.0, 3)                            # $10 buys 3 contracts at size 3
    assert [y.symbol for y in u] == ["D", "B", "C"]
    assert bp.pick_universe(quotes, set(), 2.0, 3) == []


def test_a_small_cap_never_churns_post_cancel_each_step():
    now = time.time()
    k = FakeKalshi(now)
    k.markets = 12
    p = run.Pilot(k, run.ShadowVenue(), POLICY, bp.pilot_config(POLICY, 3, 3),
                  spot_get=lambda url: {"price": "97200"}, log=lambda m: None)
    for _ in range(4):
        p.step()
    assert p.counts["paused"] == 0 and p.counts["cancels"] == 0 and p.counts["rotations"] == 0
    assert p.venue.resting_collateral() <= p.cap and len({o.instrument.symbol for o in p.venue.open_orders()}) <= 6


def test_a_quoted_contract_keeps_its_slot_near_the_cut():
    def q(t, mid):
        return bp.Quote(InstrumentId("kalshi", t, "yes"), 0.0, mid - 0.01, mid + 0.01, 10, 10)
    quotes = {x.instrument: x for x in (q("A", 0.50), q("B", 0.47), q("C", 0.44), q("D", 0.40), q("E", 0.10))}
    assert [y.symbol for y in bp.pick_universe(quotes, set(), 6.0, 3)] == ["A", "B"]
    # C ticked just past B, but B is quoted and still within two places of the cut: no swap
    assert [y.symbol for y in bp.pick_universe(quotes, set(), 6.0, 3, quoted={"B", "D"})] == ["B", "D"]
    # E is far past the cut: it gives up its slot to the best unquoted contract
    assert [y.symbol for y in bp.pick_universe(quotes, set(), 6.0, 3, quoted={"B", "E"})] == ["B", "A"]


class FakeS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        raise KeyError(Key)

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body


def test_every_run_keeps_its_own_report_next_to_latest():
    now = time.time()
    s3 = FakeS3()
    p = run.Pilot(FakeKalshi(now), run.ShadowVenue(), POLICY, bp.pilot_config(POLICY, 3, 3), s3=s3,
                  spot_get=lambda url: {"price": "97200"}, log=lambda m: None)
    p.step()
    p.report()
    keys = sorted(s3.objects)
    assert keys[0].endswith("latest.json") and keys[1].startswith(run.PREFIX + "runs/") and keys[1].endswith("_dry.json")
    assert s3.objects[keys[0]] == s3.objects[keys[1]]
