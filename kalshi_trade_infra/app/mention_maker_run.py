"""Pre-registered forward test of the in-play mention YES seller (app.services.mention_maker).

Public market data only, no orders and no keys. Every day it lists the mention series, the markets
in them that settled since the forward start, and every trade print of those markets, replays the
frozen rule on each event that went live, and writes

    s3://<datalake>/research-watch/mention-maker/events/<event>.json   (one per settled event, cached)
    s3://<datalake>/research-watch/mention-maker/latest.json           (pooled result + the one-look verdict)

    python -m app.mention_maker_run
    python -m app.mention_maker_run --since 2026-10-01T00:00:00Z --no-s3 --out /tmp/mm.json   # replay

Runs on the WEB box (the cloud sandbox cannot reach Kalshi).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.mention_calibration_run import mention_series
from app.services import mention_maker as mm
from app.services.long_dated_calibration import _ts, result_of
from data_pipeline.common import api_get
from data_pipeline.kalshi_fees import maker_fee_dollars

BUCKET = os.getenv("DATALAKE_BUCKET", "kalshi-trade-datalake-568386354218-us-east-1")
PREFIX = "research-watch/mention-maker"
PAUSE_S = float(os.getenv("TAU_MENTION_PAUSE_S", "0.06"))


def _get(path: str, params: Optional[Dict] = None) -> Dict:
    time.sleep(PAUSE_S)
    return api_get(path, params) or {}


def settled_since(series: str, since_ts: int) -> List[Dict]:
    out, cursor = [], None
    for _ in range(50):
        params = {"series_ticker": series, "status": "settled", "min_close_ts": since_ts, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        try:
            data = _get("/markets", params)
        except RuntimeError as e:
            print("[mention-maker] markets %s: %s" % (series, str(e)[:160]), flush=True)
            break
        for m in data.get("markets") or []:
            if m.get("ticker") and result_of(m) is not None:
                out.append({"ticker": m["ticker"], "event_ticker": m.get("event_ticker"), "y": result_of(m)})
        cursor = data.get("cursor")
        if not cursor:
            break
    return out


def prints_of(ticker: str) -> List[mm.Print]:
    for path in ("/markets/trades", "/historical/trades"):
        rows, cursor = [], None
        for _ in range(200):
            params = {"ticker": ticker, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            try:
                data = _get(path, params)
            except RuntimeError:
                break
            for t in data.get("trades") or []:
                ts = _ts(t.get("created_time"))
                p = t.get("yes_price_dollars")
                c = t.get("count_fp")
                if ts is None or p is None or c is None:
                    continue
                rows.append((float(ts), float(p), float(c), t.get("taker_side") == "yes"))
            cursor = data.get("cursor")
            if not cursor or not data.get("trades"):
                break
        if rows:
            return rows
    return []


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default=mm.FORWARD_FROM, help="count events whose live trigger is at or after this")
    ap.add_argument("--no-s3", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    since = _ts(a.since)
    s3 = None
    if not a.no_s3:
        import boto3
        s3 = boto3.client("s3")
    t0 = time.time()
    by_event: Dict[str, List[Dict]] = defaultdict(list)
    series_of: Dict[str, str] = {}
    for s in mention_series():
        for m in settled_since(s["ticker"], since - 86400):
            by_event[m["event_ticker"]].append(m)
            series_of[m["event_ticker"]] = s["ticker"]
    print("[mention-maker] %d settled events since %s" % (len(by_event), a.since), flush=True)
    events: List[Dict] = []
    new = 0
    for ev, ms in sorted(by_event.items()):
        key = "%s/events/%s.json" % (PREFIX, ev)
        rec = None
        if s3 is not None:
            try:
                rec = json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
            except Exception:
                rec = None
        if rec is None:
            prints = {m["ticker"]: prints_of(m["ticker"]) for m in ms}
            res = mm.event_result(prints, {m["ticker"]: m["y"] for m in ms}, maker_fee_dollars)
            rec = dict(res or {"live": False}, event=ev, series=series_of[ev], markets=len(ms))
            new += 1
            if s3 is not None:
                s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(rec).encode(), ContentType="application/json")
        if rec.get("trigger") is not None and rec["trigger"] >= since:
            events.append(rec)
    rep = mm.summarize(events)
    per_series: Dict[str, Dict] = {}
    for e in events:
        d = per_series.setdefault(e["series"], {"events": 0, "filled": 0.0, "pnl": 0.0})
        d["events"] += 1
        d["filled"] += e.get("filled", 0.0)
        d["pnl"] += e.get("pnl", 0.0)
    rep.update(generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), since=a.since,
               rule=dict(band=[mm.LO, mm.HI], qty=mm.QTY, hold_s=mm.HOLD_S, delay_s=mm.DELAY_S,
                         trigger=[mm.TRIGGER_FROM_BELOW, mm.TRIGGER_AT], maker_fee="1.75%"),
               settled_events=len(by_event), new_events=new, seconds=round(time.time() - t0, 1),
               series={k: dict(v, c=round(100 * v["pnl"] / v["filled"], 2) if v["filled"] else None)
                       for k, v in sorted(per_series.items(), key=lambda kv: -kv[1]["events"])},
               note="c per contract after maker fee, hold to settlement, fills = first 100 YES-taker contracts "
                    "at 15-65c per market from 5s to 2.5h after the event's live trigger. No orders.")
    print("[mention-maker] events %s c %s t %s verdict %s" % (rep["events"], rep.get("c"), rep.get("t"), rep["verdict"]),
          flush=True)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(dict(rep, events_detail=events), fh, default=str)
    if s3 is not None:
        s3.put_object(Bucket=BUCKET, Key=PREFIX + "/latest.json", Body=json.dumps(rep, default=str).encode(),
                      ContentType="application/json")


if __name__ == "__main__":
    main()
