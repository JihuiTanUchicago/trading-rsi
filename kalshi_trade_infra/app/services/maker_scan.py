"""Maker vs taker base rates on weather and sports history: where, if anywhere, a resting order earns.

Research can only test taker entries (backtest: buys pay the next bar's ask), and taker bets on
these markets lose even before costs (2026-10-26 audit, -1.4%/$ gross; 63% of 848 ideas on 10-31/11-01
died as "loses"/"no_signal"). Published Kalshi studies find the money goes to makers. This scan
measures that on our own candle history, at no model cost, before anyone builds maker strategies:

  for every contract, side (YES / NO), mode (maker / taker), order-price band and time-to-close
  band, the FIRST fill in that cell, held to settlement, net of Kalshi's fee and the gate's 1c
  cost stress.

Fill rules, pessimistic on purpose so adverse selection is counted, not assumed away:

* maker YES: a bid resting at the bar's best YES bid p fills on the NEXT bar only if that bar
  traded THROUGH p (YES ask low < p, or a trade printed below p): every order at p was filled
  before the book could go there. A NO bid at 1 - ask fills when the YES bid high or a trade
  went above the ask. A fill at exactly p (queue position unknown) does not count.
* taker: lifts the NEXT bar's ask (YES) or hits its bid (NO), as backtest.simulate does.

Contracts that settle together are one bet: pooled weather families cluster by settlement date
(cities share weather), single series by event. The mean and SE are taken across clusters
(ratio estimator), a cell needs MIN_CLUSTERS of them, and Benjamini-Hochberg at FDR over all
tested cells marks the survivors.

Integrity: callers pass the RESEARCH window only (the frozen partition's research slice, or the
oldest 60% when none is frozen). Validation and holdout stay unseen, so a maker idea built on a
lead here is still judged on data nobody looked at.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

PRICE_BANDS = (0.0, 0.05, 0.15, 0.35, 0.65, 0.85, 0.95, 1.0001)
TTC_BANDS_H = (0.0, 1.0, 3.0, 12.0, 48.0, float("inf"))
SIDES = ("yes", "no")
MODES = ("maker", "taker")
COST_STRESS = 0.01      # the gate's per-contract cost stress, in dollars
FEE_QTY = 10            # Kalshi rounds the fee up per order; amortize it over a typical order
MIN_CLUSTERS = 30
FDR = 0.10
MAX_ENTRY = 0.98        # backtest.MAX_ENTRY_ASK: a 99c buy can never profit after the fee
SHOW = 12
EPS = 1e-9


@dataclass
class Bar:
    """One candle: closing YES bid/ask and the period's YES ask low, YES bid high and trade range."""
    end_ts: int
    bid: Optional[float]
    ask: Optional[float]
    ask_low: Optional[float] = None
    bid_high: Optional[float] = None
    trade_low: Optional[float] = None
    trade_high: Optional[float] = None


def band(x: float, edges: Sequence[float]) -> int:
    for i in range(len(edges) - 1):
        if edges[i] <= x < edges[i + 1]:
            return i
    return len(edges) - 2


def price_label(i: int) -> str:
    lo, hi = PRICE_BANDS[i], min(PRICE_BANDS[i + 1], 1.0)
    return "%d-%dc" % (round(lo * 100), round(hi * 100))


def ttc_label(i: int) -> str:
    lo, hi = TTC_BANDS_H[i], TTC_BANDS_H[i + 1]
    return ">%gh" % lo if math.isinf(hi) else "%g-%gh" % (lo, hi)


def _payout(result, side: str) -> Optional[float]:
    if result in ("yes", "no"):
        return 1.0 if result == side else 0.0
    if isinstance(result, (int, float)):
        return float(result) if side == "yes" else 1.0 - float(result)
    return None


