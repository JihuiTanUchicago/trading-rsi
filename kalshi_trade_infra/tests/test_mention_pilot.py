"""Live mention pilot: quoting, money limits, the queue measure, fills, settlement and the loss stop."""
import types

import pytest

from app import mention_pilot_run as run
from app.services import mention_pilot as mp


# -- pure rules -------------------------------------------------------------------------------------

def test_quote_steps_inside_a_wide_spread_and_joins_a_tight_one():
    assert mp.quote_price(0.20, 0.30) == 0.29
    assert mp.quote_price(0.29, 0.30) == 0.30
    assert mp.quote_price(None, 0.40) == 0.40
    assert mp.quote_price(0.20, None) is None


def test_quote_stays_in_the_15_to_65c_band():
    assert mp.quote_price(0.05, 0.14) is None
    assert mp.quote_price(0.60, 0.70) is None      # 69c is out
    assert mp.quote_price(0.60, 0.66) is None      # 65c is the band's open top
    assert mp.quote_price(0.60, 0.65) == 0.64
    assert mp.quote_price(0.10, 0.16) == 0.15


def test_room_keeps_worst_case_loss_inside_the_stop_and_cash():
    assert mp.room(0.0, 0.0, 0.0, 50.0) == pytest.approx(25.0)              # loss stop binds first
    assert mp.room(30.0, 0.0, 0.0, 50.0) == pytest.approx(45.0)             # profit lifts it to the cap
    assert mp.room(0.0, 10.0, 5.0, 50.0) == pytest.approx(10.0)
    assert mp.room(0.0, 0.0, 0.0, 3.0) == pytest.approx(3.0)                # shard cash binds
    assert mp.room(0.0, 0.0, 0.0, None) == 0.0                              # unreadable cash: nothing new
    assert mp.room(-25.0, 0.0, 0.0, 50.0) == 0.0 and mp.halted(-25.0)


def test_order_size_is_what_is_left_of_qty_cut_to_the_room():
    assert mp.order_size(0, 0, 25.0, 0.40) == mp.QTY
    assert mp.order_size(2, 0, 25.0, 0.40) == 1
    assert mp.order_size(0, 0, 1.0, 0.40) == 1          # 60c of collateral each
    assert mp.order_size(0, 0, 0.5, 0.40) == 0


def test_sim_fill_credits_only_yes_takers_at_or_above_our_ask():
    assert mp.sim_fill(0.30, 3, 0.31, 10, True) == 3
    assert mp.sim_fill(0.30, 3, 0.30, 2, True) == 2
    assert mp.sim_fill(0.30, 3, 0.29, 10, True) == 0
    assert mp.sim_fill(0.30, 3, 0.35, 10, False) == 0


def test_fill_terms_read_a_kalshi_fill_row_as_a_no_position():
    row = {"count_fp": "3.00", "no_price_dollars": "0.7000", "yes_price_dollars": "0.3000", "fee_cost": "0.0110"}
    assert mp.fill_terms(row) == (3.0, 0.70, 0.011)
    assert mp.fill_terms({"count_fp": "2", "yes_price_dollars": "0.25"}) == (2.0, 0.75, 0.0)
    assert mp.fill_terms({"count_fp": "0"}) is None


def test_trigger_is_a_jump_to_90c_from_below_70c():
    tr = mp.Triggers()
    assert tr.see("E", "E-A", 100, 0.40) is None
    assert tr.see("E", "E-B", 101, 0.95) is None        # first print of B: no previous price
    assert tr.see("E", "E-A", 200, 0.92) == 200
    assert tr.see("E", "E-A", 300, 0.10) is None and tr.at["E"] == 200
    assert tr.live(200 + mp.DELAY_S) == {"E": 200}
    assert tr.live(200 + mp.HOLD_S) == {}


# -- the runner, with a fake exchange and a fake store ---------------------------------------------

