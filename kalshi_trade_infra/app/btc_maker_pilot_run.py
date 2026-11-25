"""Bitcoin maker pilot: the lab's confirmed KXBTC policy quoting live books (app.services.btc_maker_pilot).

Modes, chosen by switches in /opt/kalshi/.env.app (TAU_BTC_MAKER_PILOT_ENABLED=0 turns it off):
- **Dry run** (the default, when TAU_BTC_MAKER_PILOT_ENABLED is unset). Public reads only: Kalshi books and prints, and Coinbase spot. Quotes rest
  in a shadow venue that NEVER sends an order. A shadow order fills only when a public print trades
  strictly through its price (no queue credit), so dry-run fills undercount. The run shows the live
  plumbing and decisions working, at no cost.
- **LIVE.** Needs all three of TAU_BTC_MAKER_PILOT_ENABLED=1, TAU_BTC_MAKER_PILOT_DRY_RUN=0 and
  APP_LIVE_TRADING_ENABLED=1. REAL post-only orders go on the user's Kalshi cash (crypto trades on
  shard 2), through app.adapters.kalshi.quoting_venue. Before a live run quotes, live_preflight
  funds the crypto shard once (TAU_BTC_MAKER_PILOT_FUND_USD from a deposit on shard 0, never taking shard 0
  under TAU_BTC_MAKER_PILOT_KEEP_ON_SHARD0), records that shard's cash when the pilot first goes live,
  stops for good once it has fallen by the loss stop (an account-level check on Kalshi's own numbers), and
  rests then cancels one 1c bid to prove orders are accepted. Until all of that passes, a run only waits.

Each run ends 2 minutes after an hourly close. The policy stops quoting 20 minutes before each close,
so every position a run opened has settled when it ends, and the next run starts flat. A deploy is
picked up at the next run. A run cancels its orders when it starts and when it stops. A live run will
not start while Kalshi shows an open KXBTC position. Its P&L carries across runs in
s3://<datalake>/research-watch/btc-maker-pilot/state.json, and the loss stop there is permanent. The
report is in latest.json next to it, and every run's report (with its fills) is kept in
runs/<start>_<dry|live>.json there.

    python -m app.btc_maker_pilot_run
"""
from __future__ import annotations

import json
import os
import signal
import time
import uuid
from datetime import datetime, timezone
from collections import deque
from typing import Deque, Dict, List, Optional, Sequence

from app.services import btc_maker_pilot as bp
from trading_core.domain import InstrumentId
from trading_core.domain.orders import Fill, RestingOrder, Side
from trading_core.engine.quoting import QuotingLoop
from trading_core.ports.venue import VenueCapabilities

BUCKET = os.getenv("DATALAKE_BUCKET", "kalshi-trade-datalake-568386354218-us-east-1")
PREFIX = "research-watch/btc-maker-pilot/"
POLICY_RUN = "research-watch/maker/runs/20261115T1048_KXBTC.json"   # r5.1: forward + 72 h confirm passed
POLICY_NAME = "r5.1 r5.1_ultra_flow_close1200"
CLIENT_PREFIX = "tbm-"
COINBASE_TICKER = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"
MAX_ERRORS = 5
BALANCE_PATH = "/trade-api/v2/portfolio/balance"
TRANSFER_PATH = "/trade-api/v2/portfolio/intra_exchange_instance_transfer"
ORDERS_LIST_PATH = "/trade-api/v2/portfolio/orders"


