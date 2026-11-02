"""Riskless-set scan: did a Kalshi event's own quotes ever let you lock in a profit?

An event whose contracts are mutually exclusive and exhaustive (exactly one settles YES: weather
brackets, game winners, win margins) must have YES prices that sum to $1. When the YES asks of
every contract, plus taker fees, sum below $1, buying one of each pays $1 for less. When the YES
bids sum above $1 plus fees, buying one NO of each (cost: sum of 1 - bid) pays n - 1 for less.
Neither needs a forecast, so there is nothing to overfit: the scan uses all history.

Quotes are candle closes, so a hit says the book stood there at the bar's end, not how deep it was
or whether it outlived the bar. `persist` counts hits that were still there one bar later.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

QTY = 10                 # contracts per leg, for the fee rounding
SLIPPAGE = 0.0           # extra cost per leg; the report also shows the margin, so this stays 0
SHOW = 10


@dataclass
class Leg:
    ticker: str
    bars: Dict[int, Tuple[Optional[float], Optional[float]]]   # end_ts -> (yes bid, yes ask)
    close_ts: int


def exclusive_event(results: Sequence[str]) -> bool:
    """Exactly one YES and the rest NO: the event's contracts are mutually exclusive and exhaustive
    (a void, scalar or second YES disqualifies it)."""
    return results.count("yes") == 1 and all(r in ("yes", "no") for r in results)


def edges_at(quotes: Sequence[Tuple[Optional[float], Optional[float]]],
             fee: Callable[[int, float], float]) -> Optional[Tuple[float, float]]:
    """(buy-all profit, sell-all profit) per set in dollars after taker fees, or None when a leg's
    ask is missing (0 or None means no quote; 1.00 means an empty side, which only costs)."""
    asks, bids = [], []
    for bid, ask in quotes:
        if ask is None or ask <= 0 or ask > 1:
            return None
        asks.append(ask)
        bids.append(bid if bid is not None and 0 < bid < 1 else None)
    buy = 1.0 - sum(a + fee(QTY, a) / QTY + SLIPPAGE for a in asks)
    if any(b is None for b in bids):
        sell = -1.0          # a leg with no YES bid has no NO ask to buy: the set can't be sold
    else:
        # buying NO on every leg costs sum(1 - bid) and pays n - 1 (every leg but the winner)
        sell = sum(b - fee(QTY, 1.0 - b) / QTY - SLIPPAGE for b in bids) - 1.0
    return buy, sell


@dataclass
class _Fam:
    events: int = 0
    stamps: int = 0
    hits: List[Dict] = field(default_factory=list)
    best: List[float] = field(default_factory=lambda: [-1.0, -1.0])  # best buy-all / sell-all margin seen


class Scan:
    def __init__(self, fee: Callable[[int, float], float]):
        self.fee = fee
        self.fams: Dict[str, _Fam] = defaultdict(_Fam)

    def add_event(self, family: str, event: str, legs: List[Leg], period_s: int) -> None:
        f = self.fams[family]
        f.events += 1
        last = min(l.close_ts for l in legs) - period_s       # every leg still trading, none settling
        stamps = sorted(set.intersection(*[set(l.bars) for l in legs]))
        prev_hit = {"buy": False, "sell": False}
        for ts in stamps:
            if ts > last:
                break
            e = edges_at([l.bars[ts] for l in legs], self.fee)
            if e is None:
                continue
            f.stamps += 1
            for i, (side, margin) in enumerate(zip(("buy", "sell"), e)):
                f.best[i] = max(f.best[i], margin)
                if margin > 0:
                    f.hits.append({"event": event, "ts": ts, "side": side, "legs": len(legs),
                                   "profit_c": round(100 * margin, 2), "persist": prev_hit[side]})
                prev_hit[side] = margin > 0

    def finish(self) -> Dict:
        out = []
        for fam, f in sorted(self.fams.items()):
            hits = f.hits
            out.append({
                "family": fam, "events": f.events, "stamps": f.stamps, "hits": len(hits),
                "hit_events": len({h["event"] for h in hits}),
                "persisting": sum(h["persist"] for h in hits),
                "mean_profit_c": round(sum(h["profit_c"] for h in hits) / len(hits), 2) if hits else None,
                "best_buy_c": round(100 * f.best[0], 2), "best_sell_c": round(100 * f.best[1], 2),
                "examples": sorted(hits, key=lambda h: -h["profit_c"])[:SHOW],
            })
        return {"families": out, "hits": sum(r["hits"] for r in out),
                "persisting": sum(r["persisting"] for r in out)}