class Store:
    """In-memory stand-in for mention_pilot_repository."""

    def __init__(self):
        self.events, self.ords, self.fills, self.pos = {}, {}, {}, {}

    def triggers_since(self, _cur, since):
        return {e: t for e, t in self.events.items() if t >= since}

    def save_trigger(self, _cur, e, t):
        self.events.setdefault(e, t)

    def add_order(self, _cur, oid, coid, uid, ev, tk, px, qty):
        self.ords[oid] = dict(order_id=oid, client_order_id=coid, event_ticker=ev, ticker=tk, yes_price=px, qty=qty,
                              filled=0.0, sim_filled=0.0, status="resting")

    def close_order(self, _cur, oid, status="cancelled"):
        if oid in self.ords and self.ords[oid]["status"] == "resting":
            self.ords[oid]["status"] = status

    def add_sim_fill(self, _cur, oid, q):
        self.ords[oid]["sim_filled"] += q

    def orders(self, _cur, status=None):
        return [dict(o) for o in self.ords.values() if status is None or o["status"] == status]

    def book_fill(self, _cur, fid, oid, uid, ev, tk, qty, no_px, fee, ts):
        if fid in self.fills:
            return False
        self.fills[fid] = qty
        self.ords[oid]["filled"] += qty
        p = self.pos.setdefault(tk, dict(id=len(self.pos) + 1, ticker=tk, qty=0.0, cost=0.0, fees=0.0, status="open"))
        p["qty"] += qty
        p["cost"] += qty * no_px
        p["fees"] += fee
        return True

    def open_positions(self, _cur, _uid):
        return [dict(p) for p in self.pos.values() if p["status"] == "open"]

    def settle_position(self, _cur, pid, result, pnl):
        for p in self.pos.values():
            if p["id"] == pid and p["status"] == "open":
                p.update(status="closed", result=result, pnl=pnl)
                return True
        return False

    def realized_pnl(self, _cur, _uid):
        return sum(p["pnl"] for p in self.pos.values() if p["status"] == "closed")

    def filled_by_ticker(self, _cur):
        out = {}
        for p in self.pos.values():
            out[p["ticker"]] = out.get(p["ticker"], 0) + p["qty"]
        return out

    def settled_count(self, _cur, _uid):
        return sum(p["status"] == "closed" for p in self.pos.values())


class Exchange:
    def __init__(self):
        self.trades, self.markets, self.fills, self.results = [], {}, [], {}
        self.placed, self.cancelled = [], []
        self.cash = 50.0

    def get(self, path, params=None):
        if path == "/markets/trades":
            out, self.trades = self.trades, []
            return {"trades": out, "cursor": ""}
        if path == "/markets":
            return {"markets": list(self.markets.values())}
        if path.startswith("/markets/"):
            return {"market": self.results.get(path.split("/")[-1], {"status": "active"})}
        raise AssertionError(path)

    def get_auth(self, path, params=None, credentials=None):
        if path.endswith("/balance"):
            return {"balance_breakdown": [{"exchange_index": 0, "balance": str(self.cash)}]}
        if path.endswith("/fills"):
            return {"fills": self.fills}
        if path.endswith("/orders"):
            return {"orders": []}
        raise AssertionError(path)

    def post(self, path, body, credentials=None):
        self.placed.append(body)
        return {"order": {"order_id": "o%d" % len(self.placed)}}

    def delete(self, path, credentials=None):
        self.cancelled.append(path.rsplit("/", 1)[-1])
        return {}


