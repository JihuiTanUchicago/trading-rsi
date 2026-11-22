"""The market-making research loop (paper only): the model proposes quoting policies, the replay
scores them on research hours, the model refines them, and one finalist is judged once on hours it
never saw.

    python -m app.maker_lab_run --series KXBTCD --hours 48 --holdout 16

Split: the newest ``holdout`` archived hours are held out; the ``hours`` before them are research.
The model sees research results only. Round 0 scores a few fixed baselines so it starts from
measured numbers; each later round it proposes ``per_round`` policies with the earlier results in
view. The finalist is the research policy with the highest hour-clustered t (positive mean, enough
fills), or none.

Gate (maker_policy.hour_stats rows, the hour as the unit, scored at pool 0 because no Bitcoin
series carries a rewards pool yet): at least MIN_HOURS active hours and MIN_FILLS fills, the
stationary-bootstrap one-sided p of the hourly markout mean at or below alpha, and a positive mean
after the maker fee Kalshi would charge if the series started charging makers (fee stress). alpha is
GATE_ALPHA divided by the number of finalists already judged on overlapping held-out hours (the
ledger), so rerunning the lab on the same window cannot buy a pass. The break-even pool is reported
either way: once Bitcoin pools post, a policy that loses a little and earns a big share can pay.

Forward test (Todd 2026-10-31: "produce profitable strategies as soon as possible"). One held-out pass
in ~10 judged finalists is about what chance gives, so a finalist whose held-out p is at most
FORWARD_MAX_P with a positive fee-stressed mean is judged ONCE more, frozen, on the FORWARD_HOURS that
follow its holdout window (hours no lab run had when it was picked), by the same judge() at GATE_ALPHA.
The rule is fixed before any forward hour is scored; a policy (series + levers) is judged forward at
most once, and forward verdicts live in their own ledger so they never split a holdout's alpha.

Confirmation (fixed 2026-11-16, after the first two forward passes, before any later hour was scored).
The forward judge scores 60-second markouts, and inventory held past 60 s is not scored. A forward pass
is judged ONCE more, frozen, on the CONFIRM_HOURS that follow its forward window. It passes only when
both of these hold:
- judge() passes at GATE_ALPHA divided by the number of forward passes so far.
- The summed trading P&L of those hours is positive. That P&L is each hour's book marked at its last
  mid, and the hourly series settle on the hour, so it is close to settled money.
Confirm verdicts live in their own ledger.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Sequence

from app.research import stats
from app.adapters.llm.llm import ModelFn, Usage, ask_json
from trading_core.engine import maker_policy as mr

GATE_ALPHA = 0.05
MIN_HOURS = 12
MIN_FILLS = 200
FORWARD_HOURS = 24
FORWARD_MAX_P = 0.20
CONFIRM_HOURS = 72
ROUND0 = ("join_touch", "spot_fair_2c", "spot_fair_2c_pull")

SYSTEM = """You are a quantitative researcher designing PASSIVE market-making policies for Kalshi Bitcoin
binary contracts. Every policy only rests buy orders (a YES bid and a NO bid, which is a YES offer) and
never crosses the spread. It is judged on a queue-aware replay of the real per-second order books:
our orders join the back of their price level and fill only when real prints eat the queue ahead.

How a policy is scored, per UTC hour:
- markout: for every fill, the mid 60 seconds later minus the price paid, times contracts, minus fees.
  This is the cost of adverse selection (informed takers hitting us before the price moves). It is the
  number to make positive. Inventory P&L past 60 s is outcome noise and is not scored.
- reward share: Kalshi pays a liquidity-rewards pool per market for resting size near the touch (both
  sides needed, size deeper than the touch discounted by half per cent). Shown as the pool-dollars the
  policy would earn at $1 per contract-hour. No Bitcoin series has a pool today; Kalshi has filed to
  allow pools of up to $10,000 a day per market from Oct 28. break-even pool = the pool per
  contract-hour at which the policy pays.
- statistics treat each hour as one observation; t = mean / standard error over hours.