def _maker_fill(side: str, post: Bar, nxt: Bar) -> Optional[float]:
    """The price a maker order posted on `post` fills at on `nxt`, or None."""
    if side == "yes":
        p = post.bid
        through = ((nxt.ask_low is not None and nxt.ask_low < p - EPS)
                   or (nxt.trade_low is not None and nxt.trade_low < p - EPS))
        return p if through else None
    a = post.ask
    through = ((nxt.bid_high is not None and nxt.bid_high > a + EPS)
               or (nxt.trade_high is not None and nxt.trade_high > a + EPS))
    return 1.0 - a if through else None


def contract_fills(bars: Sequence[Bar], close_ts: int, period_s: int, result,
                   maker_fee: Callable[[int, float], float],
                   taker_fee: Callable[[int, float], float]) -> Dict[Tuple, Tuple[float, int]]:
    """{(side, mode, price band, ttc band): (net P&L per contract after the cost stress, bars the
    order was posted)} for one contract's first fill in each cell. A maker cell that was posted
    but never filled appears with P&L None so fill rates can be counted."""
    out: Dict[Tuple, Tuple[Optional[float], int]] = {}
    filled = set()
    gap = 2 * max(period_s, 1)
    for i in range(len(bars) - 1):
        b, n = bars[i], bars[i + 1]
        if b.bid is None or b.ask is None or not (0.0 < b.bid < b.ask < 1.0):
            continue
        if n.end_ts - b.end_ts > gap or n.end_ts > close_ts:
            continue
        tb = band(max(0.0, (close_ts - b.end_ts) / 3600.0), TTC_BANDS_H)
        for side in SIDES:
            pay = _payout(result, side)
            if pay is None:
                continue
            # maker: post at this bar's best price for the side
            post = b.bid if side == "yes" else 1.0 - b.ask
            key = (side, "maker", band(post, PRICE_BANDS), tb)
            if key not in filled:
                px = _maker_fill(side, b, n)
                prev = out.get(key, (None, 0))
                if px is None:
                    out[key] = (None, prev[1] + 1)
                else:
                    out[key] = (pay - px - maker_fee(FEE_QTY, px) / FEE_QTY - COST_STRESS, prev[1] + 1)
                    filled.add(key)
            # taker: signal on this bar's quote, fill at the next bar's
            sig = b.ask if side == "yes" else 1.0 - b.bid
            key = (side, "taker", band(sig, PRICE_BANDS), tb)
            if key in filled:
                continue
            if n.bid is None or n.ask is None:
                continue
            px = n.ask if side == "yes" else 1.0 - n.bid
            if not 0.0 < px <= MAX_ENTRY:
                continue
            out[key] = (pay - px - taker_fee(FEE_QTY, px) / FEE_QTY - COST_STRESS, 1)
            filled.add(key)
    return out


@dataclass
class _Cell:
    clusters: Dict[str, List[float]] = field(default_factory=lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))
    posted: int = 0          # contracts the order was posted on (maker)
    halves: List[List[float]] = field(default_factory=lambda: [[0.0, 0], [0.0, 0]])


