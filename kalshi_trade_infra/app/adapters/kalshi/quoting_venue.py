"""Kalshi as a QuotingVenue (trading_core.ports.venue): post-only resting orders for a market maker.

It sends REAL ORDERS through whatever ``api`` it is given. It never decides on its own to trade. The
runner that owns it holds the switches, and this class only says what the exchange did.

Wire form (the events/orders endpoint speaks only the YES leg; data_pipeline.architecture.execution_kernel
.order_wire_params owns the math):
- Buying YES at p rests a YES ``bid`` at p.
- Buying NO at p rests a YES ``ask`` at 1 - p.
Every order is good_till_canceled and post_only, so it can never take liquidity. A post that would
cross is refused by the exchange and simply not rested.

Fills are read from /portfolio/fills and matched to our orders by order_id. Each fill is booked once
(by fill_id), at our resting price on our own instrument, with the fee Kalshi charged. A post-only
order only ever fills at its own limit, so the price needs no leg translation.

``api`` needs three methods, the shape of app.mention_pilot_run.Api:
- ``place(body) -> dict`` (the order is in ``["order"]["order_id"]`` or ``["order_id"]``)
- ``cancel(order_id)``
- ``private(path, params) -> dict``
"""
from __future__ import annotations

import time
import uuid
from typing import Callable, Dict, List, Optional, Sequence

from data_pipeline.architecture.execution_kernel import order_wire_params
from trading_core.domain.instruments import InstrumentId
from trading_core.domain.orders import Fill, RestingOrder, Side
from trading_core.ports.venue import VenueCapabilities

ORDERS_PATH = "/trade-api/v2/portfolio/orders"
FILLS_PATH = "/trade-api/v2/portfolio/fills"
TICK = 0.01


def _num(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class KalshiQuotingVenue:
    """Our resting orders on Kalshi, tracked locally and reconciled with the exchange."""

    name = "kalshi"
    capabilities = VenueCapabilities(sends_real_orders=True)

    def __init__(self, api, client_prefix: str, log: Callable[[str], None] = print,
                 clock: Callable[[], float] = time.time) -> None:
        if not client_prefix:
            raise ValueError("client_prefix is required: it is how stray orders are found and cancelled")
        self.api, self.prefix, self.log, self.clock = api, client_prefix, log, clock
        self._orders: Dict[str, RestingOrder] = {}      # order_id -> what is still working
        self._known: Dict[str, RestingOrder] = {}       # every order we placed (fills can land after a cancel)
        self._seen_fills: set = set()
        self._fills: List[Fill] = []
        self.fill_min_ts = int(self.clock()) - 60
        self.refused = 0

    # --- QuotingVenue ----------------------------------------------------------------------------
    def post(self, instrument: InstrumentId, side: Side, price: float, qty: float, client_order_id: str) -> str:
        if side is not Side.BUY:
            raise ValueError("the maker only buys (a NO bid is the YES offer)")
        count = int(qty)
        cents = int(round(price / TICK))
        if count <= 0 or not 1 <= cents <= 99:
            return ""
        wire_side, wire_price = order_wire_params("buy", instrument.outcome, cents * TICK)
        coid = self.prefix + uuid.uuid4().hex[:24]
        body = {"ticker": instrument.symbol, "side": wire_side, "count": "%d.00" % count,
                "price": "%.4f" % round(wire_price, 4), "time_in_force": "good_till_canceled", "post_only": True,
                "self_trade_prevention_type": "taker_at_cross", "client_order_id": coid}
        try:
            r = self.api.place(body)
        except Exception as e:  # noqa: BLE001 - a refusal (would cross, cash) leaves nothing resting
            self.refused += 1
            self.log("post %s %s @%.2f x%d refused: %s" % (instrument.symbol, instrument.outcome, price, count,
                                                           str(e)[:200]))
            return ""
        oid = (r.get("order") or {}).get("order_id") or r.get("order_id")
        if not oid:
            self.log("post %s: no order id in reply" % instrument.symbol)
            return ""
        o = RestingOrder(oid, instrument, Side.BUY, cents * TICK, float(count), coid, self.clock())
        self._orders[oid] = self._known[oid] = o
        return oid

    def cancel(self, order_id: str) -> None:
        if not order_id:
            return
        try:
            self.api.cancel(order_id)
        except Exception as e:  # noqa: BLE001 - keep it in view as working; the next step retries
            self.log("cancel %s failed: %s" % (order_id[:12], str(e)[:160]))
            return
        self._orders.pop(order_id, None)

    def open_orders(self) -> Sequence[RestingOrder]:
        return [o for o in self._orders.values() if o.qty > 1e-9]

    def take_fills(self) -> Sequence[Fill]:
        self.sync_fills()
        out, self._fills = self._fills, []
        return out

    # --- exchange reads ----------------------------------------------------------------------------
    def sync_fills(self) -> None:
        """Book new fills on our orders (paging until a short page). Fills on other orders are ignored."""
        cursor, newest = None, self.fill_min_ts
        for _ in range(20):
            params = {"min_ts": self.fill_min_ts, "limit": 200, **({"cursor": cursor} if cursor else {})}
            page = self.api.private(FILLS_PATH, params) or {}
            for f in page.get("fills") or []:
                fid, oid = f.get("fill_id") or f.get("trade_id"), f.get("order_id")
                o = self._known.get(oid)
                if o is None or not fid or fid in self._seen_fills:
                    continue
                qty = _num(f.get("count_fp", f.get("count")))
                if qty <= 0:
                    continue
                self._seen_fills.add(fid)
                ts = _num(f.get("ts"), self.clock())
                newest = max(newest, int(ts))
                self._fills.append(Fill(o.instrument, Side.BUY, qty, o.price, fee=_num(f.get("fee_cost")), ts=ts,
                                        order_id=oid))
                left = self._orders.get(oid)
                if left is not None:
                    rest = left.qty - qty
                    if rest <= 1e-9:
                        self._orders.pop(oid, None)
                    else:
                        self._orders[oid] = RestingOrder(oid, left.instrument, left.side, left.price, rest,
                                                         left.client_order_id, left.placed_at)
            cursor = page.get("cursor")
            if not cursor or len(page.get("fills") or []) < 200:
                break
        # Re-read a minute of overlap each time: a fill stamped just before the last read can arrive late.
        self.fill_min_ts = max(self.fill_min_ts, newest - 60)

    def sync_orders(self) -> None:
        """Drop from view any order the exchange no longer shows resting (filled or cancelled there)."""
        page = self.api.private(ORDERS_PATH, {"status": "resting", "limit": 200}) or {}
        live = {o.get("order_id") for o in page.get("orders") or []}
        for oid in [k for k in self._orders if k not in live]:
            self._orders.pop(oid, None)

    def cancel_all(self) -> int:
        """Cancel every resting order with our client prefix, ours in view or strays from an earlier run."""
        n = 0
        page = self.api.private(ORDERS_PATH, {"status": "resting", "limit": 200}) or {}
        ids = {o["order_id"] for o in page.get("orders") or []
               if str(o.get("client_order_id") or "").startswith(self.prefix) and o.get("order_id")}
        for oid in ids | set(self._orders):
            try:
                self.api.cancel(oid)
                n += 1
            except Exception as e:  # noqa: BLE001
                self.log("cancel_all %s failed: %s" % (oid[:12], str(e)[:160]))
            self._orders.pop(oid, None)
        return n

    def resting_collateral(self) -> float:
        """Cash our working orders tie up: price x contracts (buying YES at p or NO at p costs p each)."""
        return sum(o.price * o.qty for o in self.open_orders())