Levers (all optional; omitted ones keep their defaults):
  series: KXBTC15M (15-minute up/down, one contract open at a time, very deep touch),
          KXBTCD (hourly above/below strikes, ~130 strikes), KXBTC (hourly ranges)
  quote_size (contracts per side, default 100), max_inventory (|YES - NO| cap, default 500)
  fair_source: "mid" (default), "micro" (book microprice), "spot" (probability implied by the
          cross-exchange BTC price and realized volatility; single-strike contracts only, falls back to mid)
  min_edge (dollars per contract a quote must sit below fair; default 0), back_off (true: a side short
          of min_edge rests at fair - min_edge instead of being dropped)
  offset_ticks (rest N cents behind the touch), improve_min_spread_ticks (step 1c inside a spread of
          at least N cents; 0 = never), skew_ticks (at full inventory, the side adding to it rests N
          cents further back, linear in fill level)
  mid_band [lo, hi] (quote only contracts whose mid is inside), pull_before_close_s (default 60),
          max_secs_to_close (skip contracts further from close)
  pull_move_bp + pull_cooldown_s + move_window_s (1, 10 or 60): pull a contract's quotes for the
          cooldown after BTC moves that many basis points over the window
  max_touch_size (skip a side whose touch queue is deeper), contract_loss_limit (bench a contract
          whose fills cost more than this many dollars; it then only unwinds)

