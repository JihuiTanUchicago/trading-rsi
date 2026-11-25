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


T0 = 1_794_697_200.0   # 2026-11-14 23:00Z


def test_trigger_is_a_jump_to_90c_from_below_70c():
    tr = mp.Triggers()
    ev = "KXX-26NOV14"
    assert tr.see(ev, ev + "-A", T0 + 100, 0.40) is None
    assert tr.see(ev, ev + "-B", T0 + 101, 0.95) is None        # first print of B: no previous price
    assert tr.see(ev, ev + "-A", T0 + 200, 0.92) == T0 + 200
    assert tr.see(ev, ev + "-A", T0 + 300, 0.10) is None and tr.at[ev] == T0 + 200
    assert tr.live(T0 + 200 + mp.DELAY_S) == {ev: T0 + 200}
    assert tr.live(T0 + 200 + mp.HOLD_S) == {}


def test_a_jump_weeks_before_the_event_date_is_not_a_live_event():
    tr = mp.Triggers()
    ev = "KXEARNINGSMENTIONCHWY-26DEC09"
    assert tr.see(ev, ev + "-SMAR", T0, 0.60) is None
    assert tr.see(ev, ev + "-SMAR", T0 + 10, 0.90) is None and not tr.at
    assert mp.on_event_day("KXHILLARYMENTION-26NOV14", 1_794_706_026)      # 11-15 01:27Z, the US evening
    assert mp.on_event_day("KXFTNMENTION-26NOV15", 1_794_757_594)
    assert mp.on_event_day("KXFTNMENTION-26NOV15", T0)                     # the UTC day before counts
    assert not mp.on_event_day("KXFTNMENTION-26NOV15", T0 - 86400)         # two days before does not
    assert not mp.on_event_day("KXFTNMENTION-26NOV15", T0 + 3 * 86400)
    assert not mp.on_event_day("NODATE", T0)
    assert mp.event_date("KXNBAMENTION-26NOV14LALBOS").day == 14
    assert mp.Triggers({ev: T0, "KXX-26NOV14": T0}).at == {"KXX-26NOV14": T0}   # saved triggers too


# -- the runner, with a fake exchange and a fake store ---------------------------------------------

class Store:
    """In-memory stand-in for mention_pilot_repository."""

    def __init__(self):
        self.events, self.ords, self.fills, self.pos, self.said = {}, {}, {}, {}, {}
        self.exit_orders, self.exit_fills = {}, {}

    def add_exit(self, _cur, oid, pid, tk, px, qty, ts):
        self.exit_orders[oid] = dict(order_id=oid, position_id=pid, ticker=tk, yes_price=px, qty=qty, placed_ts=ts)

    def exits(self, _cur):
        return dict(self.exit_orders)

    def book_exit_fill(self, _cur, fid, oid, pid, qty, yes_px, fee, ts):
        p = next((p for p in self.pos.values() if p["id"] == pid and p["status"] == "open"), None)
        if p is None or fid in self.exit_fills:
            return None
        q = min(qty, p["qty"])
        cost, fees = p["cost"] * q / p["qty"], p["fees"] * q / p["qty"]
        pnl = round(q * (1 - yes_px) - cost - fees - fee, 4)
        self.exit_fills[fid] = pnl
        p["qty"] -= q
        p["cost"] -= cost
        p["fees"] -= fees
        if p["qty"] <= 1e-9:
            p.update(status="closed", result="sold", pnl=0.0)
        return pnl

    def save_said(self, _cur, tk, ts):
        self.said.setdefault(tk, ts)

    def said_since(self, _cur, since):
        return [t for t, ts in self.said.items() if ts >= since]

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
        return sum(p["pnl"] for p in self.pos.values() if p["status"] == "closed") + sum(self.exit_fills.values())

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
        self.held = {}   # ticker -> Kalshi position (negative = NO)
        self.refuse = lambda body: False   # True = Kalshi rejects this order

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
        if path.endswith("/positions"):
            return {"market_positions": [{"ticker": t, "position_fp": "%.2f" % q} for t, q in self.held.items()
                                         if t == (params or {}).get("ticker")]}
        raise AssertionError(path)

    def post(self, path, body, credentials=None):
        if self.refuse(body):
            raise RuntimeError("POST failed 400: invalid count")
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
    store, ex, clock = Store(), Exchange(), {"t": T0}
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


