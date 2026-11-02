"""Run the maker vs taker base-rate scan (app.services.maker_scan) over weather and sports history.

No model, no orders: reads hist_markets + market_candles inside each market's RESEARCH window and
writes s3://<datalake>/research-watch/maker-scan/latest.json plus a dated copy.

    python -m app.maker_scan_run                     # every weather and sports market in the catalog
    python -m app.maker_scan_run --markets high_ny,nba_game --no-s3

Weather daily highs (and lows) pool every city into one family clustered by settlement date; each
sports series is its own family clustered by event.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.adapters.db import candle_range_repository as crr
from app.research import research_repository as rr
from app.services import maker_scan
from app.trading.markets import MARKETS, Market
from app.trading.settlement import dataset_result
from data_pipeline.kalshi_fees import maker_fee_dollars, taker_fee_dollars

BUCKET = os.getenv("DATALAKE_BUCKET", "kalshi-trade-datalake-568386354218-us-east-1")
PREFIX = "research-watch/maker-scan"
RESEARCH_FRAC = 0.60                 # partitions.RESEARCH_FRAC, for a market with no frozen partition
MAX_CONTRACTS = {"weather": 1500, "sports": 1200}   # newest contracts per market inside its window
DEADLINE_S = float(os.getenv("TAU_MAKER_SCAN_DEADLINE_S", str(100 * 60)))
CATEGORIES = ("Sports", "Climate and Weather")


def family_of(m: Market) -> Tuple[str, bool]:
    """(family, pooled across series)."""
    s = (m.series or "").upper()
    if s.startswith("KXHIGH"):
        return "weather-high", True
    if s.startswith("KXLOWT"):
        return "weather-low", True
    return m.key, False


def scan_markets(keys: Optional[List[str]] = None) -> List[Market]:
    if keys:
        return [MARKETS[k] for k in keys if k in MARKETS]
    ms = [m for m in MARKETS.values() if m.category in CATEGORIES and m.underlying is None and m.kind == "binary"]
    # Weather first: pooled across cities it is the family with the most history (back to 2021).
    return sorted(ms, key=lambda m: (not family_of(m)[1], m.key))


VALIDATION_FRAC = 0.25               # partitions.VALIDATION_FRAC (the holdout keeps the newest 0.15)

# Pre-registered 2026-11-02 10:30Z from the 09:43Z research-window scan (the only cells that survived
# BH plus the tail test, before the void fix dropped kxuslgame). Judged once on the validation slice,
# Bonferroni over the three; the holdout is never read.
CONFIRM_CELLS = [
    {"family": "weather-low", "side": "no", "mode": "maker", "price": "35-65c", "ttc": "3-12h"},
    {"family": "kxnflfirsttd", "side": "no", "mode": "maker", "price": "65-85c", "ttc": "any time"},
    {"family": "kxnflwinmargin", "side": "no", "mode": "maker", "price": "35-65c", "ttc": "any time"},
]
CONFIRM_ALPHA = 0.05

# 2026-11-02 10:43Z validation: only weather-low passed (+3.96c, 89 dates, p 0.012 < 0.0167). Its one
# holdout look, at alpha 0.05; refused if a holdout report already exists (one look, ever).
HOLDOUT_CELLS = [CONFIRM_CELLS[0]]


def _from_ts(v) -> datetime:
    return datetime.fromtimestamp(int(v), tz=timezone.utc).replace(tzinfo=None)


def research_window(cur, m: Market, now: datetime) -> Tuple[datetime, datetime]:
    """The market's frozen research slice, else the oldest RESEARCH_FRAC of its history window."""
    p = rr.get_active_partition(cur, m.key)
    if p and p.get("research_from_ts") and p.get("research_to_ts"):
        return _from_ts(p["research_from_ts"]), _from_ts(p["research_to_ts"])
    start = now - timedelta(days=m.history_days)
    return start, start + (now - start) * RESEARCH_FRAC


def holdout_window(cur, m: Market, now: datetime) -> Tuple[datetime, datetime]:
    """The market's frozen holdout slice, else the newest share after research and validation."""
    p = rr.get_active_partition(cur, m.key)
    if p and p.get("holdout_from_ts") and p.get("holdout_to_ts"):
        return _from_ts(p["holdout_from_ts"]), _from_ts(p["holdout_to_ts"])
    start = now - timedelta(days=m.history_days)
    return start + (now - start) * (RESEARCH_FRAC + VALIDATION_FRAC), now


def validation_window(cur, m: Market, now: datetime) -> Tuple[datetime, datetime]:
    """The market's frozen validation slice, else the VALIDATION_FRAC after the research share.
    Never the holdout."""
    p = rr.get_active_partition(cur, m.key)
    if p and p.get("validation_from_ts") and p.get("validation_to_ts"):
        return _from_ts(p["validation_from_ts"]), _from_ts(p["validation_to_ts"])
    start = now - timedelta(days=m.history_days)
    return start + (now - start) * RESEARCH_FRAC, start + (now - start) * (RESEARCH_FRAC + VALIDATION_FRAC)