class Scan:
    """Accumulates contract_fills across contracts; finish() gives the table."""

    def __init__(self):
        self.cells: Dict[Tuple, _Cell] = defaultdict(_Cell)
        self.contracts = 0

    def add(self, family: str, cluster: str, half: int, fills: Dict[Tuple, Tuple]) -> None:
        """One contract's fills. `half` (0/1) is which half of its market's research window it
        closed in: a lead must be positive in both, so one lucky stretch can't carry it."""
        self.contracts += 1
        for key, (pnl, _posts) in fills.items():
            c = self.cells[(family,) + key]
            c.posted += 1
            if pnl is None:
                continue
            row = c.clusters[cluster]
            row[0] += pnl
            row[1] += 1
            row[2] += pnl * pnl
            c.halves[half][0] += pnl
            c.halves[half][1] += 1

    def finish(self) -> Dict:
        rows = []
        for (fam, side, mode, pb, tb), c in self.cells.items():
            xs = list(c.clusters.values())
            n = sum(x[1] for x in xs)
            k = len(xs)
            if n == 0:
                continue
            mean = sum(x[0] for x in xs) / n
            if k >= 2:
                var = sum((x[0] - mean * x[1]) ** 2 for x in xs) * k / (k - 1) / (n * n)
                se = math.sqrt(var) if var > 0 else float("inf")
            else:
                se = float("inf")
            t = mean / se if se and not math.isinf(se) else 0.0
            p = _p_one_sided(t, k - 1)
            halves = [round(100 * h[0] / h[1], 2) if h[1] else None for h in c.halves]
            rows.append({
                "family": fam, "side": side, "mode": mode,
                "price": price_label(pb), "ttc": ttc_label(tb),
                "contracts": n, "clusters": k,
                "fill_rate": round(n / c.posted, 3) if mode == "maker" and c.posted else None,
                "mean_c": round(100 * mean, 2), "t": round(t, 2), "p": p,
                "halves_c": halves, "testable": k >= MIN_CLUSTERS,
            })
        tested = [r for r in rows if r["testable"]]
        keep = _bh([r["p"] for r in tested], FDR)
        for r, k in zip(tested, keep):
            r["lead"] = bool(k and r["mean_c"] > 0 and all(h is None or h > 0 for h in r["halves_c"]))
        leads = sorted((r for r in tested if r.get("lead")), key=lambda r: -r["t"])
        return {
            "contracts": self.contracts,
            "cells": len(rows),
            "tested": len(tested),
            "leads": leads[:SHOW],
            "lead_count": len(leads),
            "by_mode": _by_mode(tested),
            "best": sorted(tested, key=lambda r: -r["t"])[:SHOW],
        }


def _p_one_sided(t: float, df: int) -> float:
    """P(T >= t) under Student's t with `df` (cluster count - 1): with 30-60 clusters the normal
    tail is noticeably too thin. Falls back to the normal without scipy."""
    try:
        from scipy.stats import t as student
        return float(student.sf(t, max(df, 1)))
    except Exception:  # noqa: BLE001 - scipy is optional on a box
        return 0.5 * math.erfc(t / math.sqrt(2.0))


def _bh(pvalues: List[float], q: float) -> List[bool]:
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    k = 0
    for rank, i in enumerate(order, start=1):
        if pvalues[i] <= rank / m * q:
            k = rank
    keep = [False] * m
    for rank, i in enumerate(order, start=1):
        keep[i] = rank <= k
    return keep


def _by_mode(rows: Iterable[Dict]) -> Dict:
    """Per family and mode: contracts-weighted mean P&L (cents per contract) over every tested cell."""
    acc: Dict[Tuple[str, str], List[float]] = defaultdict(lambda: [0.0, 0])
    for r in rows:
        a = acc[(r["family"], r["mode"])]
        a[0] += r["mean_c"] * r["contracts"]
        a[1] += r["contracts"]
    return {"%s|%s" % k: {"mean_c": round(v[0] / v[1], 2), "contracts": int(v[1])}
            for k, v in sorted(acc.items()) if v[1]}


def summary_lines(report: Dict) -> List[str]:
    """A few plain lines for the log, the watch report and a research prompt."""
    out = ["Maker vs taker base rates (research window only, net of fee + 1c stress, %d contracts, %d cells "
           "tested): %d lead(s) survive FDR %.0f%% with both halves positive."
           % (report.get("contracts", 0), report.get("tested", 0), report.get("lead_count", 0), FDR * 100)]
    for r in report.get("leads", [])[:6]:
        out.append("  %s %s %s at %s, %s to close: %+.2fc/contract, t %.1f, %d clusters%s"
                   % (r["family"], r["mode"], r["side"].upper(), r["price"], r["ttc"], r["mean_c"], r["t"],
                      r["clusters"], ", fills %.0f%% of posts" % (100 * r["fill_rate"]) if r["fill_rate"] else ""))
    return out