Think about WHERE adverse selection comes from (spot moves the Kalshi book lags, the last minutes before
close, cheap tails) and which lever removes it while keeping fills. A policy that fills rarely cannot be
judged: it needs hundreds of fills over the hours."""


def _policy_hash(p: Dict) -> str:
    core = {k: v for k, v in p.items() if k not in ("name", "why")}
    return hashlib.sha1(json.dumps(core, sort_keys=True).encode()).hexdigest()[:12]


def research_line(name: str, policy: Dict, st: Dict) -> str:
    knobs = {k: v for k, v in policy.items() if k not in ("name", "why", "rewarded_only") and v != mr.DEFAULTS.get(k)}
    return ("%s %s: hours %s, fills %s, markout/contract %s, mean/hour $%s, t %s, reward share/hour %s, "
            "break-even pool $%s per contract-hour" % (
                name, json.dumps(knobs, sort_keys=True), st["hours"], st["fills"], st["markout_per_contract"],
                st["mean"], st["t"], st["reward_share_per_hour"], st["breakeven_pool"]))


def _check(series: str, n: int) -> Callable[[Dict], List[Dict]]:
    def check(obj: Dict) -> List[Dict]:
        raw = obj.get("policies")
        if not isinstance(raw, list) or not raw:
            raise ValueError('reply must be {"policies": [ ... ]}')
        out = []
        for i, p in enumerate(raw[:n]):
            if not isinstance(p, dict):
                raise ValueError("policy %d is not an object" % i)
            try:
                out.append(mr.validate_policy(dict(p, series=p.get("series", series))))
            except mr.PolicyError as e:
                raise ValueError("policy %d: %s" % (i, e))
        return out
    return check


def propose(model: ModelFn, usage: Usage, series: str, history: List[str], n: int,
            prior: Sequence[str] = ()) -> List[Dict]:
    earlier = ("Earlier lab runs on other hours (what was tried, how it did, and how its finalist fared "
               "on held-out hours):\n%s\n\n" % "\n".join(prior)) if prior else ""
    prompt = (earlier + "Research results so far (research hours only):\n%s\n\nPropose %d new policies for %s that "
              "should beat these. Each one changes something for a stated reason. Reply with JSON only: "
              '{"policies": [{"name": "...", "why": "one sentence", ...levers}]}' % (
                  "\n".join(history) or "(none yet)", n, series))
    return ask_json(model, SYSTEM, [{"role": "user", "content": prompt}], _check(series, n), usage)


def pick_finalist(results: Dict[str, Dict]) -> Optional[str]:
    best, best_t = None, 0.0
    for name, r in results.items():
        st = r["stats"]
        if st["t"] is None or (st["mean"] or 0) <= 0 or st["fills"] < MIN_FILLS:
            continue
        if st["t"] > best_t:
            best, best_t = name, st["t"]
    return best


def judge(rows: Sequence[Dict], alpha: float) -> Dict:
    """The gate on held-out hours (rows in time order), at pool 0."""
    active = [r for r in rows if r.get("fills") or r.get("reward")]
    vals = [r[mr.HORIZON] for r in active]
    fills = sum(r["fills"] for r in active)
    out = {"hours": len(active), "fills": fills, "alpha": alpha, "passed": False,
           "stats": mr.hour_stats(rows, 0.0)}
    if len(active) < MIN_HOURS or fills < MIN_FILLS:
        out["reason"] = "underpowered: %d active hours, %d fills (needs %d and %d)" % (
            len(active), fills, MIN_HOURS, MIN_FILLS)
        return out
    boot = stats.stationary_bootstrap_ci(vals, alpha=2 * alpha)
    stressed = sum(v - r.get("fee_stress", 0.0) for v, r in zip(vals, active)) / len(vals)
    out.update({"p_value": round(boot["p_value"], 4), "lo": round(boot["lo"], 2), "mean": round(boot["point"], 2),
                "fee_stressed_mean": round(stressed, 2)})
    if boot["p_value"] > alpha:
        out["reason"] = "not significant: p %.3f > %.4f" % (boot["p_value"], alpha)
    elif stressed <= 0:
        out["reason"] = "loses once makers pay Kalshi's fee: $%.2f an hour" % stressed
    else:
        out["passed"], out["reason"] = True, "passed"
    return out


class MemoryLedger:
    """Finalists judged per held-out window; the S3 one persists it (S3Ledger)."""

    def __init__(self, entries: Optional[List[Dict]] = None) -> None:
        self.entries = list(entries or [])

    def overlapping(self, series: str, start: int, end: int) -> int:
        return sum(1 for e in self.entries if e["series"] == series and e["start"] < end and start < e["end"])

    def record(self, entry: Dict) -> None:
        self.entries.append(entry)


class S3Ledger(MemoryLedger):
    KEY = "research-watch/maker/ledger.json"

    def __init__(self, s3, bucket: str) -> None:
        self.s3, self.bucket = s3, bucket
        try:
            body = s3.get_object(Bucket=bucket, Key=self.KEY)["Body"].read()
            entries = json.loads(body).get("entries") or []
        except Exception:  # noqa: BLE001 - first run: empty ledger
            entries = []
        super().__init__(entries)

    def record(self, entry: Dict) -> None:
        super().record(entry)
        self.s3.put_object(Bucket=self.bucket, Key=self.KEY, Body=json.dumps({"entries": self.entries}).encode())


class ForwardBook:
    """Forward verdicts, one per (series, policy hash); the S3 one persists them (S3ForwardBook)."""

    def __init__(self, entries: Optional[List[Dict]] = None) -> None:
        self.entries = list(entries or [])

    def judged(self, series: str, h: str) -> bool:
        return any(e["series"] == series and e["hash"] == h for e in self.entries)

    def record(self, entry: Dict) -> None:
        self.entries.append(entry)


class S3ForwardBook(ForwardBook):
    KEY = "research-watch/maker/forward.json"

    def __init__(self, s3, bucket: str) -> None:
        self.s3, self.bucket = s3, bucket
        try:
            entries = json.loads(s3.get_object(Bucket=bucket, Key=self.KEY)["Body"].read()).get("entries") or []
        except Exception:  # noqa: BLE001 - first run: nothing judged forward yet
            entries = []
        super().__init__(entries)

    def record(self, entry: Dict) -> None:
        super().record(entry)
        self.s3.put_object(Bucket=self.bucket, Key=self.KEY, Body=json.dumps({"entries": self.entries}).encode())


def forward_candidate(run: Dict) -> Optional[Dict]:
    """A finished lab run's finalist, frozen, with its forward window, when it qualifies; else None."""
    fin, v = run.get("finalist"), run.get("verdict") or {}
    if not fin or v.get("p_value") is None or v["p_value"] > FORWARD_MAX_P or (v.get("fee_stressed_mean") or 0) <= 0:
        return None
    start = int(run["holdout_hours"][-1]) + 3600
    r = run["results"][fin]
    return {"series": run["series"], "name": fin, "hash": r["hash"], "policy": r["policy"], "holdout_p": v["p_value"],
            "start": start, "end": start + FORWARD_HOURS * 3600}


