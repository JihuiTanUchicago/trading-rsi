"""Calibration of Kalshi's settled long-dated crypto contracts (app.services.long_dated_calibration).

Public market data only, no orders and no keys: lists every long-dated crypto series (KXBTCMAXMON,
KXBTCMAXY, KXBTCPRICE, KXETHMINY, ...), every settled market in them (/historical/markets before
Kalshi's cutoff, /markets?status=settled after), and each market's daily candles, then writes

    s3://<datalake>/research-watch/long-dated-calibration/latest.json

    python -m app.long_dated_calibration_run                    # all coins, all history
    python -m app.long_dated_calibration_run --coins BTC --no-s3 --out /tmp/cal.json

Runs on the WEB box (the cloud sandbox cannot reach Kalshi). Candles already fetched are cached in
S3 (research-watch/long-dated-calibration/candles/<ticker>.json): a settled market never changes,
so later runs only fetch markets settled since.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.services import long_dated_calibration as ldc
from data_pipeline.common import api_get

BUCKET = os.getenv("DATALAKE_BUCKET", "kalshi-trade-datalake-568386354218-us-east-1")
PREFIX = "research-watch/long-dated-calibration"
PAUSE_S = float(os.getenv("TAU_LDC_PAUSE_S", "0.12"))
KEEP = ("ticker", "event_ticker", "series_ticker", "title", "strike_type", "floor_strike", "cap_strike",
        "open_time", "close_time", "result", "status")


def _get(path: str, params: Optional[Dict] = None) -> Dict:
    time.sleep(PAUSE_S)
    return api_get(path, params) or {}


def long_dated_series(coins) -> List[str]:
    out = []
    for s in _get("/series").get("series") or []:
        t = s.get("ticker") or ""
        if ldc.is_long_dated(t) and ldc.coin_of(t) in coins:
            out.append(t)
    return sorted(set(out))


def settled_markets(series: str) -> List[Dict]:
    seen: Dict[str, Dict] = {}
    for path, params in (("/historical/markets", {"series_ticker": series, "limit": 1000}),
                         ("/markets", {"series_ticker": series, "status": "settled", "limit": 1000})):
        cursor = None
        for _ in range(50):
            try:
                data = _get(path, dict(params, cursor=cursor) if cursor else params)
            except RuntimeError as e:
                print("[ldc] %s %s: %s" % (path, series, str(e)[:160]), flush=True)
                break
            for m in data.get("markets") or []:
                if m.get("ticker") and ldc.result_of(m) is not None:
                    seen[m["ticker"]] = dict({k: m.get(k) for k in KEEP}, series_ticker=series)
            cursor = data.get("cursor")
            if not cursor:
                break
    return list(seen.values())


def fetch_candles(m: Dict, cutoff_ts: Optional[int]) -> List[Dict]:
    o, c = ldc._ts(m.get("open_time")), ldc._ts(m.get("close_time"))
    if o is None or c is None:
        return []
    paths = ["/series/%s/markets/%s/candlesticks" % (m["series_ticker"], m["ticker"])]
    hist = "/historical/markets/%s/candlesticks" % m["ticker"]
    paths.insert(0 if cutoff_ts is not None and c < cutoff_ts else 1, hist)
    for path in paths:
        out, t, ok = [], o, True
        while t < c:
            end = min(t + 4900 * 86400, c)
            try:
                data = _get(path, {"start_ts": t, "end_ts": end, "period_interval": 1440})
            except RuntimeError:
                ok = False
                break
            out.extend({"end_period_ts": x.get("end_period_ts"), "yes_bid": x.get("yes_bid"), "yes_ask": x.get("yes_ask")}
                       for x in data.get("candlesticks") or [])
            t = end
        if ok:
            return out
    return []


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--coins", default=",".join(ldc.COINS))
    ap.add_argument("--no-s3", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    coins = tuple(x.strip().upper() for x in a.coins.split(",") if x.strip())
    s3 = None
    if not a.no_s3:
        import boto3
        s3 = boto3.client("s3")
    t0 = time.time()
    try:
        cut = ldc._ts((_get("/historical/cutoff") or {}).get("market_settled_ts"))
    except RuntimeError:
        cut = None
    series = long_dated_series(coins)
    print("[ldc] %d long-dated series: %s" % (len(series), " ".join(series)), flush=True)
    markets: List[Dict] = []
    for s in series:
        ms = settled_markets(s)
        print("[ldc] %s: %d settled" % (s, len(ms)), flush=True)
        markets.extend(ms)
    candles: Dict[str, List[Dict]] = {}
    fetched = cached = 0
    for m in markets:
        tk = m["ticker"]
        key = "%s/candles/%s.json" % (PREFIX, tk)
        cs = None
        if s3 is not None:
            try:
                cs = json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
                cached += 1
            except Exception:
                cs = None
        if cs is None:
            cs = fetch_candles(m, cut)
            fetched += 1
            if s3 is not None and cs:
                s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(cs, separators=(",", ":")).encode(),
                              ContentType="application/json")
        candles[tk] = cs
    rows = ldc.observations(markets, candles)
    rep = ldc.summarize(rows)
    rep.update(generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), series=series,
               settled_markets=len(markets), candles_fetched=fetched, candles_cached=cached,
               seconds=round(time.time() - t0, 1), horizons_d=list(ldc.HORIZONS_D),
               note="Sell = sell YES at the bid (buy NO) as a taker, hold to settlement; c per contract; "
                    "p5/p95 resample whole end dates. No orders.")
    for r in rep["rules"]:
        print("[ldc] %-32s n %5d dates %3d mid %.3f yes %.3f sell %sc [%s, %s] %s" % (
            r["name"], r["n"], r["end_dates"], r["mean_mid"] or 0, r["yes_rate"] or 0, r["sell_c"],
            r["sell_c_p5"], r["sell_c_p95"], r["verdict"]), flush=True)
    body = json.dumps(rep, default=str)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(dict(rep, obs=rows), fh, default=str)
    if s3 is not None:
        s3.put_object(Bucket=BUCKET, Key=PREFIX + "/latest.json", Body=body.encode(), ContentType="application/json")
        s3.put_object(Bucket=BUCKET, Key=PREFIX + "/obs.json", Body=json.dumps(rows, separators=(",", ":")).encode(),
                      ContentType="application/json")
        print("[ldc] wrote s3://%s/%s/latest.json" % (BUCKET, PREFIX), flush=True)


if __name__ == "__main__":
    main()