def test_a_said_word_stays_said_for_the_next_run(world, monkeypatch):
    """Runs restart every 55 minutes mid-event: the new run must not quote a word the old one saw said."""
    pilot, store, ex, clock = world
    go_live(pilot, ex, clock)
    ex.markets = {E + "-CHINA": market(E + "-CHINA", 0.20, 0.30)}
    ex.trades = [trade(E + "-CHINA", clock["t"], 0.92)]
    pilot.step()
    assert not ex.placed and E + "-CHINA" in store.said
    clock["t"] += 60
    ex.markets = {E + "-CHINA": market(E + "-CHINA", 0.20, 0.30)}   # the price fell back
    fresh = run.Pilot(None, pilot.api, 1, {"KXTRUMPMENTION"}, now=lambda: clock["t"], log=lambda m: None)
    fresh.step()
    assert not ex.placed


def test_an_event_goes_live_once_even_after_a_restart_past_its_window(world):
    """A run that starts after the window must remember the trigger: a later 70c->90c jump on the same
    event is not a second live window (KXLUCKEYMENTION-26NOV18 re-triggered 5.5h later, 2026-11-18)."""
    pilot, store, ex, clock = world
    go_live(pilot, ex, clock)
    first = dict(store.events)
    clock["t"] += mp.HOLD_S + 3 * 3600
    fresh = run.Pilot(None, pilot.api, 1, {"KXTRUMPMENTION"}, now=lambda: clock["t"], log=lambda m: None)
    ex.markets = {E + "-CHINA": market(E + "-CHINA", 0.20, 0.30)}
    ex.trades = [trade(E + "-WALL", clock["t"] - 20, 0.50), trade(E + "-WALL", clock["t"] - 10, 0.95)]
    fresh.step()
    clock["t"] += 30
    fresh.step()
    assert store.events == first and not fresh.triggers.live(clock["t"]) and not ex.placed


def test_tape_flags_a_fall_of_more_than_10c_against_the_print_5_minutes_back():
    tp = mp.Tape()
    tp.add("W", T0, 0.45)
    tp.add("W", T0 + 100, 0.40)
    assert not tp.fell("W", T0 + 200)               # nothing 5 minutes old yet
    tp.add("W", T0 + 290, 0.34)
    assert tp.fell("W", T0 + 300)                   # 0.34 vs 0.45 at T0: down 11c
    assert not tp.fell("W", T0 + 400)               # reference is now 0.40 (T0+100): down 6c
    assert not tp.fell("NEW", T0) and not tp.fell("W", T0 - 1)
    tp.add("W", T0 + 1000, 0.36)
    assert not tp.fell("W", T0 + 1000)              # flat 5 minutes after the fall: quoting again


def test_no_yes_ask_right_after_a_sharp_fall_and_a_resting_one_is_pulled(world):
    pilot, store, ex, clock = world
    ex.markets = {E + "-CHINA": market(E + "-CHINA", 0.20, 0.30), E + "-WALL": market(E + "-WALL", 0.20, 0.30)}
    go_live(pilot, ex, clock)
    pilot.step()
    assert {b["ticker"] for b in ex.placed} == {E + "-CHINA", E + "-WALL"}
    t = clock["t"]
    clock["t"] += mp.DROP_S + 30
    ex.trades = [trade(E + "-CHINA", t + 1, 0.45, yes=False), trade(E + "-CHINA", clock["t"] - 5, 0.30, yes=False)]
    pilot.step()
    assert ex.cancelled == ["o1"] and {o["ticker"] for o in pilot.resting.values()} == {E + "-WALL"}
    clock["t"] += mp.REQUOTE_MIN_S + 1
    pilot.step()
    assert len(ex.placed) == 2                      # still falling-back: no new CHINA ask
    clock["t"] += mp.DROP_S + 1                     # 5 flat minutes later the market is quotable again
    pilot.step()
    assert [b["ticker"] for b in ex.placed[2:]] == [E + "-CHINA"]


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


def test_cancel_of_an_order_already_gone_is_done_without_the_deprecated_path():
    calls = []

    def delete(path, credentials=None):
        calls.append(path)
        raise RuntimeError("DELETE %s failed 404: not found" % path)

    api = run.Api(("kid", None), get=None, get_auth=None, post_auth=None, delete_auth=delete, sleep=lambda s: None)
    assert api.cancel("abc") is True
    assert calls == [run.ORDERS + "/abc"]


