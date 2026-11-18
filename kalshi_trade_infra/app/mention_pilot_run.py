"""Live pilot of the in-play mention YES seller on the user's own Kalshi cash (app.services.mention_pilot).

REAL ORDERS. Off unless TAU_MENTION_PILOT_ENABLED=1 and APP_LIVE_TRADING_ENABLED=1. Runs on the WEB box as
tau-mention-pilot.service, started by its timer; each run lasts at most TAU_MENTION_PILOT_RUN_S (55 min) so
a deploy's new code is picked up, and cancels every resting pilot order when it starts and when it stops.

    python -m app.mention_pilot_run

Each step (about every 2s):
1. Read the public trade feed since the last step. Mention prints set event triggers, mark markets whose
   word was probably said, and credit the replay's fills to our resting asks (the queue measure).
2. Book our own fills from /portfolio/fills into app_mention_pilot_* (the reconcilers count them).
3. For every live event, read its markets' top of book in one call and keep one post-only YES ask in
   each in-band market, cancelling on a said word, a closed window, an out-of-band price or an undercut.
Every minute it reads the mention shard's cash, settles positions Kalshi has settled, checks the loss
stop and writes s3://<datalake>/research-watch/mention-pilot/latest.json.
"""
from __future__ import annotations

import json
import math
import os
import signal
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Set

from app.adapters.kalshi import mention_pilot_repository as repo
from app.services import mention_pilot as mp
from app.trading.settlement import market_settlement, settlement_terms

BUCKET = os.getenv("DATALAKE_BUCKET", "kalshi-trade-datalake-568386354218-us-east-1")
REPORT_KEY = "research-watch/mention-pilot/latest.json"
SHARD = 0                       # mention markets trade on exchange index 0
ORDERS = "/trade-api/v2/portfolio/events/orders"
MAX_ERRORS = 5