def forward_due(runs: Sequence[Dict], book: ForwardBook, archived_to: datetime, limit: int) -> List[Dict]:
    """Candidates whose forward window has fully archived (hours before ``archived_to``) and that were
    never judged forward, oldest window first, at most ``limit``."""
    out: List[Dict] = []
    for c in sorted(filter(None, map(forward_candidate, runs)), key=lambda c: c["start"]):
        if c["end"] <= archived_to.timestamp() and not book.judged(c["series"], c["hash"]) and \
                not any(o["series"] == c["series"] and o["hash"] == c["hash"] for o in out):
            out.append(c)
    return out[:limit]


def judge_forward(c: Dict, book: ForwardBook, evaluate: Callable[..., List[Dict]], workers: int = 1,
                  log: Callable[[str], None] = print) -> Dict:
    hours = [datetime.fromtimestamp(t, timezone.utc) for t in range(c["start"], c["end"], 3600)]
    v = judge(evaluate(c["policy"], hours, workers=workers), GATE_ALPHA)
    entry = {k: c[k] for k in ("series", "name", "hash", "holdout_p", "start", "end")}
    entry.update({"passed": v["passed"], "reason": v["reason"], "p_value": v.get("p_value"), "mean": v.get("mean"),
                  "fee_stressed_mean": v.get("fee_stressed_mean"), "hours": v["hours"], "fills": v["fills"],
                  "markout_per_contract": v["stats"].get("markout_per_contract"), "at": int(time.time())})
    book.record(entry)
    log("forward %s %s on %d hours after its holdout: %s" % (c["series"], c["name"], len(hours), v["reason"]))
    return entry


class ConfirmBook(ForwardBook):
    """Confirm verdicts, one per (series, policy hash); the S3 one persists them (S3ConfirmBook)."""


class S3ConfirmBook(S3ForwardBook):
    KEY = "research-watch/maker/confirm.json"


def confirm_due(runs: Sequence[Dict], forward: ForwardBook, book: ForwardBook, archived_to: datetime,
                limit: int) -> List[Dict]:
    """Forward passes whose confirm window (the CONFIRM_HOURS after their forward window) has fully
    archived and that were never confirmed, oldest first, at most ``limit``. The frozen policy comes
    from the lab run that picked it (matched by series and levers hash)."""
    policies = {}
    for run in runs:
        for r in (run.get("results") or {}).values():
            if r.get("hash") and r.get("policy"):
                policies.setdefault((run.get("series"), r["hash"]), r["policy"])
    out: List[Dict] = []
    for e in sorted((e for e in forward.entries if e.get("passed")), key=lambda e: e["end"]):
        key = (e["series"], e["hash"])
        start = int(e["end"])
        end = start + CONFIRM_HOURS * 3600
        if key in policies and end <= archived_to.timestamp() and not book.judged(*key) and \
                not any((o["series"], o["hash"]) == key for o in out):
            out.append({"series": e["series"], "name": e["name"], "hash": e["hash"], "policy": policies[key],
                        "forward_p": e.get("p_value"), "start": start, "end": end})
    return out[:limit]


def judge_confirm(c: Dict, forward: ForwardBook, book: ForwardBook, evaluate: Callable[..., List[Dict]],
                  workers: int = 1, log: Callable[[str], None] = print) -> Dict:
    hours = [datetime.fromtimestamp(t, timezone.utc) for t in range(c["start"], c["end"], 3600)]
    rows = evaluate(c["policy"], hours, workers=workers)
    alpha = GATE_ALPHA / max(1, sum(1 for e in forward.entries if e.get("passed")))
    v = judge(rows, alpha)
    pnl = [float(r.get("trading_pnl") or 0.0) for r in rows]
    settled = round(sum(pnl), 2)
    passed, reason = v["passed"], v["reason"]
    if passed and settled <= 0:
        passed, reason = False, "markouts pass but the held inventory lost: trading P&L $%.2f" % settled
    entry = {k: c[k] for k in ("series", "name", "hash", "forward_p", "start", "end")}
    entry.update({"passed": passed, "reason": reason, "alpha": alpha, "p_value": v.get("p_value"),
                  "mean": v.get("mean"), "fee_stressed_mean": v.get("fee_stressed_mean"), "hours": v["hours"],
                  "fills": v["fills"], "markout_per_contract": v["stats"].get("markout_per_contract"),
                  "trading_pnl": settled, "losing_hours": sum(1 for x in pnl if x < 0),
                  "worst_hour": round(min(pnl), 2) if pnl else None,
                  "peak_collateral": max((r.get("peak_collateral") or 0.0 for r in rows), default=0.0),
                  "at": int(time.time())})
    book.record(entry)
    log("confirm %s %s on %d hours after its forward window: %s" % (c["series"], c["name"], len(hours), reason))
    return entry