def test_position_risk_is_the_loss_if_sold_at_the_no_bid():
    assert mp.position_risk(3, 2.40, 0.02) == 0.0                              # decided winner
    assert mp.position_risk(3, 2.46, 1.00) == pytest.approx(2.46)              # said: all of it
    assert mp.position_risk(3, 2.10, 0.50) == pytest.approx(0.60)
    assert mp.position_risk(3, 2.10, None) == pytest.approx(2.10)


def test_room_counts_mark_risk_in_the_loss_leg_only():
    assert mp.room(0.0, 24.51, 0.0, 26.23) == pytest.approx(0.49)
    assert mp.room(0.0, 24.51, 0.0, 26.23, open_risk=10.0) == pytest.approx(15.0)
    assert mp.room(0.0, 40.0, 0.0, 60.0, open_risk=0.0) == pytest.approx(5.0)   # capital cap still on cost
    assert mp.room(-25.0, 0.0, 0.0, 50.0, open_risk=0.0) == 0.0


def _decided_book(store, ex, ticker, ask):
    store.pos[ticker] = dict(id=1, ticker=ticker, qty=30.0, cost=24.6, fees=0.0, status="open")   # $0.40 of loss room at cost
    ex.results[ticker] = {"status": "active", "yes_ask_dollars": "%.4f" % ask}


@pytest.mark.parametrize("flag,ask,quotes", [("0", 0.01, False), ("1", 0.01, True), ("1", 1.00, False)])
def test_decided_unsettled_words_block_the_next_event_only_at_cost(world, monkeypatch, flag, ask, quotes):
    pilot, store, ex, clock = world
    monkeypatch.setenv("TAU_MENTION_PILOT_RISK_AT_MARK", flag)
    _decided_book(store, ex, "KXHILLARYMENTION-26NOV14-EPST", ask)
    pilot = run.Pilot(None, pilot.api, 1, {"KXTRUMPMENTION"}, now=lambda: clock["t"], log=lambda m: None)
    go_live(pilot, ex, clock)
    ex.markets = {E + "-CHINA": market(E + "-CHINA", 0.20, 0.30)}
    clock["t"] += 61
    pilot.step()
    assert bool(ex.placed) is quotes


H = "KXHILLARYMENTION-26NOV14"


def _held(store, ex, tk, qty=3.0, cost=2.40, status="active", ask=0.01, pid=1):
    store.pos[tk] = dict(id=pid, event_ticker=H, ticker=tk, qty=qty, cost=cost, fees=0.0, status="open")
    ex.results[tk] = {"status": status, "yes_ask_dollars": "%.4f" % ask}
    ex.held[tk] = -qty


def test_should_exit_a_decided_no_any_time():
    assert mp.should_exit(0.01) and mp.should_exit(0.02)
    assert not mp.should_exit(0.03)
    assert not mp.should_exit(None)
    assert not mp.should_exit(0.0)


def test_should_exit_said_only_while_the_no_still_has_a_bid():
    assert mp.should_exit_said(0.98, 0.99) and mp.should_exit_said(0.97, 0.98)
    assert not mp.should_exit_said(0.98, 1.00)      # nobody bids NO: nothing to sell into
    assert not mp.should_exit_said(0.55, 0.99)      # disputed (AICO 11-23 fell 99c -> 55c): hold
    assert not mp.should_exit_said(None, 0.99)


