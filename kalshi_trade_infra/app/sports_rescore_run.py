"""Re-score the sports ideas that reached the gate before the close-time look-ahead fix (PR #839).

Read-only and diagnostic: for every experiment of the given research runs that has a validation
result, replay its frozen spec on its own frozen research and validation windows twice, with each
contract timed against its own settled close (the leak) and against its event's last close (the
fix), and write both next to the stored verdict. Nothing is written to the database, no gate is
re-run, and the holdout is never loaded.

    python -m app.sports_rescore_run --runs 2622,2646       # result: s3://<datalake>/research-watch/dumps/
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import traceback
from datetime import datetime, timezone
from typing import Dict, List

from app.research import partitions
from app.research import research_repository as rr
from app.trading import backtest

BUCKET = os.getenv("DATALAKE_BUCKET", "kalshi-trade-datalake-568386354218-us-east-1")
DEFAULT_RUNS = "2622,2646"


def summarize(trades) -> Dict:
    """n, net P&L, mean return per dollar staked, and the plain t of per-trade returns."""
    rets = [t.pnl / max(t.qty * t.entry_price + t.fees, 1e-9) for t in trades]
    n = len(rets)
    if not n:
        return {"n": 0}
    mean = sum(rets) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1)) if n > 1 else 0.0
    return {"n": n, "net_pnl": round(sum(t.pnl for t in trades), 2), "mean_ret": round(mean, 4),
            "t": round(mean / (sd / math.sqrt(n)), 2) if sd > 0 else None,
            "win_rate": round(sum(1 for t in trades if t.pnl > 0) / n, 3)}


def replay(spec: Dict, market: str, part: Dict, leaky: bool) -> Dict:
    real = backtest.outcome_event_closes
    if leaky:
        backtest.outcome_event_closes = lambda markets: {}
    try:
        rds = partitions.research_dataset(market, part)
        vds = partitions.validation_dataset(market, part)
    finally:
        backtest.outcome_event_closes = real
    return {"research": summarize(backtest.research_fold_trades(spec, rds, int(part["embargo_seconds"]))),
            "validation": summarize(backtest.simulate(spec, vds, vds.start_ts, vds.end_ts + 1))}


def run(cur, run_ids: List[int], log=print) -> Dict:
    cur.execute("SELECT id, user_id, run_id, market, spec_json, research_partition_id FROM app_experiments "
                "WHERE run_id IN (%s) ORDER BY id" % ",".join(["%s"] * len(run_ids)), tuple(run_ids))
    out = []
    for e in cur.fetchall():
        stored = [r for r in rr.results_for_experiment(cur, int(e["id"])) if r.get("phase") == "validation"]
        if not stored:
            continue      # never reached the gate
        rec = {"experiment_id": e["id"], "run_id": e["run_id"], "user_id": e["user_id"], "market": e["market"],
               "stored_validation": {k: stored[-1].get(k) for k in ("classification", "n_trades", "net_pnl",
                                                                      "mean_net_return", "p_value")}}
        try:
            spec = e["spec_json"] if isinstance(e["spec_json"], dict) else json.loads(e["spec_json"])
            rec["title"] = (spec.get("name") or spec.get("title") or "")[:120]
            part = rr.partition_by_id(cur, int(e["research_partition_id"]))
            rec["leaky"] = replay(spec, e["market"], part, leaky=True)
            rec["fixed"] = replay(spec, e["market"], part, leaky=False)
        except Exception:  # noqa: BLE001 - one bad replay must not hide the others
            rec["error"] = traceback.format_exc()[-1200:]
        log("[rescore] exp %s %s: %s" % (e["id"], e["market"], json.dumps({k: rec.get(k) for k in ("leaky", "fixed")},
                                                                         default=str)))
        out.append(rec)
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "runs": run_ids, "experiments": out}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", default=DEFAULT_RUNS)
    ap.add_argument("--no-s3", action="store_true")
    a = ap.parse_args()
    from data_pipeline.common import get_db_conn
    conn = get_db_conn()
    try:
        report = run(conn.cursor(), [int(x) for x in a.runs.split(",") if x.strip()])
    finally:
        conn.close()
    if not a.no_s3:
        import boto3
        key = "research-watch/dumps/sports_rescore_%s.json.gz" % report["generated_at"][:16].replace(":", "")
        boto3.client("s3").put_object(Bucket=BUCKET, Key=key,
                                      Body=gzip.compress(json.dumps(report, default=str).encode()))
        print("[rescore] wrote s3://%s/%s" % (BUCKET, key), flush=True)


if __name__ == "__main__":
    main()