def trade(tk, ts, p, c=5, yes=True, tid=None):
    from datetime import datetime, timezone
    return {"trade_id": tid or "%s-%s-%s" % (tk, ts, p), "ticker": tk, "yes_price_dollars": "%.4f" % p,
            "count_fp": "%.2f" % c, "taker_side": "yes" if yes else "no",
            "created_time": datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")}


def market(tk, bid, ask, idx=0):
    return {"ticker": tk, "yes_bid_dollars": "%.4f" % bid, "yes_ask_dollars": "%.4f" % ask, "exchange_index": idx}


@pytest.fixture
def world(monkeypatch):
    store, ex, clock = Store(), Exchange(), {"t": 1_000_000.0}
    monkeypatch.setattr(run, "repo", types.SimpleNamespace(**{k: getattr(store, k) for k in dir(store)
                                                              if not k.startswith("_")}))
    monkeypatch.setattr(run.Pilot, "report", lambda self: {})
    api = run.Api(("kid", None), get=ex.get, get_auth=ex.get_auth, post_auth=ex.post, delete_auth=ex.delete,
                  sleep=lambda s: None)
    pilot = run.Pilot(None, api, 1, {"KXTRUMPMENTION"}, now=lambda: clock["t"], log=lambda m: None)
    return pilot, store, ex, clock


E = "KXTRUMPMENTION-26NOV14"


def go_live(pilot, ex, clock):
    t = clock["t"]
    ex.trades = [trade(E + "-TARIFF", t - 20, 0.50), trade(E + "-TARIFF", t - 10, 0.95)]
    pilot.step()
    clock["t"] += 10


def test_a_live_event_gets_post_only_yes_asks_in_band_markets_only(world):
    pilot, store, ex, clock = world
    ex.markets = {k: market(k, b, a) for k, b, a in [(E + "-CHINA", 0.20, 0.30), (E + "-WALL", 0.40, 0.41),
                                                       (E + "-TARIFF", 0.95, 0.97), (E + "-LOW", 0.02, 0.05)]}
    go_live(pilot, ex, clock)
    pilot.step()
    got = {b["ticker"]: b for b in ex.placed}
    assert set(got) == {E + "-CHINA", E + "-WALL"}
    assert got[E + "-CHINA"]["price"] == "0.2900" and got[E + "-WALL"]["price"] == "0.4100"
    for b in got.values():
        assert b["side"] == "ask" and b["post_only"] is True and b["time_in_force"] == "good_till_canceled"
        assert b["count"] == "%d.00" % mp.QTY and b["client_order_id"].startswith(mp.CLIENT_PREFIX)
    assert store.events == {E: clock["t"] - 10 - 10}


def test_collateral_never_passes_the_loss_stop(world):
    pilot, store, ex, clock = world
    ex.markets = {E + "-W%02d" % i: market(E + "-W%02d" % i, 0.10, 0.16) for i in range(40)}   # 85c each
    go_live(pilot, ex, clock)
    pilot.step()
    held = sum(mp.collateral(float(b["count"]), float(b["price"])) for b in ex.placed)
    assert held <= mp.LOSS_STOP_USD + 1e-9 and held > mp.LOSS_STOP_USD - 3


def test_a_said_word_is_cancelled_and_never_quoted_again(world):
    pilot, store, ex, clock = world
    ex.markets = {E + "-CHINA": market(E + "-CHINA", 0.20, 0.30)}
    go_live(pilot, ex, clock)
    pilot.step()
    assert len(ex.placed) == 1
    ex.trades = [trade(E + "-CHINA", clock["t"], 0.85)]
    ex.markets = {E + "-CHINA": market(E + "-CHINA", 0.84, 0.90)}
    clock["t"] += 30
    pilot.step()
    assert ex.cancelled == ["o1"] and not pilot.resting
    ex.markets = {E + "-CHINA": market(E + "-CHINA", 0.20, 0.30)}
    clock["t"] += 30
    pilot.step()
    assert len(ex.placed) == 1


def test_prints_through_our_ask_count_as_replay_fills_and_real_fills_are_booked_once(world):
    pilot, store, ex, clock = world
    ex.markets = {E + "-CHINA": market(E + "-CHINA", 0.20, 0.30)}
    go_live(pilot, ex, clock)
    pilot.step()
    clock["t"] += 5
    ex.trades = [trade(E + "-CHINA", clock["t"], 0.29, c=2), trade(E + "-CHINA", clock["t"] + 1, 0.28, c=9),
                 trade(E + "-CHINA", clock["t"] + 2, 0.31, c=9, yes=False)]
    ex.fills = [{"fill_id": "f1", "order_id": "o1", "count_fp": "1.00", "no_price_dollars": "0.7100",
                 "yes_price_dollars": "0.2900", "fee_cost": "0.0036", "ts": clock["t"]}]
    pilot.step()
    pilot.step()   # the same fill row again books nothing
    o = store.ords["o1"]
    assert o["sim_filled"] == 2 and o["filled"] == 1
    assert store.pos[E + "-CHINA"]["qty"] == 1 and store.pos[E + "-CHINA"]["cost"] == pytest.approx(0.71)
    assert mp.summary(store.ords, 0, 0, 0)["fill_ratio"] == 0.5


def test_window_end_cancels_and_settlement_books_the_no_payout(world):
    pilot, store, ex, clock = world
    ex.markets = {E + "-CHINA": market(E + "-CHINA", 0.20, 0.30)}
    go_live(pilot, ex, clock)
    pilot.step()
    ex.fills = [{"fill_id": "f1", "order_id": "o1", "count_fp": "3.00", "no_price_dollars": "0.7100",
                 "fee_cost": "0.0100", "ts": clock["t"]}]
    pilot.step()
    assert not pilot.resting   # fully filled
    clock["t"] += mp.HOLD_S + 61
    ex.results[E + "-CHINA"] = {"status": "finalized", "result": "no"}
    pilot.step()
    p = store.pos[E + "-CHINA"]
    assert p["status"] == "closed" and p["pnl"] == pytest.approx(3 * 0.29 - 0.01)
    assert pilot.realized == pytest.approx(0.86)


def test_loss_stop_cancels_everything_and_stops(world):
    pilot, store, ex, clock = world
    ex.markets = {E + "-CHINA": market(E + "-CHINA", 0.20, 0.30), E + "-WALL": market(E + "-WALL", 0.30, 0.40)}
    go_live(pilot, ex, clock)
    pilot.step()
    store.pos["X"] = dict(id=99, ticker="X", qty=40.0, cost=26.0, fees=0.0, status="closed", pnl=-26.0)
    clock["t"] += 61
    pilot.step()
    assert pilot.stopped and set(ex.cancelled) == {"o1", "o2"}


def test_runner_is_off_without_both_flags(monkeypatch):
    monkeypatch.delenv("TAU_MENTION_PILOT_ENABLED", raising=False)
    monkeypatch.setenv("TAU_MENTION_PILOT_OFF_SLEEP_S", "0")
    monkeypatch.setattr(run, "Pilot", lambda *a, **k: (_ for _ in ()).throw(AssertionError("started")))
    run.main()
    monkeypatch.setenv("TAU_MENTION_PILOT_ENABLED", "1")
    monkeypatch.delenv("APP_LIVE_TRADING_ENABLED", raising=False)
    run.main()
