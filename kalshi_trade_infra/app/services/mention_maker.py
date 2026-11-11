"""In-play YES seller on Kalshi mention markets ("Will X say <word>?"): the pre-registered forward test.

Discovery (2026-11-11, 60k settled markets, every trade print): once an event is under way, takers
who buy YES on words not yet said lose 5-14c a contract. Time runs out on the word and they pay as
if it hadn't. A maker who sells them YES and holds to settlement collects it.

The rule, frozen here (do not tune it on forward data):
- An event goes LIVE at the first print >= 90c in one of its markets whose previous print was
  < 70c: a word just got said. Anyone can see it on the public trade feed.
- From 5s after that until 2.5h later, we are the best YES ask in every market of the event and
  sell to the first 100 contracts of YES-taker prints at 15-65c (at the print's price), then hold
  to settlement. Kalshi's 1.75% maker fee is charged.
- Stress ('slow'): we never cancel. When a word we are short gets said, any size we have not
  sold yet fills at the last in-band price and loses. That includes markets we never got a fill in.

Fills come from real prints, so adverse selection (word said, stale ask taken) is in the number.
What prints cannot show is queue competition: if other makers beat us to the front, we fill less.

Judgment: ONE look once JUDGE_AT_EVENTS events whose trigger is at or after FORWARD_FROM have
settled. Pass = mean c/contract > 0 with an event-clustered one-sided p < 0.05.
"""
from __future__ import annotations

import math
import random
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

LO, HI = 0.15, 0.65
QTY = 100
HOLD_S = 9000
DELAY_S = 5
TRIGGER_AT, TRIGGER_FROM_BELOW = 0.90, 0.70
SAID_AT = 0.90
FORWARD_FROM = "2026-11-12T00:00:00Z"
JUDGE_AT_EVENTS = 120
ALPHA = 0.05

# one print: (ts seconds, yes price in dollars, contracts, taker bought YES)
Print = Tuple[float, float, float, bool]


def event_trigger(prints_by_ticker: Dict[str, Sequence[Print]]) -> Optional[float]:
    """Earliest time a market of the event printed >= 90c right after a print < 70c (None if never)."""
    best = None
    for prints in prints_by_ticker.values():
        prev = None
        for ts, p, _c, _ty in sorted(prints, key=lambda r: r[0]):
            if prev is not None and prev < TRIGGER_FROM_BELOW and p >= TRIGGER_AT:
                if best is None or ts < best:
                    best = ts
                break
            prev = p
    return best


def market_result(prints: Sequence[Print], y: float, trigger: float,
                  maker_fee: Callable[[int, float], float], qty: int = QTY) -> Dict:
    """Our fills, P&L and the never-cancel stress for one market of a live event."""
    start, end = trigger + DELAY_S, trigger + HOLD_S
    ps = sorted(prints, key=lambda r: r[0])
    said = next((ts for ts, p, _c, _ty in ps if p >= SAID_AT), None)
    filled = 0.0
    pnl = 0.0
    first_fill = None
    last_band_px = None
    for ts, p, c, taker_yes in ps:
        if ts < start or ts >= end or not (LO <= p < HI):
            continue
        if said is None or ts <= said:
            last_band_px = p
        if not taker_yes or filled >= qty:
            continue
        f = min(float(c), qty - filled)
        lots = int(math.ceil(f))
        pnl += f * (p - y) - maker_fee(lots, p) * (f / lots)
        filled += f
        if first_fill is None:
            first_fill = ts
    slow = pnl
    if y >= 1.0 and said is not None and said >= start and last_band_px is not None and filled < qty:
        slow += (qty - filled) * (last_band_px - 1.0)
    return dict(filled=round(filled, 2), pnl=round(pnl, 4), slow=round(slow, 4), first_fill=first_fill, said=said)


def event_result(prints_by_ticker: Dict[str, Sequence[Print]], results: Dict[str, float],
                 maker_fee: Callable[[int, float], float], qty: int = QTY) -> Optional[Dict]:
    """None when the event never went live; else totals over its markets."""
    trig = event_trigger(prints_by_ticker)
    if trig is None:
        return None
    filled = pnl = slow = 0.0
    markets = 0
    for tk, prints in prints_by_ticker.items():
        if tk not in results:
            continue
        r = market_result(prints, results[tk], trig, maker_fee, qty)
        filled += r["filled"]
        pnl += r["pnl"]
        slow += r["slow"]
        markets += r["filled"] > 0
    return dict(trigger=trig, filled=round(filled, 2), pnl=round(pnl, 4), slow=round(slow, 4), markets_filled=markets)


def summarize(events: Iterable[Dict], judge_at: int = JUDGE_AT_EVENTS, seed: int = 7) -> Dict:
    """Pooled c/contract, event-clustered t and bootstrap interval, and the one-look verdict."""
    ev = [e for e in events if e.get("filled", 0) > 0]
    n = len(ev)
    q = sum(e["filled"] for e in ev)
    out: Dict = dict(events=n, contracts=round(q, 1), judge_at=judge_at)
    if n < 2 or q <= 0:
        out.update(verdict="accumulating")
        return out
    m = sum(e["pnl"] for e in ev) / q
    resid = [e["pnl"] - m * e["filled"] for e in ev]
    se = math.sqrt(sum(r * r for r in resid) * n / (n - 1)) / q
    t = m / se if se > 0 else float("inf")
    p = 0.5 * math.erfc(t / math.sqrt(2))
    rng = random.Random(seed)
    boots = []
    for _ in range(1000):
        s = [ev[rng.randrange(n)] for _ in range(n)]
        sq = sum(e["filled"] for e in s)
        boots.append(sum(e["pnl"] for e in s) / sq if sq else 0.0)
    boots.sort()
    out.update(c=round(100 * m, 3), t=round(t, 3), p_one_sided=round(p, 5),
               ci_c=[round(100 * boots[25], 3), round(100 * boots[974], 3)],
               slow_c=round(100 * sum(e["slow"] for e in ev) / q, 3),
               usd=round(sum(e["pnl"] for e in ev), 2))
    if n < judge_at:
        out["verdict"] = "accumulating"
    else:
        out["verdict"] = "pass" if (m > 0 and p < ALPHA) else "fail"
    return out