def test_a_decided_winner_is_sold_back_once_and_frees_the_loss_room(world):
    pilot, store, ex, clock = world
    _held(store, ex, H + "-EPST", ask=0.01)
    _held(store, ex, H + "-IMPE", ask=1.00, cost=2.46, pid=2)   # said: held to settlement
    pilot.open_cost = pilot._open_cost()
    pilot.step()
    exits = [b for b in ex.placed if b["client_order_id"].startswith(mp.EXIT_PREFIX)]
    assert len(exits) == 1
    b = exits[0]
    assert b["ticker"] == H + "-EPST" and b["side"] == "bid" and b["price"] == "0.0100"
    assert b["count"] == "3.00" and b["time_in_force"] == "immediate_or_cancel" and "post_only" not in b
    oid = "o%d" % len(ex.placed)
    ex.fills = [{"fill_id": "x1", "order_id": oid, "count_fp": "3.00", "yes_price_dollars": "0.0100",
                 "fee_cost": "0.0100", "ts": clock["t"]}]
    clock["t"] += 61
    pilot.step()
    p = store.pos[H + "-EPST"]
    assert p["status"] == "closed" and p["result"] == "sold"
    assert pilot.realized == pytest.approx(3 * 0.99 - 2.40 - 0.01)
    assert pilot.open_cost == pytest.approx(2.46)
    assert len([b for b in ex.placed if b["client_order_id"].startswith(mp.EXIT_PREFIX)]) == 1


def test_an_exit_never_sells_more_than_kalshi_holds_or_twice_inside_the_gap(world):
    pilot, store, ex, clock = world
    _held(store, ex, H + "-EPST", ask=0.02)
    ex.held[H + "-EPST"] = -2.0          # Kalshi shows less than our book: sell only that
    pilot.step()
    assert [b["count"] for b in ex.placed] == ["2.00"]
    clock["t"] += 61                      # no fill shows up yet: wait out the gap, do not resend
    pilot.step()
    assert len(ex.placed) == 1
    clock["t"] += run.EXIT_GAP_S
    pilot.step()
    assert len(ex.placed) == 2


def test_a_partial_exit_fill_leaves_the_rest_open_at_its_share_of_cost(world):
    pilot, store, ex, clock = world
    _held(store, ex, H + "-EPST", ask=0.01, cost=2.40)
    pilot.step()
    ex.fills = [{"fill_id": "x1", "order_id": "o1", "count_fp": "1.00", "yes_price_dollars": "0.0100",
                 "fee_cost": "0.0000", "ts": clock["t"]}]
    pilot.step()
    p = store.pos[H + "-EPST"]
    assert p["status"] == "open" and p["qty"] == 2 and p["cost"] == pytest.approx(1.60)
    assert pilot.realized == pytest.approx(0.99 - 0.80)


def test_a_decided_winner_exits_while_the_event_is_still_live(world):
    pilot, store, ex, clock = world
    store.events[H] = clock["t"] - 60
    pilot = run.Pilot(None, pilot.api, 1, {"KXHILLARYMENTION"}, now=lambda: clock["t"], log=lambda m: None)
    _held(store, ex, H + "-EPST", ask=0.01)
    pilot.step()
    exits = [b for b in ex.placed if b["client_order_id"].startswith(mp.EXIT_PREFIX)]
    assert [(b["ticker"], b["price"]) for b in exits] == [(H + "-EPST", "0.0100")]


def test_a_said_word_is_closed_at_the_yes_ask(world):
    pilot, store, ex, clock = world
    _held(store, ex, H + "-IMPE", ask=0.99, cost=2.46)
    ex.results[H + "-IMPE"]["yes_bid_dollars"] = "0.9800"
    pilot.step()
    exits = [b for b in ex.placed if b["client_order_id"].startswith(mp.EXIT_PREFIX)]
    assert len(exits) == 1
    b = exits[0]
    assert b["side"] == "bid" and b["price"] == "0.9900" and b["count"] == "3.00"
    assert b["time_in_force"] == "immediate_or_cancel"


def test_at_most_three_exits_a_minute(world):
    pilot, store, ex, clock = world
    for i, w in enumerate(["EPST", "BIDE", "OBAM", "TRUM"]):
        _held(store, ex, H + "-" + w, ask=0.01, pid=i + 1)
    pilot.step()
    assert len(ex.placed) == 3
    clock["t"] += 61
    pilot.step()
    assert len(ex.placed) == 4 and len({b["ticker"] for b in ex.placed}) == 4


def test_an_exit_sells_a_fractional_holding_in_full(world):
    pilot, store, ex, clock = world
    _held(store, ex, H + "-EPST", qty=2.75, cost=2.06, ask=0.01)   # Kalshi filled 2.75 of an ask for 3
    pilot.step()
    assert [b["count"] for b in ex.placed] == ["2.75"]