class ShadowVenue:
    """A QuotingVenue that never sends anything. An order fills on a public print strictly through its price."""

    name = "kalshi-shadow"
    capabilities = VenueCapabilities(sends_real_orders=False)

    def __init__(self) -> None:
        self._orders: Dict[str, RestingOrder] = {}
        self._fills: List[Fill] = []
        self._seq = 0

    def post(self, instrument: InstrumentId, side: Side, price: float, qty: float, client_order_id: str) -> str:
        self._seq += 1
        oid = "s%d" % self._seq
        self._orders[oid] = RestingOrder(oid, instrument, side, price, float(int(qty)), client_order_id, time.time())
        return oid

    def cancel(self, order_id: str) -> None:
        self._orders.pop(order_id, None)

    def open_orders(self) -> Sequence[RestingOrder]:
        return list(self._orders.values())

    def take_fills(self) -> Sequence[Fill]:
        out, self._fills = self._fills, []
        return out

    def cancel_all(self) -> int:
        n = len(self._orders)
        self._orders.clear()
        return n

    def resting_collateral(self) -> float:
        return sum(o.price * o.qty for o in self._orders.values())

    def on_print(self, ticker: str, yes_price: float, count: float, taker_side: str, ts: float) -> None:
        """A YES-taker print at p fills NO bids above 1 - p. A NO-taker print fills YES bids above p."""
        for oid, o in list(self._orders.items()):
            if o.instrument.symbol != ticker or count <= 0:
                continue
            through = (o.instrument.outcome == "yes" and taker_side == "no" and yes_price < o.price - 1e-9) or \
                      (o.instrument.outcome == "no" and taker_side == "yes" and 1.0 - yes_price < o.price - 1e-9)
            if not through:
                continue
            q = min(o.qty, count)
            count -= q
            self._fills.append(Fill(o.instrument, Side.BUY, q, o.price, fee=0.0, ts=ts, order_id=oid))
            if o.qty - q <= 1e-9:
                self._orders.pop(oid, None)
            else:
                self._orders[oid] = RestingOrder(oid, o.instrument, o.side, o.price, o.qty - q, o.client_order_id,
                                                 o.placed_at)


