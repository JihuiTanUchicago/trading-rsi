"""Watcher for the PAPER implication-lag catcher (app.services.implication). Never places an order.

Runs on the WEB box as tau-implication-watch.service, restarted by its timer; each run lasts at most
TAU_IMPLICATION_RUN_S (4h) so a deploy's new code is picked up, and saves its price history to
STATE_PATH so the next run does not start blind to the last 15 minutes.

    python -m app.implication_watch_run
    python -m app.implication_watch_run --run-s 120 --no-s3      # smoke test

Every POLL_S seconds it reads the top of book of every market the daily map watches (the "if" side of a
link), in batches through GET /markets?tickers=. When implication.TriggerWatch fires, it reads the book
of every market the map says that outcome forces, records what buying the forced side at the visible
asks would have cost (implication.paper_fill, up to 90c, 200 contracts), and reads those books again
60s and 300s later to measure how fast the lag closes. Each trigger is written to
s3://<datalake>/research-watch/implication/triggers/<ts>_<ticker>_<side>.json; the daily map run
settles them and keeps the scoreboard.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import time
from typing import Callable, Dict, List, Optional

from app.services import implication as imp
from data_pipeline.common import api_get

BUCKET = os.getenv("DATALAKE_BUCKET", "kalshi-trade-datalake-568386354218-us-east-1")
PREFIX = "research-watch/implication"
POLL_S = float(os.getenv("TAU_IMPLICATION_POLL_S", "5"))
RUN_S = float(os.getenv("TAU_IMPLICATION_RUN_S", str(4 * 3600)))
BATCH = 100
MIN_GAP_S = 0.3                         # between API calls: ~3 req/s at most, shared with the live pilots
STATE_PATH = os.getenv("TAU_IMPLICATION_STATE", "/opt/kalshi/state/implication_watch.json")
FOLLOW_UPS = (60.0, 300.0)


def _num(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def top_of(m: Dict):
    """(yes_bid, yes_ask, volume) of a /markets row; prices in dollars (older rows carry cents)."""
    def px(k):
        v = _num(m.get(k + "_dollars"))
        if v is None:
            v = _num(m.get(k))
            v = v / 100.0 if v is not None and v > 1.0 else v
        return v if v and 0.0 < v < 1.0 else None
    vol = _num(m.get("volume_fp"))
    return px("yes_bid"), px("yes_ask"), vol if vol is not None else (_num(m.get("volume")) or 0.0)


class Watcher:
    def __init__(self, mp: Dict, get: Callable = api_get, now: Callable[[], float] = time.time,
                 put: Optional[Callable[[str, Dict], None]] = None, sleep: Callable[[float], None] = time.sleep,
                 log: Callable[[str], None] = lambda m: print("[implication-watch] " + m, flush=True)):
        self.get, self.now, self.put, self.sleep, self.log = get, now, put, sleep, log
        self.tw = imp.TriggerWatch()
        self.load_map(mp)
        self.pending: List[Dict] = []      # triggers waiting for their follow-up book reads
        self._last_call = 0.0
        self.polls = 0

    def load_map(self, mp: Dict) -> None:
        self.watch = list(mp.get("watch") or [])
        self.edges: Dict[tuple, List[Dict]] = {}
        for e in mp.get("edges") or []:
            self.edges.setdefault((e["if"], e["if_result"]), []).append(e)

    def _call(self, path: str, params: Optional[Dict] = None) -> Dict:
        wait = MIN_GAP_S - (self.now() - self._last_call)
        if wait > 0:
            self.sleep(wait)
        self._last_call = self.now()
        try:
            return self.get(path, params) or {}
        except RuntimeError as e:
            self.log("GET %s failed: %s" % (path, str(e)[:200]))
            return {}

    def poll(self) -> List[Dict]:
        """One pass over the watch list; returns the triggers that fired (already written)."""
        fired, seen = [], 0
        for i in range(0, len(self.watch), BATCH):
            batch = self.watch[i:i + BATCH]
            want = set(batch)
            rows = self._call("/markets", {"tickers": ",".join(batch), "limit": len(batch)}).get("markets") or []
            ts = self.now()
            for m in rows:
                if m.get("ticker") not in want:   # never score a market we did not ask for
                    continue
                seen += 1
                bid, ask, vol = top_of(m)
                side = self.tw.update(m.get("ticker"), ts, bid, ask, vol)
                if side:
                    fired.append(self.on_trigger(m.get("ticker"), side, ts, bid, ask, vol))
        self.polls += 1
        if self.watch and (self.polls == 1 or self.polls % 720 == 0):   # first pass, then hourly at 5s
            self.log("poll %d: %d of %d watched markets returned" % (self.polls, seen, len(self.watch)))
        return fired

    def book(self, ticker: str) -> Dict:
        return self._call("/markets/%s/orderbook" % ticker)

    def on_trigger(self, ticker: str, side: str, ts: float, bid, ask, vol) -> Dict:
        fills = []
        for e in self.edges.get((ticker, side), []):
            f = imp.paper_fill(self.book(e["then"]), e["then_result"])
            fills.append(dict(f, then=e["then"], why=e["why"], confidence=e["confidence"], follow={}))
        t = {"ticker": ticker, "side": side, "ts": ts, "yes_bid": bid, "yes_ask": ask, "volume": vol,
             "fills": fills}
        self.log("TRIGGER %s %s bid=%s ask=%s; %d implied: %s" % (
            ticker, side, bid, ask, len(fills),
            ", ".join("%s %s x%.0f@%s" % (f["then"], f["side"], f["qty"], f["avg"]) for f in fills)))
        self.save(t)
        if fills:
            self.pending.append(t)
        return t

    def follow_up(self) -> None:
        """Read the implied books again FOLLOW_UPS seconds after each trigger: how fast the lag closed."""
        now = self.now()
        for t in list(self.pending):
            for lag in FOLLOW_UPS:
                k = str(int(lag))
                if now - t["ts"] >= lag and k not in t["fills"][0]["follow"]:
                    for f in t["fills"]:
                        g = imp.paper_fill(self.book(f["then"]), f["side"])
                        f["follow"][k] = {"best_ask": g["best_ask"], "depth_at_max": g["depth_at_max"]}
                    self.save(t)
            if all(str(int(x)) in t["fills"][0]["follow"] for x in FOLLOW_UPS):
                self.pending.remove(t)

    def save(self, t: Dict) -> None:
        if self.put:
            key = "%s/triggers/%d_%s_%s.json" % (PREFIX, int(t["ts"]), t["ticker"], t["side"])
            try:
                self.put(key, t)
            except Exception as e:      # S3 hiccup: the log line above still has it
                self.log("save failed: %s" % str(e)[:200])

    # The price history survives a restart, so a run never starts blind to the lookback window.
    def dump_state(self, path: str) -> None:
        state = {"saved": self.now(), "fired": sorted(list(x) for x in self.tw.fired),
                 "hist": {k: list(v) for k, v in self.tw.hist.items() if v}}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(state, f)

    def load_state(self, path: str, max_age_s: float = 600.0) -> None:
        try:
            with open(path) as f:
                st = json.load(f)
        except (OSError, ValueError):
            return
        self.tw.fired = {tuple(x) for x in st.get("fired") or []}   # fired stays fired, however old
        if self.now() - float(st.get("saved", 0)) > max_age_s:
            return
        for k, v in (st.get("hist") or {}).items():
            self.tw.hist[k].extend(tuple(x) for x in v)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-s", type=float, default=RUN_S)
    ap.add_argument("--no-s3", action="store_true")
    a = ap.parse_args(argv)
    import boto3
    s3 = boto3.client("s3")

    def get_map() -> Dict:
        try:
            return json.loads(s3.get_object(Bucket=BUCKET, Key=PREFIX + "/map.json")["Body"].read())
        except Exception as e:
            print("[implication-watch] no map yet: %s" % str(e)[:200], flush=True)
            return {}

    put = None if a.no_s3 else (lambda k, o: s3.put_object(Bucket=BUCKET, Key=k, Body=json.dumps(o, default=str).encode(),
                                                           ContentType="application/json"))
    w = Watcher(get_map(), put=put)
    w.load_state(STATE_PATH)
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    start = last_map = time.time()
    w.log("watching %d markets, %d links" % (len(w.watch), sum(len(v) for v in w.edges.values())))
    while not stop["now"] and time.time() - start < a.run_s:
        t0 = time.time()
        if not w.watch:
            time.sleep(10)
        else:
            w.poll()
            w.follow_up()
        if time.time() - last_map > 1800:
            w.load_map(get_map() or {"watch": w.watch, "edges": [e for v in w.edges.values() for e in v]})
            last_map = time.time()
            w.log("map reloaded: watching %d markets, %d links" % (len(w.watch), sum(len(v) for v in w.edges.values())))
        time.sleep(max(0.0, POLL_S - (time.time() - t0)))
    # Let open follow-ups finish (at most 5 min) so no trigger is left half measured.
    end = time.time()
    while w.pending and not stop["now"] and time.time() - end < max(FOLLOW_UPS) + 10:
        w.follow_up()
        time.sleep(5)
    w.dump_state(STATE_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
