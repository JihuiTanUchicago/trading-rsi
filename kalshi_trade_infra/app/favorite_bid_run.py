"""Pre-registered forward test of the favorite-bid lead (app.services.favorite_bid) on Kalshi BTC ticks.

No orders and no model: replays our would-be resting bid against the recorded per-second books
(features_1s) and trade prints (kalshi_trades) in the S3 tick store, settles every contract on the
official 60s BRTI average (the next 15-minute contract's strike), and writes

    s3://<datalake>/research-watch/favorite-bid/days/<YYYY-MM-DD>.json   (one per finished UTC day)
    s3://<datalake>/research-watch/favorite-bid/latest.json              (forward verdict + grid)

    python -m app.favorite_bid_run                       # every finished day since FORWARD_FROM
    python -m app.favorite_bid_run --start 2026-10-25 --end 2026-11-04 --no-s3 --label discovery

Days already in S3 are not recomputed, so the daily timer only reads the newest day.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.services import favorite_bid as fb
from data_pipeline.kalshi_fees import maker_fee_dollars

BUCKET = os.getenv("DATALAKE_BUCKET", "kalshi-trade-datalake-568386354218-us-east-1")
PREFIX = "research-watch/favorite-bid"
BOOK_COLS = ["sec", "ticker", "close_ts", "yes_bid", "yes_ask", "yes_bid_size", "yes_ask_size"]
TRADE_COLS = ["ticker", "yes_price", "count", "taker_side", "exch_ms", "rt"]
MAX_HORIZON = max(fb.GRID_HORIZONS + (fb.HORIZON_S,))


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def settlement_chain(store, start: datetime, end: datetime) -> Tuple[Dict[int, float], Dict[str, float]]:
    """({open_ts: official 60s BRTI average ending then}, {ticker: own strike}) for KXBTC15M."""
    t = store.read("kalshi_markets", start - timedelta(hours=1), end + timedelta(hours=2),
                   columns=["rt", "ticker", "series", "open_ts", "floor_strike"])
    chain, strike = {}, {}
    if t is None:
        return chain, strike
    for tk, s, o, k in zip(*(t[c].to_pylist() for c in ("ticker", "series", "open_ts", "floor_strike"))):
        if s != fb.SERIES or k is None or o is None:
            continue
        chain[int(o)] = float(k)
        strike[tk] = float(k)
    return chain, strike


def day_fills(store, day: datetime, cells: List[Tuple[float, int]], log=print) -> Dict:
    """Fills for every contract closing in [day, day + 1d), for each (price, horizon) cell."""
    start, end = day, day + timedelta(days=1)
    chain, strike = settlement_chain(store, start, end)
    out: Dict[str, List[Dict]] = defaultdict(list)
    n_contracts = n_settled = 0
    hour = start
    while hour < end:
        lo = hour - timedelta(seconds=MAX_HORIZON)
        hi = hour + timedelta(hours=1)
        f = store.features_1s(lo, hi, series=[fb.SERIES], columns=BOOK_COLS)
        tr = store.read("kalshi_trades", lo.replace(minute=0, second=0), hi, columns=TRADE_COLS)
        hour = hi
        hi_ts = hi.replace(tzinfo=timezone.utc).timestamp()
        if not f:
            continue
        books: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
        closes: Dict[str, int] = {}
        for i, tk in enumerate(f["ticker"]):
            ct = int(f["close_ts"][i])
            if not (hi_ts - 3600 < ct <= hi_ts):      # each contract belongs to exactly one hour
                continue
            closes[tk] = ct
            b = books[tk]
            for c in ("sec", "yes_bid", "yes_ask", "yes_bid_size", "yes_ask_size"):
                b[c].append(f[c][i])
        prints: Dict[str, List[tuple]] = defaultdict(list)
        if tr is not None:
            for tk, yp, cnt, side, ms in zip(*(tr[c].to_pylist() for c in ("ticker", "yes_price", "count", "taker_side", "exch_ms"))):
                if tk in closes and yp is not None and cnt:
                    prints[tk].append((ms / 1000.0, float(yp), float(cnt), side))
        for tk, ct in closes.items():
            if not (start.replace(tzinfo=timezone.utc).timestamp() < ct <= end.replace(tzinfo=timezone.utc).timestamp()):
                continue
            n_contracts += 1
            x, k = chain.get(ct), strike.get(tk)
            res = None if x is None or k is None else (1.0 if x >= k else 0.0)
            if res is None:
                continue
            n_settled += 1
            b = books[tk]
            order = sorted(range(len(b["sec"])), key=lambda j: b["sec"][j])
            book = {c: [b[c][j] for j in order] for c in b}
            for p, h in cells:
                for fl in fb.contract_fills(tk, ct, res, book, prints.get(tk, []), p, h, fb.QTY, maker_fee_dollars):
                    out["%.2f@%d" % (p, h)].append(fl.__dict__)
    log("[favorite-bid] %s: %d contracts, %d settled" % (day.date(), n_contracts, n_settled))
    return {"date": day.strftime("%Y-%m-%d"), "contracts": n_contracts, "settled": n_settled, "cells": dict(out)}


def _fills(rows: List[Dict]) -> List[fb.Fill]:
    return [fb.Fill(**r) for r in rows]


def aggregate(days: List[Dict], forward_from: datetime) -> Dict:
    ff = forward_from.replace(tzinfo=timezone.utc).timestamp()
    pooled: Dict[str, List[Dict]] = defaultdict(list)
    for d in days:
        for cell, rows in d["cells"].items():
            pooled[cell].extend(r for r in rows if r["close_ts"] >= ff)
    key = "%.2f@%d" % (fb.PRICE, fb.HORIZON_S)
    j = fb.judge(_fills(pooled.get(key, [])), fb.PRICE)
    grid = {c: fb.judge(_fills(rows), float(c.split("@")[0])) for c, rows in sorted(pooled.items())}
    return {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rule": {"series": fb.SERIES, "price": fb.PRICE, "horizon_s": fb.HORIZON_S, "qty": fb.QTY,
                     "forward_from": fb.FORWARD_FROM, "min_fills": fb.MIN_FILLS, "alpha": fb.ALPHA},
            "days": [d["date"] for d in days], "forward": j, "verdict": fb.verdict(j),
            "grid_descriptive": grid}


def _s3():
    import boto3
    return boto3.client("s3")


def load_day(s3, date: str) -> Optional[Dict]:
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key="%s/days/%s.json" % (PREFIX, date))["Body"].read())
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default=fb.FORWARD_FROM[:10])
    ap.add_argument("--end", default=None, help="exclusive UTC date (default: today, so only finished days)")
    ap.add_argument("--no-s3", action="store_true")
    ap.add_argument("--label", default="forward")
    ap.add_argument("--out", default=None, help="also write the full report (with per-day fills) to this local JSON file")
    a = ap.parse_args()
    from data_pipeline.tick_store import TickStore
    store = TickStore()
    s3 = None if a.no_s3 else _s3()
    cells = sorted({(p, h) for p in fb.GRID_PRICES for h in fb.GRID_HORIZONS} | {(fb.PRICE, fb.HORIZON_S)})
    day = _utc(a.start + "T00:00:00Z") if "T" not in a.start else _utc(a.start)
    day = day.replace(hour=0, minute=0, second=0, microsecond=0)
    stop = _utc(a.end + "T00:00:00Z") if a.end else datetime.now(timezone.utc).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    days = []
    t0 = time.time()
    while day < stop:
        date = day.strftime("%Y-%m-%d")
        d = load_day(s3, date) if s3 else None
        if d is None:
            d = day_fills(store, day, cells)
            if s3:
                s3.put_object(Bucket=BUCKET, Key="%s/days/%s.json" % (PREFIX, date),
                              Body=json.dumps(d, separators=(",", ":")).encode(), ContentType="application/json")
        days.append(d)
        day += timedelta(days=1)
    forward_from = _utc(fb.FORWARD_FROM) if a.label == "forward" else _utc(a.start + "T00:00:00Z")
    rep = aggregate(days, forward_from)
    rep["label"], rep["seconds"] = a.label, round(time.time() - t0, 1)
    j = rep["forward"]
    print("[favorite-bid] %s: %s | fills %d, losses %d (%.2f%% vs break-even %.2f%%), %+.2fc/contract, p %.3g"
          % (a.label, rep["verdict"], j["fills"], j["losses"], 100 * (j["loss_rate"] or 0),
             100 * j["break_even_loss_rate"], j["mean_c"], j["p_binom"] if j["p_binom"] is not None else 1.0), flush=True)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(dict(rep, day_rows=days), fh, default=str)
    if s3:
        s3.put_object(Bucket=BUCKET, Key=PREFIX + "/latest.json", Body=json.dumps(rep, default=str).encode(),
                      ContentType="application/json")
        print("[favorite-bid] wrote s3://%s/%s/latest.json" % (BUCKET, PREFIX), flush=True)
    else:
        print(json.dumps({k: v for k, v in rep.items() if k != "grid_descriptive"}, default=str, indent=1))


if __name__ == "__main__":
    main()