def _ts(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def run(cur, markets: List[Market], log=print, deadline_s: float = DEADLINE_S, window=None) -> Dict:
    window = window or research_window
    t0 = time.time()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    scan = maker_scan.Scan()
    done, skipped = [], []
    for m in markets:
        if time.time() - t0 > deadline_s:
            skipped.append(m.key)
            continue
        fam, pooled = family_of(m)
        since, until = window(cur, m, now)
        mid = _ts(since) + (_ts(until) - _ts(since)) // 2
        rows = crr.settled_contracts(cur, m.series, since, until)
        cap = MAX_CONTRACTS["weather" if pooled else "sports"]
        rows = rows[-cap:]
        n = 0
        for r in rows:
            if time.time() - t0 > deadline_s:
                break
            close_ts = _ts(r["close_time"])
            bars = [maker_scan.Bar(**b) for b in crr.candle_ranges(cur, r["ticker"], m.candle_minutes)]
            if len(bars) < 2:
                continue
            # Weather closes on schedule; sports close when the outcome is decided, so their
            # settled close_time must not pick the time band (maker_scan.contract_fills).
            fills = maker_scan.contract_fills(bars, close_ts, m.candle_minutes * 60, dataset_result(r),
                                              maker_fee_dollars, taker_fee_dollars, ttc_known=pooled)
            if not fills:
                continue
            cluster = r["close_time"].strftime("%Y-%m-%d") if pooled else (r.get("event_ticker")
                                                                           or r["ticker"].rsplit("-", 1)[0])
            scan.add(fam, cluster, 0 if close_ts < mid else 1, fills, r["ticker"])
            n += 1
        done.append({"market": m.key, "family": fam, "contracts": n,
                     "window": [since.isoformat()[:10], until.isoformat()[:10]]})
        log("[maker-scan] %s: %d contracts (%s to %s), %.0fs" % (m.key, n, since.date(), until.date(), time.time() - t0))
    report = scan.finish()
    report["_scan"] = scan
    report.update({"generated_at": datetime.now(timezone.utc).isoformat(), "markets": done,
                   "skipped_for_time": skipped, "seconds": round(time.time() - t0)})
    return report


def confirm(cur, log=print, cells: Optional[List[Dict]] = None, window=None, label: str = "validation",
            alpha: Optional[float] = None) -> Dict:
    """Run pre-registered cells (default CONFIRM_CELLS on each market's validation slice) and judge
    each once: confirmed when its mean is positive and its p (cluster t, tail test for lopsided
    payoffs) is under alpha / number of cells."""
    cells = cells or CONFIRM_CELLS
    fams = {c["family"] for c in cells}
    markets = [m for m in scan_markets() if family_of(m)[0] in fams]
    rep = run(cur, markets, log=log, window=window or validation_window)
    rows = rep.pop("_scan").rows_for(cells)
    bar = (CONFIRM_ALPHA if alpha is None else alpha) / len(cells)
    for r in rows:
        r["confirmed"] = bool(r.get("contracts") and r.get("mean_c", 0) > 0 and r.get("p", 1.0) < bar)
    return {"generated_at": rep["generated_at"], "window": label, "alpha_each": bar, "cells": rows,
            "confirmed": sum(r["confirmed"] for r in rows), "markets": rep["markets"],
            "skipped_for_time": rep["skipped_for_time"], "seconds": rep["seconds"]}


def holdout_already_read(s3) -> bool:
    return bool(s3.list_objects_v2(Bucket=BUCKET, Prefix=PREFIX + "/holdout/", MaxKeys=1).get("KeyCount"))


def publish(report: Dict, s3=None, prefix: str = PREFIX) -> str:
    if s3 is None:
        import boto3
        s3 = boto3.client("s3")
    report = {k: v for k, v in report.items() if not k.startswith("_")}
    body = json.dumps(report, default=str, separators=(",", ":")).encode()
    ts = report["generated_at"][:16].replace(":", "")
    key = "%s/date=%s/%s.json" % (prefix, ts[:10], ts[11:])
    for k in (key, prefix + "/latest.json"):
        s3.put_object(Bucket=BUCKET, Key=k, Body=body, ContentType="application/json")
    return key


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--markets", default="", help="comma-separated market keys (default: all weather + sports)")
    ap.add_argument("--no-s3", action="store_true")
    ap.add_argument("--confirm", action="store_true",
                    help="judge the pre-registered CONFIRM_CELLS once on the validation slice")
    ap.add_argument("--holdout", action="store_true",
                    help="the one holdout look at HOLDOUT_CELLS (refused if one was already published)")
    a = ap.parse_args()
    from data_pipeline.common import get_db_conn
    if a.holdout:
        import boto3
        s3 = boto3.client("s3")
        if holdout_already_read(s3):
            print("[maker-scan] holdout already read once; refusing a second look", flush=True)
            return
        conn = get_db_conn()
        try:
            rep = confirm(conn.cursor(), cells=HOLDOUT_CELLS, window=holdout_window, label="holdout")
        finally:
            conn.close()
        print(json.dumps(rep["cells"], default=str), flush=True)
        print("[maker-scan] wrote s3://%s/%s" % (BUCKET, publish(rep, s3=s3, prefix=PREFIX + "/holdout")), flush=True)
        return
    if a.confirm:
        conn = get_db_conn()
        try:
            rep = confirm(conn.cursor())
        finally:
            conn.close()
        print(json.dumps(rep["cells"], default=str), flush=True)
        if not a.no_s3:
            print("[maker-scan] wrote s3://%s/%s" % (BUCKET, publish(rep, prefix=PREFIX + "/confirm")), flush=True)
        return
    markets = scan_markets([k for k in a.markets.split(",") if k] or None)
    conn = get_db_conn()
    try:
        report = run(conn.cursor(), markets)
    finally:
        conn.close()
    for line in maker_scan.summary_lines(report):
        print(line, flush=True)
    if not a.no_s3:
        print("[maker-scan] wrote s3://%s/%s" % (BUCKET, publish(report)), flush=True)


if __name__ == "__main__":
    main()
