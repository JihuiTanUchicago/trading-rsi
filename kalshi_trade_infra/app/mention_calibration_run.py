"""Calibration and track-record test of Kalshi's settled mention markets (app.services.mention_calibration).

Public market data only, no orders and no keys. Lists every mention series (ticker has MENTION,
category "Mentions", or a "What will X say" title), every settled market in them, and each
market's hourly candles over the last 50 hours before its event ended, then writes

    s3://<datalake>/research-watch/mention-calibration/latest.json   (rules, calibration, series)
    s3://<datalake>/research-watch/mention-calibration/obs.json      (every observation)
    s3://<datalake>/research-watch/mention-calibration/samples.json  (raw market fields, 2 per series)

    python -m app.mention_calibration_run
    python -m app.mention_calibration_run --max-markets 2000 --no-s3 --out /tmp/m.json

Runs on the WEB box (the cloud sandbox cannot reach Kalshi). Candles are cached per series in S3
(candles/<series>.json, compact [end_ts, bid, ask] rows); a settled market never changes, so later runs fetch only new ones.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.services import mention_calibration as mc
from app.services.long_dated_calibration import _ts, event_ends, result_of
from data_pipeline.common import api_get

BUCKET = os.getenv("DATALAKE_BUCKET", "kalshi-trade-datalake-568386354218-us-east-1")
PREFIX = "research-watch/mention-calibration"
PAUSE_S = float(os.getenv("TAU_MENTION_PAUSE_S", "0.06"))
WINDOW_H = 50
KEEP = ("ticker", "event_ticker", "title", "yes_sub_title", "no_sub_title", "custom_strike", "strike_type",
        "open_time", "close_time", "expected_expiration_time", "result", "status", "volume")


def _get(path: str, params: Optional[Dict] = None) -> Dict:
    time.sleep(PAUSE_S)
    return api_get(path, params) or {}


def mention_series() -> List[Dict]:
    out = []
    for s in _get("/series").get("series") or []:
        if mc.is_mention_series(s.get("ticker"), s.get("category"), s.get("title")):
            out.append({k: s.get(k) for k in ("ticker", "title", "category", "frequency")})
    return sorted(out, key=lambda s: s["ticker"])


def settled_markets(series: str) -> List[Dict]:
    seen: Dict[str, Dict] = {}
    for path, params in (("/historical/markets", {"series_ticker": series, "limit": 1000}),
                         ("/markets", {"series_ticker": series, "status": "settled", "limit": 1000})):
        cursor = None
        for _ in range(200):
            try:
                data = _get(path, dict(params, cursor=cursor) if cursor else params)
            except RuntimeError as e:
                print("[mention] %s %s: %s" % (path, series, str(e)[:160]), flush=True)
                break
            for m in data.get("markets") or []:
                if m.get("ticker") and result_of(m) is not None:
                    seen[m["ticker"]] = dict({k: m.get(k) for k in KEEP}, series_ticker=series)
            cursor = data.get("cursor")
            if not cursor:
                break
    return list(seen.values())


def fetch_candles(m: Dict, end: int, cutoff_ts: Optional[int]) -> List[Dict]:
    o = _ts(m.get("open_time")) or end - WINDOW_H * 3600
    start = max(o, end - WINDOW_H * 3600)
    hist = "/historical/markets/%s/candlesticks" % m["ticker"]
    live = "/series/%s/markets/%s/candlesticks" % (m["series_ticker"], m["ticker"])
    c = _ts(m.get("close_time")) or end
    for path in ((hist, live) if cutoff_ts is not None and c < cutoff_ts else (live, hist)):
        try:
            data = _get(path, {"start_ts": start, "end_ts": min(end, c + 3600), "period_interval": 60})
        except RuntimeError:
            continue
        return mc.compact(data.get("candlesticks") or [])
    return []


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--max-markets", type=int, default=int(os.getenv("TAU_MENTION_MAX_MARKETS", "60000")))
    ap.add_argument("--no-s3", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    s3 = None
    if not a.no_s3:
        import boto3
        s3 = boto3.client("s3")
    t0 = time.time()
    try:
        cut = _ts((_get("/historical/cutoff") or {}).get("market_settled_ts"))
    except RuntimeError:
        cut = None
    series = mention_series()
    print("[mention] %d mention series" % len(series), flush=True)
    markets: List[Dict] = []
    samples = {}
    for s in series:
        ms = settled_markets(s["ticker"])
        samples[s["ticker"]] = dict(s, markets=ms[:2], settled=len(ms))
        print("[mention] %s: %d settled" % (s["ticker"], len(ms)), flush=True)
        markets.extend(ms)
    if s3 is not None:
        s3.put_object(Bucket=BUCKET, Key=PREFIX + "/samples.json", Body=json.dumps(samples, default=str).encode(),
                      ContentType="application/json")
    ends = event_ends(markets)
    # newest first, so a capped run still covers the most recent (and most liquid) years
    markets.sort(key=lambda m: -(ends.get(m.get("event_ticker")) or 0))
    markets = markets[:a.max_markets]
    quotes: Dict[str, Dict] = {}
    fetched = 0
    by_series: Dict[str, List[Dict]] = {}
    for m in markets:
        by_series.setdefault(m["series_ticker"], []).append(m)
    for s, ms in by_series.items():
        key = "%s/candles/%s.json" % (PREFIX, s)
        cache: Dict[str, List[List[float]]] = {}
        if s3 is not None:
            try:
                cache = json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
            except Exception:
                cache = {}
        new = 0
        for m in ms:
            tk = m["ticker"]
            if tk not in cache:
                end = ends.get(m.get("event_ticker"))
                cache[tk] = fetch_candles(m, end, cut) if end else []
                new += 1
            end = ends.get(m.get("event_ticker"))
            if end:
                quotes[tk] = mc.horizon_quotes(cache[tk], end)
        fetched += new
        if s3 is not None and new:
            s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(cache, separators=(",", ":")).encode(),
                          ContentType="application/json")
        print("[mention] candles %s: %d markets, %d fetched (%.0fs)" % (s, len(ms), new, time.time() - t0), flush=True)
    rows = mc.observations(markets, quotes)
    rep = mc.summarize(rows)
    rep.update(generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               series=[s["ticker"] for s in series], settled_markets=len(markets), candles_fetched=fetched,
               seconds=round(time.time() - t0, 1), horizons_h=list(mc.HORIZONS_H),
               note="c per contract after taker fee, hold to settlement; p5/p95 resample whole events. No orders.")
    for r in rep["rules"]:
        print("[mention] %-24s trades %6d events %5d pnl %sc [%s, %s] %s" % (
            r["name"], r["trades"], r["events"], r["pnl_c"], r["pnl_c_p5"], r["pnl_c_p95"], r["verdict"]), flush=True)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(dict(rep, obs=rows), fh, default=str)
    if s3 is not None:
        s3.put_object(Bucket=BUCKET, Key=PREFIX + "/obs.json", Body=json.dumps(rows, separators=(",", ":")).encode(),
                      ContentType="application/json")
        s3.put_object(Bucket=BUCKET, Key=PREFIX + "/latest.json", Body=json.dumps(rep, default=str).encode(),
                      ContentType="application/json")
        print("[mention] wrote s3://%s/%s/latest.json" % (BUCKET, PREFIX), flush=True)


if __name__ == "__main__":
    main()
