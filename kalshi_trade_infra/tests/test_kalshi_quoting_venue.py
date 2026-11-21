"""Kalshi as a QuotingVenue: post-only GTC wire form, fills matched by order id, strays cancelled."""
from app.adapters.kalshi.quoting_venue import KalshiQuotingVenue
from trading_core.domain.instruments import InstrumentId
from trading_core.domain.orders import Side
from trading_core.ports.venue import QuotingVenue

YES = InstrumentId("kalshi", "KXBTC-26NOV2016-B97250", "yes")
NO = InstrumentId("kalshi", "KXBTC-26NOV2016-B97250", "no")


class FakeApi:
    def __init__(self):
        self.placed, self.cancelled, self.fills, self.resting = [], [], [], []
        self.refuse = False

    def place(self, body):
        if self.refuse:
            raise RuntimeError("POST failed 400: post only cross")
        self.placed.append(body)
        oid = "o%d" % len(self.placed)
        self.resting.append({"order_id": oid, "client_order_id": body["client_order_id"]})
        return {"order": {"order_id": oid}}

    def cancel(self, oid):
        self.cancelled.append(oid)
        self.resting = [o for o in self.resting if o["order_id"] != oid]

    def private(self, path, params=None):
        if path.endswith("/fills"):
            return {"fills": [f for f in self.fills if f["ts"] >= params["min_ts"]]}
        return {"orders": list(self.resting)}


def _venue(api):
    return KalshiQuotingVenue(api, "tmk-", log=lambda m: None, clock=lambda: 1000.0)


def test_it_is_a_quoting_venue_and_rests_post_only_gtc_orders_on_the_yes_leg():
    api = FakeApi()
    v = _venue(api)
    assert isinstance(v, QuotingVenue) and v.capabilities.sends_real_orders
    a = v.post(YES, Side.BUY, 0.42, 5, "x")
    b = v.post(NO, Side.BUY, 0.55, 3, "y")
    yes_body, no_body = api.placed
    assert yes_body["side"] == "bid" and yes_body["price"] == "0.4200" and yes_body["count"] == "5.00"
    assert no_body["side"] == "ask" and no_body["price"] == "0.4500"          # buying NO at 55c = YES ask 45c
    for body in api.placed:
        assert body["post_only"] is True and body["time_in_force"] == "good_till_canceled"
        assert body["client_order_id"].startswith("tmk-")
    assert {o.order_id for o in v.open_orders()} == {a, b} and v.resting_collateral() == 0.42 * 5 + 0.55 * 3


def test_a_refused_post_rests_nothing_and_bad_sizes_are_never_sent():
    api = FakeApi()
    v = _venue(api)
    api.refuse = True
    assert v.post(YES, Side.BUY, 0.42, 5, "x") == "" and v.open_orders() == [] and v.refused == 1
    api.refuse = False
    assert v.post(YES, Side.BUY, 0.42, 0.4, "x") == "" and v.post(YES, Side.BUY, 1.0, 5, "x") == ""
    assert api.placed == []


def test_fills_book_once_at_our_price_and_shrink_the_order():
    api = FakeApi()
    v = _venue(api)
    oid = v.post(NO, Side.BUY, 0.55, 5, "y")
    api.fills = [{"fill_id": "f1", "order_id": oid, "count_fp": "2.00", "yes_price_dollars": "0.45",
                  "fee_cost": "0.01", "ts": 1001},
                 {"fill_id": "fx", "order_id": "someone-else", "count_fp": "9.00", "ts": 1001}]
    fills = v.take_fills()
    assert len(fills) == 1 and fills[0].instrument == NO and fills[0].price == 0.55 and fills[0].qty == 2.0
    assert fills[0].fee == 0.01 and v.open_orders()[0].qty == 3.0
    assert v.take_fills() == []                                                # the same fill is never booked twice
    api.fills.append({"fill_id": "f2", "order_id": oid, "count_fp": "3.00", "ts": 1002})
    assert [f.qty for f in v.take_fills()] == [3.0] and v.open_orders() == []


def test_a_fill_after_our_cancel_still_books_and_cancel_all_sweeps_strays():
    api = FakeApi()
    v = _venue(api)
    oid = v.post(YES, Side.BUY, 0.30, 4, "x")
    v.cancel(oid)
    assert v.open_orders() == [] and api.cancelled == [oid]
    api.fills = [{"fill_id": "f1", "order_id": oid, "count_fp": "1.00", "ts": 1001}]
    assert [f.qty for f in v.take_fills()] == [1.0]
    api.resting += [{"order_id": "stray", "client_order_id": "tmk-old"}, {"order_id": "theirs", "client_order_id": "mp-1"}]
    v.post(YES, Side.BUY, 0.31, 4, "x")
    assert v.cancel_all() == 2 and "theirs" not in api.cancelled and v.open_orders() == []


def test_sync_orders_drops_what_the_exchange_no_longer_rests():
    api = FakeApi()
    v = _venue(api)
    a = v.post(YES, Side.BUY, 0.30, 4, "x")
    v.post(YES, Side.BUY, 0.31, 4, "x")
    api.resting = [o for o in api.resting if o["order_id"] != a]
    v.sync_orders()
    assert [o.price for o in v.open_orders()] == [0.31]