class Pilot:
    def __init__(self, public_get, venue, policy: Dict, cfg, s3=None, spot_get=None, log=None) -> None:
        self.get, self.venue, self.policy, self.cfg = public_get, venue, policy, cfg
        self.s3, self.spot_get = s3, spot_get
        base = log or (lambda m: print("[btc-maker-pilot] %s" % m, flush=True))
        self.log_lines: Deque[str] = deque(maxlen=60)    # the box log is unreadable from outside: the report carries a tail

        def _log(m: str) -> None:
            self.log_lines.append("%s %s" % (datetime.now(timezone.utc).strftime("%H:%M:%S"), str(m)[:300]))
            base(m)
        self.log = _log
        self.probe_info: Dict = {}
        self.live = venue.capabilities.sends_real_orders
        self.instruments: Dict = {}
        self.loop = QuotingLoop(venue, self.instruments, cfg, strategy_id="btc-maker-pilot")
        self.spot = bp.SpotTracker()
        self.move_window = int(policy.get("move_window_s", 10))
        self.mids: Dict[str, float] = {}
        self.markets: List[bp.Market] = []
        self.markets_at = 0.0
        self.trade_min_ts = int(time.time())
        self.state = self.load_state()
        self.counts = {"steps": 0, "posts": 0, "cancels": 0, "fills": 0, "filled_qty": 0.0, "paused": 0,
                       "rotations": 0}
        self.fill_log: List[Dict] = []
        self.started = datetime.now(timezone.utc)
        self.peak_collateral = 0.0
        self.last_reason = ""
        self.stopped = bool(self.state.get("stopped"))
        self.cap = float(os.getenv("TAU_BTC_MAKER_PILOT_COLLATERAL_CAP", "20"))
        self.account: Dict = {}       # live: the crypto shard's cash at this run's start vs when the pilot went live
        self.waiting = ""             # live: why this run did not start quoting

    # --- state ------------------------------------------------------------------------------
    def load_state(self) -> Dict:
        if self.s3 is None:
            return {}
        try:
            return json.loads(self.s3.get_object(Bucket=BUCKET, Key=PREFIX + "state.json")["Body"].read())
        except Exception:  # noqa: BLE001 - first run
            return {}

    def save_state(self, pnl: float) -> None:
        if self.s3 is None:
            return
        key = "live" if self.live else "dry"
        st = dict(self.state)
        st["carried_%s" % key] = round(float(self.state.get("carried_%s" % key, 0.0)) + pnl, 4)
        st["stopped"] = self.stopped
        st["at"] = datetime.now(timezone.utc).isoformat()
        try:
            self.s3.put_object(Bucket=BUCKET, Key=PREFIX + "state.json", Body=json.dumps(st).encode())
            self.state = st
        except Exception as e:  # noqa: BLE001
            self.log("state not saved: %s" % str(e)[:120])

    def put_state(self) -> None:
        if self.s3 is not None:
            self.s3.put_object(Bucket=BUCKET, Key=PREFIX + "state.json", Body=json.dumps(self.state).encode())

    def wait(self, reason: str) -> bool:
        self.waiting = reason
        self.log("not quoting: %s" % reason)
        self.report()
        return False

    def live_preflight(self, api, loss_stop: float, fund_usd: float, keep_on_0: float, sleep=time.sleep) -> bool:
        """Before a live run quotes (the pilot is flat: every earlier position has settled). In order:
        fund the crypto shard once from a deposit on shard 0; record the shard's cash when the pilot first
        goes live; stop for good if that cash has since fallen by the loss stop; then rest and cancel one
        1c bid to prove orders are accepted (location attestation, shard cash). False = do not quote."""
        now = time.time()
        self.refresh_markets(now)
        shards = [m.shard for m in self.markets if m.shard is not None]
        shard = max(set(shards), key=shards.count) if shards else bp.CRYPTO_SHARD
        cash = bp.shard_cash(api.private(BALANCE_PATH) or {})
        plan = bp.funding_plan(cash, fund_usd, keep_on_0, shard, funded=bool(self.state.get("funded")))
        if plan.transfer > 0:
            body = {"amount": int(round(plan.transfer * 10000)), "source": "event_contract",
                    "destination": "event_contract", "source_exchange_shard": 0, "destination_exchange_shard": shard,
                    "client_transfer_id": str(uuid.uuid4())}
            r = api._post(TRANSFER_PATH, body, credentials=api.creds) or {}
            self.log("FUNDING: %s (transfer %s)" % (plan.reason, r.get("transfer_id")))
            self.state["funding_transfer"] = {"amount": plan.transfer, "to_shard": shard,
                                              "transfer_id": r.get("transfer_id"), "at": time.time()}
            self.put_state()
            sleep(5)
            cash = bp.shard_cash(api.private(BALANCE_PATH) or {})
            plan = bp.funding_plan(cash, fund_usd, keep_on_0, shard)
        if not plan.ready:
            return self.wait(plan.reason)
        have = cash.get(shard, 0.0)
        if not self.state.get("funded") or self.state.get("live_start_cash") is None:
            self.state.update(funded=True, live_start_cash=round(have, 2), live_since=time.time(), shard=shard)
            self.put_state()
            self.log("LIVE BASELINE: shard %d cash $%.2f" % (shard, have))
        start = float(self.state["live_start_cash"])
        self.account = {"shard": shard, "cash_at_run_start": round(have, 2), "live_start_cash": start,
                        "pnl_since_live": round(have - start, 2)}
        if bp.account_stopped(have, start, loss_stop):
            self.stopped = True
            self.state["stopped"] = True
            self.put_state()
            return self.wait("ACCOUNT LOSS STOP: shard %d cash $%.2f vs $%.2f when live began" % (shard, have, start))
        return self.probe(sleep)

    def probe(self, sleep=time.sleep) -> bool:
        """Rest one 1c YES bid for 1 contract and cancel it by id. Passing means Kalshi accepted the
        post and the cancel (a 404 on cancel = already gone). Whether the resting-orders listing showed
        it is recorded but not required: the first live run (11-25 01:02Z) never saw its probe there."""
        m = next((m for m in self.markets if (m.listed_mid or 0) >= 0.10), None)
        if m is None:
            return self.wait("no listed KXBTC contract to probe with")
        yid = InstrumentId("kalshi", m.ticker, "yes")
        oid = self.venue.post(yid, Side.BUY, 0.01, 1, "probe")
        if not oid:
            return self.wait("probe order refused (location attestation or shard cash?)")
        seen, listed = False, None
        for _ in range(3):
            try:
                page = self.venue.api.private(ORDERS_LIST_PATH, {"status": "resting", "limit": 200}) or {}
                ids = [o.get("order_id") for o in page.get("orders") or []]
                listed, seen = len(ids), oid in ids
            except Exception as e:  # noqa: BLE001 - a diagnostic only
                self.log("probe listing read failed: %s" % str(e)[:160])
            if seen:
                break
            sleep(1.0)
        self.venue.cancel(oid)
        cancelled = all(o.order_id != oid for o in self.venue.open_orders())
        status = self.order_status(oid, sleep) if cancelled else ""
        self.probe_info = {"ticker": m.ticker, "order_id": oid, "seen_in_listing": seen, "listing_count": listed,
                           "cancelled": cancelled, "status_after_cancel": status}
        self.log("probe %s" % json.dumps(self.probe_info))
        if not cancelled:
            self.venue.cancel_all()
            return self.wait("probe order accepted but its cancel failed")
        # The cancel call answering is not enough: on 11-25 every cancel answered 404 ("gone") while the
        # order kept resting on the crypto shard, and the "cancelled" probe filled 11 minutes later.
        if status != "canceled":
            self.venue.cancel_all()
            return self.wait("probe cancel not confirmed by Kalshi (order status %r)" % (status or "unread"))
        if not seen:
            self.log("the resting-orders listing did not show the probe; stray sweeps rely on saved order ids")
        self.waiting = ""
        return True

    def order_status(self, oid: str, sleep=time.sleep, tries: int = 5) -> str:
        """The order's status as Kalshi reads it back: 'canceled', 'executed' or 'resting'. Reads lag a
        write, so a 404 or a stale 'resting' is read again, a second apart."""
        status = ""
        for i in range(tries):
            try:
                o = (self.venue.api.private(ORDERS_LIST_PATH + "/" + oid) or {}).get("order") or {}
                status = str(o.get("status") or "")
            except Exception as e:  # noqa: BLE001 - a 404 just after the write is normal
                self.log("order %s read failed: %s" % (oid[:12], str(e)[:120]))
            if status in ("canceled", "executed"):
                return status
            if i < tries - 1:
                sleep(1.0)
        return status

    def save_open_ids(self) -> None:
        """Live order ids in state.json, so a run killed mid-hour leaves nothing the next run cannot cancel."""
        ids = sorted([o.order_id, o.instrument.symbol] for o in self.venue.open_orders())
        if ids != self.state.get("open_ids", []):
            self.state["open_ids"] = ids
            self.put_state()

    def cancel_saved(self) -> int:
        n = 0
        for item in self.state.get("open_ids") or []:
            oid, tk = (item, None) if isinstance(item, str) else (item[0], item[1])
            try:
                if tk:
                    self.venue.api.cancel(oid, ticker=tk)
                else:
                    self.venue.api.cancel(oid, shard=bp.CRYPTO_SHARD)
                n += 1
            except Exception as e:  # noqa: BLE001
                self.log("cancel saved %s failed: %s" % (oid[:12], str(e)[:160]))
        if self.state.get("open_ids"):
            self.state["open_ids"] = []
            self.put_state()
        return n

    def carried(self) -> float:
        return float(self.state.get("carried_%s" % ("live" if self.live else "dry"), 0.0))

    # --- inputs -----------------------------------------------------------------------------
    def read_spot(self, now: float) -> None:
        try:
            r = self.spot_get(COINBASE_TICKER) if self.spot_get else {}
            self.spot.add(now, float(r.get("price") or 0))
        except Exception as e:  # noqa: BLE001 - a missed second; pull_move_bp reads the last good ones
            self.log("spot read failed: %s" % str(e)[:120])

    def refresh_markets(self, now: float) -> None:
        if self.markets and now - self.markets_at < 60:
            return
        page = self.get("/markets", {"series_ticker": bp.SERIES, "status": "open", "limit": 1000})
        self.markets = bp.markets_from(page, now, float(self.cfg.max_secs_to_close or 3600))
        bp.instruments_for(self.markets, self.instruments)
        self.markets_at = now

    def read_prints(self, now: float) -> None:
        """Public prints since the last step: the shadow venue's fills (live fills come from the venue)."""
        if self.live:
            return
        page = self.get("/markets/trades", {"min_ts": self.trade_min_ts, "limit": 1000})
        newest = self.trade_min_ts
        for t in page.get("trades") or []:
            tk = t.get("ticker") or ""
            if not tk.startswith(bp.SERIES + "-"):
                continue
            ts = bp._ts(t.get("created_time")) or now
            newest = max(newest, int(ts))
            self.venue.on_print(tk, float(t.get("yes_price_dollars") or 0), float(t.get("count_fp") or 0),
                                t.get("taker_side") or "", ts)
        self.trade_min_ts = newest

    # --- the step -----------------------------------------------------------------------------
    def step(self) -> None:
        now = time.time()
        self.counts["steps"] += 1
        self.read_spot(now)
        self.refresh_markets(now)
        self.read_prints(now)
        move = self.spot.move_bp(self.move_window)
        spot, rv = self.spot.last(), self.spot.rv()
        quotes, fair, stc = {}, {}, {}
        lo, hi = self.cfg.mid_band
        held_now = {iid.symbol for iid in self.loop.book.open_positions()}
        # Books only for what can be quoted: held contracts plus, by the minute-old listing, the in-band ones
        # nearest 50c, a few more than the collateral budget quotes. KXBTC lists ~190 contracts an hour.
        n_books = int(self.cap // max(self.cfg.quote_size, 1e-9)) + 4
        cands = sorted((m for m in self.markets if m.ticker not in held_now and
                        (m.listed_mid is None or lo - 0.10 <= m.listed_mid <= hi + 0.10)),
                       key=lambda m: 1.0 if m.listed_mid is None else abs(m.listed_mid - 0.5))
        want = held_now | {m.ticker for m in cands[:n_books]}
        for m in self.markets:
            secs = m.close_ts - now
            if secs <= 0 or m.ticker not in want:
                continue
            q = bp.quote_from_book(m.ticker, self.get("/markets/%s/orderbook" % m.ticker, None), now, move)
            if q is None:
                continue
            yid = q.instrument
            quotes[yid] = q
            self.mids[m.ticker] = q.mid
            stc[yid] = secs
            f = bp.tail_fair(spot, m.floor, m.cap, rv, secs)
            if f is not None:
                fair[yid] = f
        pnl = bp.pilot_pnl(self.loop.book, self.mids, self.carried())
        collateral = self.venue.resting_collateral() + bp.position_collateral(self.loop.book)
        self.peak_collateral = max(self.peak_collateral, collateral)
        v = bp.guard(pnl, collateral, float(os.getenv("TAU_BTC_MAKER_PILOT_LOSS_STOP", "15")), self.cap)
        breach = self.inventory_breach()
        if breach:
            self.log("INVENTORY BREACH: %s; orders are stacking past the size cap. Cancelling everything and "
                     "stopping for good" % breach)
            self.venue.cancel_all()
            self.stopped = True
            return
        if v.stop:
            self.log("LOSS STOP: %s; cancelling everything and stopping for good" % v.reason)
            self.venue.cancel_all()
            self.stopped = True
            return
        if v.pause:
            if v.reason != self.last_reason:
                self.log("pause: %s" % v.reason)
            self.last_reason = v.reason
            self.counts["paused"] += 1
            for o in self.venue.open_orders():
                self.venue.cancel(o.order_id)
            self.book_only()
            return
        self.last_reason = ""
        held = {iid.symbol for iid in self.loop.book.open_positions()}
        budget = self.cap - bp.position_collateral(self.loop.book)
        working = {o.order_id: o.instrument.symbol for o in self.venue.open_orders()}
        universe = bp.pick_universe(quotes, held, budget, self.cfg.quote_size, quoted=set(working.values()))
        out = self.loop.step(now, quotes, fair=fair, secs_to_close=stc, universe=universe)
        self.counts["posts"] += len(out.posts)
        self.counts["cancels"] += len(out.cancels)
        in_u = {y.symbol for y in universe}
        # Cancels that rotate a contract out of the universe, as opposed to repricing one still quoted.
        self.counts["rotations"] += sum(1 for oid in out.cancels if working.get(oid) not in in_u)
        self.note_fills(out.fills)

    def inventory_breach(self) -> str:
        """A contract held past 2x max_inventory. The quoting loop stops adding at max_inventory and one
        resting quote can overshoot it by a quote, so anything beyond twice the cap means orders we
        believe cancelled are still resting (11-25: 60 NO on one contract at max_inventory 3)."""
        limit = 2 * max(self.cfg.max_inventory, self.cfg.quote_size) + 1e-9
        for iid, pos in self.loop.book.open_positions().items():
            if abs(pos.qty) > limit:
                return "%s %s x%.0f over %.0f" % (iid.symbol, iid.outcome, abs(pos.qty), limit)
        return ""

    def book_only(self) -> None:
        self.note_fills(self.loop.book_fills())

    def note_fills(self, fills) -> None:
        for f in fills:
            self.counts["fills"] += 1
            self.counts["filled_qty"] += f.qty
            mid = self.mids.get(f.instrument.symbol)
            self.fill_log.append({"ts": f.ts, "ticker": f.instrument.symbol, "side": f.instrument.outcome,
                                  "qty": f.qty, "price": f.price, "fee": f.fee, "yes_mid": mid})
            self.log("FILL %s %s x%.0f @%.2f" % (f.instrument.symbol, f.instrument.outcome, f.qty, f.price))

    def report(self) -> Dict:
        pnl_run = bp.pilot_pnl(self.loop.book, self.mids, 0.0)
        rep = {"generated_at": datetime.now(timezone.utc).isoformat(), "mode": "live" if self.live else "dry",
               "policy": POLICY_NAME, "max_inventory": self.cfg.max_inventory, "quote_size": self.cfg.quote_size,
               "counts": self.counts, "run_pnl_at_mid": round(pnl_run, 2), "carried": round(self.carried(), 2),
               "peak_collateral": round(self.peak_collateral, 2), "resting": len(self.venue.open_orders()),
               "markets": len(self.markets), "stopped": self.stopped, "spot": self.spot.last(),
               "started": self.started.isoformat(), "account": self.account, "waiting": self.waiting,
               "probe": self.probe_info, "log": list(self.log_lines)[-40:],
               "fills": self.fill_log[-200:]}
        if self.s3 is not None:
            body = json.dumps(rep, default=str).encode()
            # latest.json is the current run; runs/<start>.json keeps every run's fills for later readouts.
            for key in ("latest.json",) if self.waiting else ("latest.json", self.run_key()):
                try:
                    self.s3.put_object(Bucket=BUCKET, Key=PREFIX + key, Body=body, ContentType="application/json")
                except Exception as e:  # noqa: BLE001 - a readout, never a reason to stop
                    self.log("report %s not written: %s" % (key, str(e)[:120]))
        return rep

    def run_key(self) -> str:
        return "runs/%s_%s.json" % (self.started.strftime("%Y%m%dT%H%M%SZ"), "live" if self.live else "dry")


def run_deadline(now: float, after_close_s: float = 120.0) -> float:
    """Runs end just after an hourly close: the policy stops quoting 20 min before each close, so every
    position a run opened has settled when it ends, and the next run starts flat."""
    end = (int(now // 3600) + 1) * 3600 + after_close_s
    return end if end - now >= 600 else end + 3600


def held_btc(api) -> bool:
    page = api.private("/trade-api/v2/portfolio/positions", {"limit": 1000}) or {}
    return any(str(p.get("ticker") or "").startswith(bp.SERIES + "-") and
               abs(float(p.get("position_fp") or p.get("position") or 0)) > 1e-9
               for p in page.get("market_positions") or [])


def load_policy(s3) -> Dict:
    run = json.loads(s3.get_object(Bucket=BUCKET, Key=POLICY_RUN)["Body"].read())
    return run["results"][POLICY_NAME]["policy"]


def main() -> None:  # pragma: no cover - wiring
    import boto3
    import requests
    from data_pipeline import common

    # Unset = dry run (public reads only, never an order); 0 = off; 1 = may go live with the other two switches.
    if os.getenv("TAU_BTC_MAKER_PILOT_ENABLED", "dry") == "0":
        print("[btc-maker-pilot] off (TAU_BTC_MAKER_PILOT_ENABLED=0)", flush=True)
        time.sleep(float(os.getenv("TAU_BTC_MAKER_PILOT_OFF_SLEEP_S", "300")))
        return
    live = os.getenv("TAU_BTC_MAKER_PILOT_ENABLED") == "1" and os.getenv("TAU_BTC_MAKER_PILOT_DRY_RUN", "1") == "0" \
        and os.getenv("APP_LIVE_TRADING_ENABLED") == "1"
    s3 = boto3.client("s3")
    policy = load_policy(s3)
    cfg = bp.pilot_config(policy, float(os.getenv("TAU_BTC_MAKER_PILOT_MAX_INVENTORY", "3")),
                          float(os.getenv("TAU_BTC_MAKER_PILOT_QUOTE_SIZE", "3")))
    if live:
        from app.adapters.db.kalshi_keys_repository import get_kalshi_key
        from app.adapters.kalshi import kalshi_account
        from app.adapters.kalshi.quoting_venue import KalshiQuotingVenue
        from app.mention_pilot_run import Api
        user_id = int(os.getenv("TAU_BTC_MAKER_PILOT_USER", "1"))
        key = get_kalshi_key(common.get_db_conn().cursor(), user_id)
        if key is None:
            print("[btc-maker-pilot] user %d has no live Kalshi key" % user_id, flush=True)
            time.sleep(300)
            return
        venue = KalshiQuotingVenue(Api(kalshi_account.credentials_from_row(user_id, key)), CLIENT_PREFIX)
    else:
        venue = ShadowVenue()

    def spot_get(url):
        return requests.get(url, timeout=3).json()

    pilot = Pilot(common.api_get, venue, policy, cfg, s3=s3, spot_get=spot_get)
    venue.log = pilot.log                                   # refusals and cancel failures reach the report too
    stop = {"now": False}

    def _term(_sig, _frm):
        stop["now"] = True
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    pilot.log("start: %s mode, max_inventory %.0f, quote_size %.0f, carried $%.2f"
              % ("LIVE" if live else "dry-run", cfg.max_inventory, cfg.quote_size, pilot.carried()))
    pilot.venue.cancel_all()
    if live:
        pilot.cancel_saved()
    if pilot.stopped:
        pilot.log("stopped for good by the loss stop; delete state.json's stopped flag to resume")
        time.sleep(600)
        return
    if live and held_btc(venue.api):
        pilot.log("Kalshi shows open KXBTC positions from an earlier run; waiting for them to settle")
        time.sleep(300)
        return
    if live and not pilot.live_preflight(venue.api, float(os.getenv("TAU_BTC_MAKER_PILOT_LOSS_STOP", "15")),
                                         float(os.getenv("TAU_BTC_MAKER_PILOT_FUND_USD", "0")),
                                         float(os.getenv("TAU_BTC_MAKER_PILOT_KEEP_ON_SHARD0", "40"))):
        time.sleep(120)
        return
    poll_s = float(os.getenv("TAU_BTC_MAKER_PILOT_POLL_S", "1" if live else "2"))
    deadline, errors, last_report, last_ids = run_deadline(time.time()), 0, 0.0, 0.0
    try:
        while not stop["now"] and not pilot.stopped and time.time() < deadline:
            t0 = time.time()
            try:
                pilot.step()
                errors = 0
            except Exception as e:  # noqa: BLE001 - count it; too many in a row stops the run
                errors += 1
                pilot.log("step error %d/%d: %r" % (errors, MAX_ERRORS, e))
                if errors >= MAX_ERRORS:
                    break
            if live and time.time() - last_ids >= 10:
                last_ids = time.time()
                pilot.save_open_ids()
            if time.time() - last_report >= 60:
                last_report = time.time()
                pilot.report()
            time.sleep(max(0.0, poll_s - (time.time() - t0)))
    finally:
        pilot.venue.cancel_all()
        if not pilot.venue.open_orders():
            pilot.state["open_ids"] = []
        pilot.book_only()
        pilot.save_state(bp.pilot_pnl(pilot.loop.book, pilot.mids, 0.0))
        pilot.report()
        pilot.log("stop: %s" % json.dumps(pilot.counts))


if __name__ == "__main__":  # pragma: no cover
    main()