# --- settled-profit tracking of confirmed policies ----------------------------------------------------
# Paper size (inventory in the hundreds) makes an hour's settled P&L mostly inventory noise: r5.1's
# 78 hours after its confirm made +$819 of 60s markouts but -$128 settled. A confirmed policy is
# therefore also scored every hour after its confirm window at the size we could actually trade,
# on settled P&L, which is the number a live account sees. Read-only: it gates nothing.
TRACK_SIZES = (10.0,)


class TrackBook:
    """Scored hours per (series, hash, size): {"tracks": {"<series>|<hash>|<size>": {...}}}."""

    def __init__(self, doc: Optional[Dict] = None) -> None:
        self.doc = doc or {"tracks": {}}

    def save(self) -> None:
        pass


class S3TrackBook(TrackBook):
    KEY = "research-watch/maker/track.json"

    def __init__(self, s3, bucket: str) -> None:
        self.s3, self.bucket = s3, bucket
        try:
            doc = json.loads(s3.get_object(Bucket=bucket, Key=self.KEY)["Body"].read())
        except Exception:  # noqa: BLE001 - first run
            doc = None
        super().__init__(doc)

    def save(self) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=self.KEY, Body=json.dumps(self.doc, default=str).encode())


def track_summary(rows: Sequence[Dict]) -> Dict:
    pnl = [float(r.get("trading_pnl") or 0.0) for r in rows]
    m60 = [float(r.get("m60") or 0.0) for r in rows]
    qty = sum(float(r.get("qty") or 0.0) for r in rows)

    def t(x):
        if len(x) < 2:
            return None
        m = sum(x) / len(x)
        sd = math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))
        return round(m / (sd / math.sqrt(len(x))), 2) if sd > 0 else None
    return {"hours": len(rows), "trading_pnl": round(sum(pnl), 2), "t_settled": t(pnl), "m60": round(sum(m60), 2),
            "t_m60": t(m60), "qty": round(qty, 1),
            "settled_c_per_contract": round(100 * sum(pnl) / qty, 3) if qty else None,
            "losing_hours": sum(1 for x in pnl if x < 0), "worst_hour": round(min(pnl), 2) if pnl else None,
            "peak_collateral": max((float(r.get("peak_collateral") or 0.0) for r in rows), default=0.0)}


def track(runs: Sequence[Dict], confirm: ForwardBook, book: TrackBook, evaluate: Callable[..., List[Dict]],
          archived_to: datetime, limit: int, workers: int = 1, log: Callable[[str], None] = print) -> List[Dict]:
    """Score up to ``limit`` not-yet-scored archived hours per confirmed policy and size, oldest first."""
    policies = {}
    for run in runs:
        for r in (run.get("results") or {}).values():
            if r.get("hash") and r.get("policy"):
                policies.setdefault((run.get("series"), r["hash"]), r["policy"])
    out = []
    for e in (e for e in confirm.entries if e.get("passed")):
        policy = policies.get((e["series"], e["hash"]))
        if policy is None:
            continue
        for size in TRACK_SIZES:
            key = "%s|%s|%g" % (e["series"], e["hash"], size)
            tr = book.doc["tracks"].setdefault(key, {"series": e["series"], "name": e["name"], "hash": e["hash"],
                                                     "size": size, "start": int(e["end"]), "hours": {}})
            todo = [t for t in range(tr["start"], int(archived_to.timestamp()), 3600)
                    if str(t) not in tr["hours"]][:limit]
            if not todo:
                continue
            sized = dict(policy, quote_size=size, max_inventory=size)
            rows = evaluate(sized, [datetime.fromtimestamp(t, timezone.utc) for t in todo], workers=workers)
            for t, r in zip(todo, rows):
                tr["hours"][str(t)] = r
            tr["summary"] = track_summary([tr["hours"][k] for k in sorted(tr["hours"], key=int)])
            tr["at"] = int(time.time())
            book.save()
            log("track %s %s size %g: +%d hours, %s" % (e["series"], e["name"], size, len(todo), tr["summary"]))
            out.append({"key": key, "added": len(todo), "summary": tr["summary"]})
    return out