def test_a_refused_fractional_exit_falls_back_to_whole_contracts_then_waits_the_gap(world):
    pilot, store, ex, clock = world
    _held(store, ex, H + "-EPST", qty=2.75, cost=2.06, ask=0.01)
    ex.refuse = lambda body: not body["count"].endswith(".00")
    pilot.step()
    assert [b["count"] for b in ex.placed] == ["2.00"]
    store.pos[H + "-EPST"]["qty"] = 0.75     # the whole contracts sold; only a fraction is left
    ex.held[H + "-EPST"] = -0.75
    clock["t"] += run.EXIT_GAP_S + 1
    pilot.step()                              # 0.75 refused, nothing whole to fall back to
    assert len(ex.placed) == 1
    clock["t"] += 61
    pilot.step()                              # a refusal waits the gap too, not one retry a minute
    assert len(ex.placed) == 1


def _thin(ex, tk, bid=0.0, ask=0.13, last=0.01):
    ex.results[tk] = {"status": "active", "yes_bid_dollars": "%.4f" % bid, "yes_ask_dollars": "%.4f" % ask,
                      "last_price_dollars": "%.4f" % last}


def test_should_bid_exit_only_on_a_dead_looking_word():
    assert mp.should_bid_exit(0.0, 0.01)
    assert mp.should_bid_exit(0.02, 0.05)
    assert not mp.should_bid_exit(0.0, 0.25)      # last print 25c: not decided
    assert not mp.should_bid_exit(0.09, 0.01)     # someone pays 9c for YES: not decided
    assert not mp.should_bid_exit(0.0, None)


def test_a_thin_dead_word_gets_a_resting_2c_bid_that_books_as_an_exit(world):
    pilot, store, ex, clock = world
    _held(store, ex, H + "-PROF", qty=2.97, cost=1.52)
    _thin(ex, H + "-PROF")                        # last 1c, but the cheapest YES offer is 13c
    pilot.step()
    exits = [b for b in ex.placed if b["client_order_id"].startswith(mp.EXIT_PREFIX)]
    assert len(exits) == 1
    b = exits[0]
    assert b["side"] == "bid" and b["price"] == "0.0200" and b["count"] == "2.97"
    assert b["post_only"] is True and b["time_in_force"] == "good_till_canceled"
    ex.fills = [{"fill_id": "x1", "order_id": "o1", "count_fp": "2.97", "yes_price_dollars": "0.0200",
                 "fee_cost": "0.0000", "ts": clock["t"]}]
    clock["t"] += 61
    pilot.step()
    p = store.pos[H + "-PROF"]
    assert p["status"] == "closed" and p["result"] == "sold"
    assert pilot.realized == pytest.approx(2.97 * 0.98 - 1.52)


def test_a_resting_exit_bid_is_cancelled_before_the_next_so_bids_never_stack(world):
    pilot, store, ex, clock = world
    _held(store, ex, H + "-PROF", qty=3.0, cost=1.52)
    _thin(ex, H + "-PROF")
    pilot.step()
    assert len(ex.placed) == 1 and not ex.cancelled
    clock["t"] += 61
    pilot.step()                                   # inside the gap: nothing new
    assert len(ex.placed) == 1
    clock["t"] += run.EXIT_GAP_S
    pilot.step()
    assert ex.cancelled == ["o1"] and len(ex.placed) == 2


def test_a_resting_exit_bid_goes_up_while_the_event_is_still_live(world):
    pilot, store, ex, clock = world
    store.events[H] = clock["t"] - 60
    pilot = run.Pilot(None, pilot.api, 1, {"KXHILLARYMENTION"}, now=lambda: clock["t"], log=lambda m: None)
    _held(store, ex, H + "-PROF", qty=3.0, cost=1.52)
    _thin(ex, H + "-PROF")
    pilot.step()
    exits = [b for b in ex.placed if b["client_order_id"].startswith(mp.EXIT_PREFIX)]
    assert len(exits) == 1 and exits[0]["post_only"] and exits[0]["price"] == "0.0200"


def test_no_exit_once_kalshi_stops_trading_the_market(world):
    pilot, store, ex, clock = world
    _held(store, ex, H + "-EPST", ask=0.01, status="closed")   # decided, waiting to settle: 409 market_inactive
    pilot.step()
    assert not [b for b in ex.placed if b["client_order_id"].startswith(mp.EXIT_PREFIX)]