def _ts(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def event_of(ticker: str) -> str:
    """KXTRUMPMENTION-26NOV14-TARIFF -> KXTRUMPMENTION-26NOV14."""
    return ticker.rsplit("-", 1)[0]


class Api:
    """The Kalshi calls the pilot makes. Public reads, the user's signed reads and writes; 429s back off."""

    def __init__(self, creds, get=None, get_auth=None, post_auth=None, delete_auth=None, sleep=time.sleep):
        from data_pipeline import common
        self.creds = creds
        self._get = get or common.api_get
        self._get_auth = get_auth or common.api_get_auth
        self._post = post_auth or common.api_post_auth
        self._delete = delete_auth or common.api_delete_auth
        self._sleep = sleep

    def _retry(self, fn: Callable[[], Dict]) -> Dict:
        for i in range(6):
            try:
                return fn() or {}
            except RuntimeError as e:
                if " 429" not in str(e) and "too_many" not in str(e):
                    raise
                self._sleep(0.5 * (i + 1))
        return fn() or {}

    def public(self, path: str, params: Optional[Dict] = None) -> Dict:
        return self._retry(lambda: self._get(path, params))

    def private(self, path: str, params: Optional[Dict] = None) -> Dict:
        return self._retry(lambda: self._get_auth(path, params, credentials=self.creds))

    def place(self, body: Dict) -> Dict:
        return self._post(ORDERS, body, credentials=self.creds)

    def cancel(self, order_id: str) -> bool:
        """True once the order is gone (cancelled now, or already filled/cancelled). A 404 means the
        order is no longer resting. (The old /portfolio/orders/{id} fallback answers 410 deprecated.)"""
        try:
            self._delete(ORDERS + "/" + order_id, credentials=self.creds)
        except RuntimeError as e:
            if " 404" not in str(e):
                raise
        return True


EXIT_GAP_S = 300.0


class Pilot:
    def __init__(self, cur, api: Api, user_id: int, series: Set[str], now: Callable[[], float] = time.time,
                 log: Callable[[str], None] = lambda m: print("[mention-pilot] " + m, flush=True)):
        self.cur, self.api, self.user_id, self.series, self.now, self.log = cur, api, user_id, series, now, log
        t = now()
        self.triggers = mp.Triggers(repo.triggers_since(cur, t - mp.HOLD_S))
        self.resting: Dict[str, Dict] = {}
        # Words that printed at CANCEL_AT or above: never quoted again. Kept in the DB, so a run that
        # starts mid-event (every 55 min) does not forget them and quote a word that was just said.
        self.said: Set[str] = set(repo.said_since(cur, t - mp.HOLD_S - 3600))
        self.filled = repo.filled_by_ticker(cur)
        self.last_trade_ts = t - 5
        self.seen_trades: Dict[str, float] = {}
        self.fill_min_ts = int(t) - 3600
        self.last_quote: Dict[str, float] = {}
        self.shard_cash: Optional[float] = None
        self.realized = repo.realized_pnl(cur, user_id)
        self.open_cost = self._open_cost()
        # Off by default: the loss stop counts open positions at full cost. TAU_MENTION_PILOT_RISK_AT_MARK=1
        # counts them at what they would lose if sold now (mp.position_risk), refreshed each minute.
        self.risk_at_mark = os.getenv("TAU_MENTION_PILOT_RISK_AT_MARK", "0") == "1"
        self.exit_refused: Dict[int, float] = {}
        self.open_risk: Optional[float] = None
        self.minute_at = 0.0
        self.stopped = False

    # -- money ------------------------------------------------------------------------------------

    def _open_cost(self) -> float:
        return sum(float(p["cost"]) + float(p["fees"]) for p in repo.open_positions(self.cur, self.user_id))

    def resting_collateral(self) -> float:
        return sum(mp.collateral(float(o["qty"]) - float(o["filled"]), float(o["yes_price"]))
                   for o in self.resting.values())

    def room(self) -> float:
        return mp.room(self.realized, self.open_cost, self.resting_collateral(), self.shard_cash,
                       self.open_risk if self.risk_at_mark else None)

    def read_cash(self) -> None:
        try:
            b = self.api.private("/trade-api/v2/portfolio/balance")
            shards = {int(x.get("exchange_index", -1)): float(x.get("balance")) for x in b.get("balance_breakdown") or []}
            self.shard_cash = shards.get(SHARD)
        except Exception as e:  # noqa: BLE001 - unreadable cash = no new orders this minute
            self.shard_cash = None
            self.log("balance unreadable: %s" % str(e)[:160])

    def exit_position(self, p: Dict, yes_ask: float, resting: bool = False) -> bool:
        """Buy YES for what is left of a decided NO position: immediate-or-cancel at the ask, or (resting)
        a post-only bid at EXIT_AT when the book offers no YES that cheap. Never more than Kalshi says we
        hold (so it can only close NO, never open YES), at most one exit per position every EXIT_GAP_S,
        and any earlier exit order for the position is cancelled first, so resting bids never stack."""
        now = self.now()
        mine = {oid: x for oid, x in repo.exits(self.cur).items() if int(x["position_id"]) == int(p["id"])}
        if any(now - float(x["placed_ts"]) < EXIT_GAP_S for x in mine.values()):
            return False
        for oid in mine:
            try:
                self.api.cancel(oid)   # 404 (already filled or gone) counts as done
            except Exception as e:  # noqa: BLE001 - an earlier bid may still rest: do not add another
                self.log("exit %s: earlier exit %s not cancelled: %s" % (p["ticker"], oid[:8], str(e)[:160]))
                return True
        if now - self.exit_refused.get(int(p["id"]), -1e18) < EXIT_GAP_S:
            return False   # Kalshi refused this one recently: same gap as a placed exit
        try:
            page = self.api.private("/trade-api/v2/portfolio/positions", {"ticker": p["ticker"], "limit": 10})
        except Exception as e:  # noqa: BLE001 - try again next minute
            self.log("exit %s: positions unreadable: %s" % (p["ticker"], str(e)[:160]))
            return False
        held_no = 0.0
        for row in page.get("market_positions") or []:
            if row.get("ticker") == p["ticker"]:
                held_no = max(0.0, -float(row.get("position_fp") or row.get("position") or 0))
        # Kalshi fills in hundredths of a contract, so sell the exact held amount (2.75 -> 2.75, not 2);
        # if a fractional count is refused, fall back to the whole contracts.
        qty = math.floor(min(float(p["qty"]), held_no) * 100 + 1e-6) / 100
        tries = [qty] + ([float(int(qty))] if int(qty) >= 1 and int(qty) != qty else [])
        if qty <= 0:
            return False
        for q in tries:
            body = {"ticker": p["ticker"], "side": "bid", "count": "%.2f" % q, "price": "%.4f" % yes_ask,
                    "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross",
                    "client_order_id": mp.EXIT_PREFIX + uuid.uuid4().hex[:24]}
            if resting:
                body.update({"time_in_force": "good_till_canceled", "post_only": True})
            try:
                r = self.api.place(body)
            except Exception as e:  # noqa: BLE001 - try again next minute
                self.log("exit %s x%.2f refused: %s" % (p["ticker"], q, str(e)[:200]))
                continue
            oid = r.get("order_id") or (r.get("order") or {}).get("order_id")
            if oid:
                repo.add_exit(self.cur, oid, p["id"], p["ticker"], yes_ask, q, now)
                self.log("EXIT %s x%.2f %s YES @%.2f (decided, not said)"
                         % (p["ticker"], q, "bid resting" if resting else "buy", yes_ask))
            return True
        self.exit_refused[int(p["id"])] = now
        return True   # counts as this minute's exit: a bad order format fails once, not once per word

    def settle(self) -> int:
        n, risk, exited = 0, 0.0, False
        now = self.now()
        for p in repo.open_positions(self.cur, self.user_id):
            cost = float(p["cost"]) + float(p["fees"])
            try:
                m = (self.api.public("/markets/" + p["ticker"]) or {}).get("market") or {}
            except Exception:  # noqa: BLE001 - try again next minute
                risk += cost
                continue
            result = market_settlement(m)
            if result is None:
                ask = m.get("yes_ask_dollars")
                ask = float(ask) if ask not in (None, "") else None
                risk += mp.position_risk(float(p["qty"]), cost, ask)
                trig = self.triggers.at.get(p.get("event_ticker") or event_of(p["ticker"]))
                closed = trig is None or now >= trig + mp.HOLD_S
                if (m.get("status") or "active") != "active":
                    continue   # trading halted until Kalshi settles (409 market_inactive): nothing to sell into
                if not exited and mp.should_exit(ask, closed):
                    exited = self.exit_position(p, ask)   # one exit a minute: a bad one costs one word
                elif not exited and mp.should_bid_exit(_f(m.get("yes_bid_dollars")), _f(m.get("last_price_dollars")),
                                                       closed):
                    exited = self.exit_position(p, mp.EXIT_AT, resting=True)
                continue
            qty = float(p["qty"])
            _, _, pnl = settlement_terms(result, "no", qty, float(p["cost"]) / qty, float(p["fees"]))
            if repo.settle_position(self.cur, p["id"], str(result), pnl):
                n += 1
                self.log("settled %s %s qty %.0f pnl %+.2f" % (p["ticker"], result, qty, pnl))
        self.realized = repo.realized_pnl(self.cur, self.user_id)
        self.open_cost = self._open_cost()
        self.open_risk = risk
        return n

    # -- orders -----------------------------------------------------------------------------------

    def load_resting(self) -> None:
        self.resting = {o["order_id"]: dict(o) for o in repo.orders(self.cur, "resting")}

    def cancel(self, order_id: str, why: str) -> None:
        o = self.resting.get(order_id)
        try:
            self.api.cancel(order_id)
        except Exception as e:  # noqa: BLE001 - keep it resting in our view; the next step retries
            self.log("cancel %s failed (%s): %s" % (order_id, why, str(e)[:160]))
            return
        repo.close_order(self.cur, order_id, "cancelled")
        self.resting.pop(order_id, None)
        if o:
            self.log("cancel %s %s @%.2f (%s)" % (o["ticker"], order_id[:8], float(o["yes_price"]), why))

    def cancel_all(self, why: str) -> None:
        """Every resting pilot order: ours in the DB, and any on the exchange with our client prefix."""
        self.load_resting()
        for oid in list(self.resting):
            self.cancel(oid, why)
        try:
            page = self.api.private("/trade-api/v2/portfolio/orders", {"status": "resting", "limit": 200})
        except Exception as e:  # noqa: BLE001
            self.log("resting orders unreadable: %s" % str(e)[:160])
            return
        for o in page.get("orders") or []:
            if str(o.get("client_order_id") or "").startswith(mp.CLIENT_PREFIX):
                try:
                    self.api.cancel(o["order_id"])
                    repo.close_order(self.cur, o["order_id"], "cancelled")
                except Exception as e:  # noqa: BLE001
                    self.log("cancel stray %s failed: %s" % (o.get("order_id"), str(e)[:160]))

    def post(self, event: str, ticker: str, price: float, qty: int) -> Optional[str]:
        coid = mp.CLIENT_PREFIX + uuid.uuid4().hex[:24]
        body = {"ticker": ticker, "side": "ask", "count": "%d.00" % qty, "price": "%.4f" % price,
                "time_in_force": "good_till_canceled", "post_only": True,
                "self_trade_prevention_type": "taker_at_cross", "client_order_id": coid}
        try:
            r = self.api.place(body)
        except Exception as e:  # noqa: BLE001 - a refusal (post-only cross, cash) skips this market a while
            self.log("place %s @%.2f x%d refused: %s" % (ticker, price, qty, str(e)[:200]))
            return None
        oid = (r.get("order") or {}).get("order_id") or r.get("order_id")
        if not oid:
            self.log("place %s: no order id in %s" % (ticker, json.dumps(r)[:200]))
            return None
        repo.add_order(self.cur, oid, coid, self.user_id, event, ticker, price, qty)
        self.resting[oid] = {"order_id": oid, "event_ticker": event, "ticker": ticker, "yes_price": price,
                             "qty": qty, "filled": 0.0, "sim_filled": 0.0, "placed_ts": self.now()}
        self.log("post %s @%.2f x%d" % (ticker, price, qty))
        return oid

    # -- the step ---------------------------------------------------------------------------------

    def book_exit(self, x: Dict, f: Dict) -> None:
        try:
            qty = float(f.get("count_fp") or f.get("count") or 0)
            yes_px = f.get("yes_price_dollars")
            yes_px = float(yes_px) if yes_px is not None else 1.0 - float(f["no_price_dollars"])
            fee = float(f.get("fee_cost") or 0)
        except (TypeError, ValueError, KeyError):
            return
        if qty <= 0:
            return
        pnl = repo.book_exit_fill(self.cur, f["fill_id"], x["order_id"], int(x["position_id"]), qty, yes_px, fee,
                                  float(f.get("ts") or self.now()))
        if pnl is not None:
            self.realized = repo.realized_pnl(self.cur, self.user_id)
            self.open_cost = self._open_cost()
            self.log("SOLD %s x%.2f at YES %.2f fee %.4f pnl %+.2f" % (x["ticker"], qty, yes_px, fee, pnl))

    def mark_said(self, ticker: str, ts: float) -> None:
        if ticker not in self.said:
            self.said.add(ticker)
            repo.save_said(self.cur, ticker, ts)

    def read_trades(self) -> List[Dict]:
        rows, cursor = [], None
        for _ in range(5):
            params = {"limit": 1000, "min_ts": int(self.last_trade_ts) - 1}
            if cursor:
                params["cursor"] = cursor
            page = self.api.public("/markets/trades", params)
            rows.extend(page.get("trades") or [])
            cursor = page.get("cursor")
            if not cursor or not page.get("trades"):
                break
        out = []
        for t in rows:
            tid, tk = t.get("trade_id"), t.get("ticker") or ""
            if not tid or tid in self.seen_trades or tk.split("-", 1)[0] not in self.series:
                continue
            ts = _ts(t.get("created_time"))
            if ts is None:
                continue
            self.seen_trades[tid] = ts
            out.append({"ticker": tk, "ts": ts, "p": float(t.get("yes_price_dollars") or 0),
                        "c": float(t.get("count_fp") or 0), "yes": t.get("taker_side") == "yes"})
        if out:
            self.last_trade_ts = max(self.last_trade_ts, max(r["ts"] for r in out))
        cut = self.now() - 120
        self.seen_trades = {k: v for k, v in self.seen_trades.items() if v >= cut}
        return sorted(out, key=lambda r: r["ts"])

    def on_prints(self, prints: List[Dict]) -> None:
        for r in prints:
            ev = event_of(r["ticker"])
            trig = self.triggers.see(ev, r["ticker"], r["ts"], r["p"])
            if trig is not None:
                repo.save_trigger(self.cur, ev, trig)
                self.log("LIVE %s (trigger %s at %.2f)" % (ev, r["ticker"], r["p"]))
            if r["p"] >= mp.CANCEL_AT:
                self.mark_said(r["ticker"], r["ts"])
            for oid, o in self.resting.items():
                if o["ticker"] != r["ticker"] or r["ts"] < o.get("placed_ts", 0):
                    continue
                left = float(o["qty"]) - float(o.get("sim_filled") or 0)
                q = mp.sim_fill(float(o["yes_price"]), left, r["p"], r["c"], r["yes"])
                if q > 0:
                    o["sim_filled"] = float(o.get("sim_filled") or 0) + q
                    repo.add_sim_fill(self.cur, oid, q)

    def sync_fills(self) -> None:
        known = {o["order_id"]: o for o in repo.orders(self.cur)}
        out = repo.exits(self.cur)
        page = self.api.private("/trade-api/v2/portfolio/fills", {"min_ts": self.fill_min_ts, "limit": 200})
        for f in page.get("fills") or []:
            x = out.get(f.get("order_id"))
            if x is not None:
                self.book_exit(x, f)
                continue
            o = known.get(f.get("order_id"))
            terms = mp.fill_terms(f) if o else None
            if not terms:
                continue
            qty, no_px, fee = terms
            if repo.book_fill(self.cur, f["fill_id"], o["order_id"], self.user_id, o["event_ticker"], o["ticker"],
                              qty, no_px, fee, float(f.get("ts") or self.now())):
                self.filled[o["ticker"]] = self.filled.get(o["ticker"], 0.0) + qty
                self.open_cost += qty * no_px + fee
                self.log("FILL %s x%.2f NO @%.2f fee %.4f" % (o["ticker"], qty, no_px, fee))
                r = self.resting.get(o["order_id"])
                if r is not None:
                    r["filled"] = float(r.get("filled") or 0) + qty
                    if r["filled"] >= float(r["qty"]) - 1e-9:
                        repo.close_order(self.cur, o["order_id"], "done")
                        self.resting.pop(o["order_id"], None)

    def quote_event(self, event: str) -> None:
        page = self.api.public("/markets", {"event_ticker": event, "status": "open", "limit": 200})
        mine = {o["ticker"]: oid for oid, o in self.resting.items() if o["event_ticker"] == event}
        for m in page.get("markets") or []:
            tk = m.get("ticker")
            if not tk or int(m.get("exchange_index") or 0) != SHARD:
                continue
            bid = _f(m.get("yes_bid_dollars"))
            ask = _f(m.get("yes_ask_dollars"))
            if bid is not None and bid >= mp.CANCEL_AT:
                self.mark_said(tk, self.now())
            oid = mine.pop(tk, None)
            if oid is not None:
                mine_px = float(self.resting[oid]["yes_price"])
                undercut = ask is not None and ask < mine_px - 1e-9
                if tk in self.said or not (mp.LO <= mine_px < mp.HI):
                    self.cancel(oid, "said" if tk in self.said else "band")
                elif undercut and self.now() - self.last_quote.get(tk, 0) >= mp.REQUOTE_MIN_S:
                    self.cancel(oid, "undercut to %.2f" % ask)
                continue
            if tk in self.said or self.now() - self.last_quote.get(tk, 0) < mp.REQUOTE_MIN_S:
                continue
            px = mp.quote_price(bid, ask)
            if px is None:
                continue
            qty = mp.order_size(self.filled.get(tk, 0.0), 0.0, self.room(), px)
            if qty <= 0:
                continue
            self.last_quote[tk] = self.now()
            self.post(event, tk, px, qty)
        for tk, oid in mine.items():   # no longer an open market of the event
            self.cancel(oid, "closed")

    def step(self) -> None:
        self.on_prints(self.read_trades())
        self.sync_fills()
        now = self.now()
        if now - self.minute_at >= 60:
            self.minute_at = now
            self.read_cash()
            self.settle()
            if mp.halted(self.realized):
                self.log("LOSS STOP: realized %.2f; cancelling everything and stopping" % self.realized)
                self.cancel_all("loss stop")
                self.stopped = True
                return
            self.report()
        live = self.triggers.live(now)
        for oid, o in list(self.resting.items()):
            if o["event_ticker"] not in live:
                self.cancel(oid, "window closed")
        for ev in sorted(live):
            self.quote_event(ev)

    def report(self) -> Dict:
        rep = mp.summary({o["order_id"]: o for o in repo.orders(self.cur)}, self.realized, self.open_cost,
                         repo.settled_count(self.cur, self.user_id))
        rep.update(open_risk=None if self.open_risk is None else round(self.open_risk, 2), risk_at_mark=self.risk_at_mark,
                   generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), shard_cash=self.shard_cash,
                   resting=len(self.resting), live_events=sorted(self.triggers.live(self.now())))
        try:
            import boto3
            boto3.client("s3").put_object(Bucket=BUCKET, Key=REPORT_KEY, Body=json.dumps(rep, default=str).encode(),
                                          ContentType="application/json")
        except Exception as e:  # noqa: BLE001 - the report is a readout, never a reason to stop quoting
            self.log("report not written: %s" % str(e)[:120])
        return rep


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if 0.0 < x < 1.0 else None


