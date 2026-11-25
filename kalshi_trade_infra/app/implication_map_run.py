"""Daily rules map for the PAPER implication-lag catcher (app.services.implication), and its scoreboard.

PAPER ONLY: public market data and one LLM read per changed topic cluster, never an order or a key.
Runs on the WEB box as tau-implication-map (daily), because the cloud sandbox cannot reach Kalshi.

    python -m app.implication_map_run                 # map + score, write S3
    python -m app.implication_map_run --score-only    # settle paper fills and rewrite latest.json
    python -m app.implication_map_run --max-calls 5 --no-s3 --out /tmp/map.json

1. Lists every open event in a news series (not sports, crypto, weather, mentions, parlays) with its
   markets, from /events?status=open in pages of 200.
2. Builds one neighbourhood per liquid event: its most similar events in other series
   (implication.clusters), busiest first.
3. Asks the LLM, once per cluster whose markets or rules changed, which cross-event links the rules
   force, then has a second skeptical read confirm each one; unchanged clusters keep yesterday's links.
   TAU_IMPLICATION_MAX_CALLS caps the calls a day (both reads count).
4. Writes s3://<datalake>/research-watch/implication/map.json (links + the tickers to watch).
5. Settles the watcher's paper fills (triggers/*.json) and writes latest.json with the scoreboard and
   the ONE pre-registered judgment (frozen the first time it is reached).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from typing import Dict, List, Optional

from app.services import implication as imp
from data_pipeline.common import api_get

BUCKET = os.getenv("DATALAKE_BUCKET", "kalshi-trade-datalake-568386354218-us-east-1")
PREFIX = "research-watch/implication"
PAUSE_S = float(os.getenv("TAU_IMPLICATION_PAUSE_S", "0.25"))   # ~4 req/s: the box also runs live pilots
MAX_CALLS = int(os.getenv("TAU_IMPLICATION_MAX_CALLS", "240"))   # map + audit; ~$3-5/day, cached clusters are free
KEEP = ("ticker", "event_ticker", "title", "yes_sub_title", "rules_primary", "close_time", "volume_fp",
        "volume", "last_price_dollars", "status")


def _get(path: str, params: Optional[Dict] = None) -> Dict:
    time.sleep(PAUSE_S)
    return api_get(path, params) or {}


def _s3():
    import boto3
    return boto3.client("s3")


def s3_get_json(key: str) -> Optional[Dict]:
    try:
        return json.loads(_s3().get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except Exception:
        return None


def s3_put_json(key: str, obj) -> None:
    _s3().put_object(Bucket=BUCKET, Key=key, Body=json.dumps(obj, default=str).encode(),
                     ContentType="application/json")


def news_events(max_pages: int = 600) -> List[Dict]:
    series = {}
    for s in _get("/series").get("series") or []:
        if imp.news_series(s.get("ticker"), s.get("category")):
            series[s["ticker"]] = s
    out, cursor = [], None
    for _ in range(max_pages):
        p = {"status": "open", "with_nested_markets": "true", "limit": 200}
        if cursor:
            p["cursor"] = cursor
        d = _get("/events", p)
        for e in d.get("events") or []:
            s = series.get(e.get("series_ticker"))
            if not s:
                continue
            mk = [{k: m.get(k) for k in KEEP} for m in e.get("markets") or []
                  if m.get("status") in (None, "active", "open")]
            if mk:
                out.append({"event_ticker": e.get("event_ticker"), "series_ticker": e.get("series_ticker"),
                            "title": e.get("title"), "series_title": s.get("title"), "category": s.get("category"),
                            "markets": mk})
        cursor = d.get("cursor")
        if not cursor:
            break
    return out


def build_map(max_calls: int, model=None, log=print) -> Dict:
    from app.adapters.llm import llm
    model = model or llm.claude
    events = news_events()
    cl = imp.clusters(events)
    old = (s3_get_json(PREFIX + "/map.json") or {}).get("clusters") or {}
    usage = llm.Usage()
    kept, calls, failed, rejected = {}, 0, 0, 0
    for c in cl:
        key = imp.cluster_key(c)
        if key in old and "proposed" in old[key]:   # only audited links carry over
            kept[key] = old[key]
            continue
        if calls >= max_calls:
            continue
        calls += 1
        try:
            proposed = llm.ask_json(model, imp.MAP_SYSTEM, [{"role": "user", "content": imp.map_prompt(c)}],
                                    lambda r, c=c: imp.check_edges(r, c), usage)
            edges = []
            if proposed:   # a second, skeptical read: only confirmed links are traded
                calls += 1
                edges = llm.ask_json(model, imp.VERIFY_SYSTEM,
                                     [{"role": "user", "content": imp.verify_prompt(proposed, c)}],
                                     lambda r, p=proposed: imp.check_verdicts(r, p), usage)
        except Exception as e:   # one bad cluster must not stop the map
            failed += 1
            log("[implication-map] cluster %s failed: %s" % (key, str(e)[:200]))
            continue
        rejected += len(proposed) - len(edges)
        kept[key] = {"tickers": [m["ticker"] for m in c], "edges": edges, "proposed": len(proposed),
                     "mapped_at": int(time.time())}
    edges = [e for v in kept.values() for e in v["edges"]]
    watch = sorted({e["if"] for e in edges})
    log("[implication-map] events %d clusters %d mapped %d calls %d failed %d edges %d (auditor rejected %d) watch %d"
        % (len(events), len(cl), len(kept), calls, failed, len(edges), rejected, len(watch)))
    return {"built_at": int(time.time()), "events": len(events), "clusters_found": len(cl),
            "clusters_mapped": len(kept), "llm_calls": calls, "llm_failed": failed, "auditor_rejected": rejected,
            "usage": getattr(usage, "__dict__", {}), "edges": edges, "watch": watch, "clusters": kept}


def market_result(ticker: str) -> Optional[str]:
    m = (_get("/markets/" + ticker) or {}).get("market") or {}
    r = m.get("result")
    return r if r in ("yes", "no") else None


def score(log=print) -> Dict:
    s3 = _s3()
    keys = [o["Key"] for p in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=PREFIX + "/triggers/")
            for o in p.get("Contents", [])]
    trig = [t for t in (s3_get_json(k) for k in keys) if t]
    prev = s3_get_json(PREFIX + "/latest.json") or {}
    results: Dict[str, str] = dict(prev.get("results") or {})
    for t in trig:
        for f in t.get("fills") or []:
            tk = f.get("then")
            if f.get("qty") and tk and tk not in results:
                r = market_result(tk)
                if r:
                    results[tk] = r
    summ = imp.summary(trig, results)
    frozen = prev.get("judgment")
    if not frozen and summ["verdict"] in ("pass", "kill"):
        frozen = dict(summ, judged_at=int(time.time()))   # ONE look: never re-judged
    rep = {"updated_at": int(time.time()), "summary": summ, "judgment": frozen, "results": results,
           "recent": sorted(trig, key=lambda t: -t.get("ts", 0))[:30]}
    log("[implication-map] score %s" % json.dumps(summ))
    return rep


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=MAX_CALLS)
    ap.add_argument("--score-only", action="store_true")
    ap.add_argument("--no-s3", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if not a.score_only:
        mp = build_map(a.max_calls)
        if a.out:
            with open(a.out, "w") as f:
                json.dump(mp, f, default=str)
        if not a.no_s3:
            s3_put_json(PREFIX + "/map.json", mp)
    if not a.no_s3:
        s3_put_json(PREFIX + "/latest.json", score())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