def run_lab(series: str, research: Sequence[datetime], holdout: Sequence[datetime], model: ModelFn, usage: Usage,
            ledger: MemoryLedger, evaluate: Callable[..., List[Dict]], rounds: int = 3, per_round: int = 4,
            workers: int = 1, log: Callable[[str], None] = print, prior: Sequence[str] = ()) -> Dict:
    """``evaluate(policy, hours, workers=)`` -> one row per hour (app.adapters.sim.maker_eval.evaluate).
    ``prior``: summary lines of earlier runs (prior_lines), so each run builds on the last."""
    t0 = time.monotonic()
    results: Dict[str, Dict] = {}
    history: List[str] = []

    def score(name: str, policy: Dict) -> None:
        h = _policy_hash(policy)
        if any(r["hash"] == h for r in results.values()):
            log("skip %s: same levers as an earlier policy" % name)
            return
        rows = evaluate(policy, research, workers=workers)
        st = mr.hour_stats(rows, 0.0)
        results[name] = {"policy": policy, "hash": h, "rows": rows, "stats": st}
        history.append(research_line(name, policy, st))
        log(history[-1])

    for name in ROUND0:
        score(name, mr.validate_policy(dict(mr.BASELINES[name], series=series)))
    for rnd in range(1, rounds + 1):
        for i, p in enumerate(propose(model, usage, series, history, per_round, prior)):
            name = "r%d.%d %s" % (rnd, i + 1, (p.get("name") or "policy")[:40])
            score(name, p)
    fin = pick_finalist(results)
    out = {"series": series, "research_hours": [int(h.timestamp()) for h in research],
           "holdout_hours": [int(h.timestamp()) for h in holdout], "results": results, "finalist": fin,
           "verdict": None, "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}}
    if fin is None:
        log("no finalist: no research policy has a positive markout mean with %d+ fills" % MIN_FILLS)
    else:
        start = int(holdout[0].timestamp())
        end = int(holdout[-1].timestamp()) + 3600
        prior = ledger.overlapping(series, start, end)
        alpha = GATE_ALPHA / (prior + 1)
        rows = evaluate(results[fin]["policy"], holdout, workers=workers)
        v = judge(rows, alpha)
        v["rows"] = rows
        out["verdict"] = v
        ledger.record({"series": series, "start": start, "end": end, "hash": results[fin]["hash"],
                       "passed": v["passed"], "at": int(time.time())})
        log("finalist %s on %d held-out hours: %s" % (fin, len(holdout), v["reason"]))
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out


def summary_lines(run: Dict, top: int = 3) -> List[str]:
    """A finished run in a few lines for later runs' prompts: its best research policies and the verdict."""
    res = run.get("results") or {}
    ranked = sorted(res.items(), key=lambda kv: -(kv[1]["stats"].get("t") or -99))[:top]
    when = datetime.fromtimestamp(run["holdout_hours"][0], timezone.utc).strftime("%Y-%m-%d %H:00Z") if run.get("holdout_hours") else "?"
    out = ["- run held out from %s:" % when]
    out += ["  " + research_line(name, r["policy"], r["stats"]) for name, r in ranked]
    v = run.get("verdict")
    out.append("  finalist %s: %s" % (run.get("finalist"), (v or {}).get("reason", "none judged")) +
               ("" if not v else " (held-out mean/hour $%s, markout/contract %s)" % (
                   (v.get("stats") or {}).get("mean"), (v.get("stats") or {}).get("markout_per_contract"))))
    return out


def split(end: datetime, hours: int, holdout: int):
    end = end.replace(minute=0, second=0, microsecond=0)
    allh = [end - timedelta(hours=k) for k in range(hours + holdout, 0, -1)]
    return allh[:hours], allh[hours:]