def main() -> None:
    if os.getenv("TAU_MENTION_PILOT_ENABLED") != "1" or os.getenv("APP_LIVE_TRADING_ENABLED") != "1":
        print("[mention-pilot] off (TAU_MENTION_PILOT_ENABLED / APP_LIVE_TRADING_ENABLED not 1)", flush=True)
        time.sleep(float(os.getenv("TAU_MENTION_PILOT_OFF_SLEEP_S", "300")))
        return
    from app.adapters.db.kalshi_keys_repository import get_kalshi_key
    from app.adapters.kalshi import kalshi_account
    from app.mention_calibration_run import mention_series
    from data_pipeline.common import get_db_conn

    user_id = int(os.getenv("TAU_MENTION_PILOT_USER", "1"))
    run_s = float(os.getenv("TAU_MENTION_PILOT_RUN_S", "3300"))
    poll_s = float(os.getenv("TAU_MENTION_PILOT_POLL_S", "2"))
    cur = get_db_conn().cursor()
    key = get_kalshi_key(cur, user_id)
    if key is None:
        print("[mention-pilot] user %d has no live Kalshi key" % user_id, flush=True)
        time.sleep(300)
        return
    api = Api(kalshi_account.credentials_from_row(user_id, key))
    pilot = Pilot(cur, api, user_id, {s["ticker"] for s in mention_series()})
    stop = {"now": False}

    def _term(_sig, _frm):
        stop["now"] = True
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    pilot.log("start: %d mention series, realized %.2f, open cost %.2f" % (len(pilot.series), pilot.realized,
                                                                          pilot.open_cost))
    pilot.cancel_all("start")
    if mp.halted(pilot.realized):
        pilot.log("halted for good by the loss stop (realized %.2f); settling only" % pilot.realized)
        pilot.settle()
        time.sleep(600)
        return
    deadline = time.time() + run_s
    errors = 0
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
            time.sleep(max(0.0, poll_s - (time.time() - t0)))
    finally:
        pilot.cancel_all("exit")
        pilot.report()
        pilot.log("exit")


if __name__ == "__main__":
    main()
