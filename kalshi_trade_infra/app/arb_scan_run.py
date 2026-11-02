"""Run the riskless-set scan (app.services.arb_scan) over every weather and sports market's history.

No model, no orders: hist_markets + market_candles only. Writes
s3://<datalake>/research-watch/arb-scan/latest.json plus a dated copy.

    python -m app.arb_scan_run                      # every weather and sports market
    python -m app.arb_scan_run --markets kxlowtchi --no-s3
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from app.adapters.db import candle_range_repository as crr
from app.maker_scan_run import BUCKET, _ts, family_of, scan_markets
from app.services import arb_scan
from app.trading.markets import Market
from data_pipeline.kalshi_fees import taker_fee_dollars

PREFIX = "research-watch/arb-scan"
MAX_EVENTS = 500                     # newest events per market
DEADLINE_S = float(os.getenv("TAU_ARB_SCAN_DEADLINE_S", str(100 * 60)))


def run(cur, markets: List[Market], log=print, deadline_s: float = DEADLINE_S) -> Dict:
    t0 = time.time()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    scan = arb_scan.Scan(taker_fee_dollars)
    done, skipped = [], []
    for m in markets:
        if time.time() - t0 > deadline_s:
            skipped.append(m.key)
            continue
        fam, _ = family_of(m)
        since = now - timedelta(days=m.history_days)
        rows = crr.settled_contracts(cur, m.series, since, now)
        sizes = crr.event_sizes(cur, m.series, since - timedelta(days=2), now + timedelta(days=2))
        by_event: Dict[str, List[Dict]] = defaultdict(list)
        for r in rows:
            by_event[r.get("event_ticker") or r["ticker"].rsplit("-", 1)[0]].append(r)
        events = list(by_event.items())[-MAX_EVENTS:]
        n = 0
        for ev, contracts in events:
            if time.time() - t0 > deadline_s:
                break
            if len(contracts) < 2 or len(contracts) != sizes.get(ev) or not arb_scan.exclusive_event([str(c.get("result") or "").lower()
                                                                   for c in contracts]):
                continue
            legs = []
            for c in contracts:
                bars = {b["end_ts"]: (b["bid"], b["ask"]) for b in crr.candle_ranges(cur, c["ticker"], m.candle_minutes)}
                if not bars:
                    break
                legs.append(arb_scan.Leg(c["ticker"], bars, _ts(c["close_time"])))
            else:
                scan.add_event(fam, ev, legs, m.candle_minutes * 60)
                n += 1
        done.append({"market": m.key, "family": fam, "events": n})
        log("[arb-scan] %s: %d exclusive events, %.0fs" % (m.key, n, time.time() - t0))
    report = scan.finish()
    report.update({"generated_at": datetime.now(timezone.utc).isoformat(), "markets": done,
                   "skipped_for_time": skipped, "seconds": round(time.time() - t0)})
    return report


def publish(report: Dict, s3=None) -> str:
    if s3 is None:
        import boto3
        s3 = boto3.client("s3")
    body = json.dumps(report, default=str, separators=(",", ":")).encode()
    ts = report["generated_at"][:16].replace(":", "")
    key = "%s/date=%s/%s.json" % (PREFIX, ts[:10], ts[11:])
    for k in (key, PREFIX + "/latest.json"):
        s3.put_object(Bucket=BUCKET, Key=k, Body=body, ContentType="application/json")
    return key


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--markets", default="")
    ap.add_argument("--no-s3", action="store_true")
    a = ap.parse_args()
    from data_pipeline.common import get_db_conn
    conn = get_db_conn()
    try:
        report = run(conn.cursor(), scan_markets([k for k in a.markets.split(",") if k] or None))
    finally:
        conn.close()
    print("[arb-scan] %d hits (%d persisting one bar)" % (report["hits"], report["persisting"]), flush=True)
    if not a.no_s3:
        print("[arb-scan] wrote s3://%s/%s" % (BUCKET, publish(report)), flush=True)


if __name__ == "__main__":
    main()
