"""Favorite-bid lead: rest a bid on the near-certain side of a Bitcoin contract late, hold to settlement.

Found 2026-11-04 by a hand-driven study of 10.4 days of Kalshi BTC trade prints and per-second books
(the LLM research loop could not see it: the backtest and the maker scan both refuse entries above
98c and charge a flat 1c cost stress, which is the whole payoff of a 99c contract).

Who earns on Kalshi BTC trades (3.4B contracts of prints, 10-24 to 11-04): takers who buy the 1-3c
lottery side lose about their whole stake, and whoever holds the 97-99c favorite in the last minutes
wins 0.5-1.5c a contract. Our own resting order would sit at the BACK of the queue, so the lead is
judged with a queue-aware replay, pessimistic on purpose:

* the order joins the favorite's best bid at PRICE the first second that bid equals PRICE inside the
  last HORIZON_S seconds, behind everything resting there then (the displayed size);
* it rests until the close; nothing ahead of it ever cancels;
* it fills only from prints that SELL the favorite at PRICE or worse after it rested (a sweep through
  the level fills it too, which is where the adverse selection lives), once those prints exceed the
  queue ahead;
* a fill holds to settlement and pays Kalshi's maker fee, rounded up per order.

Discovery result (KXBTC15M, 99c, last 420s, 100 lots, 10-25 to 11-03 at full 1s resolution): 885
fills, 6 losses (0.68%) against a 0.98% break-even loss rate, +0.30c a contract, one-sided binomial
p 0.23. All makers at 99c in the same window (any queue position) earned +0.66 to +0.84c, so the back
of the queue gives up about half the edge to faster makers. One of 12 grid cells, not significant: a
LEAD, not a pass. The forward test below is pre-registered and reads only days after the discovery
window, with ONE judgment at MIN_FILLS.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence

# --- the pre-registered forward test (2026-11-04, before any forward day was read) -----------------
SERIES = "KXBTC15M"
PRICE = 0.99                 # the favorite's bid we join
HORIZON_S = 420              # join only inside the last 7 minutes
QTY = 100                    # contracts per order (fee rounding; fills scale ~linearly to 5,000)
DISCOVERY_FROM = "2026-10-24T19:00:00Z"
DISCOVERY_TO = "2026-11-04T04:00:00Z"
FORWARD_FROM = "2026-11-04T05:00:00Z"   # first close the forward test may count
MIN_FILLS = 2600             # ~30 days. Power at alpha 0.05: ~45% if the loss rate is 0.68% (discovery),
                             # ~95% at 0.38% (the first, 10s-sampled pass); a null passes 5%
ALPHA = 0.05

# Descriptive grid, reported alongside, never judged.
GRID_PRICES = (0.97, 0.98, 0.99)
GRID_HORIZONS = (60, 180, 420, 900)

EPS = 1e-9


@dataclass
class Fill:
    ticker: str
    close_ts: int
    side: str            # "yes" or "no": which side was the favorite we bid on
    price: float
    qty: float
    win: float           # 1.0 when the favorite settled in the money
    fee: float           # dollars for the whole order
    secs_before_close: float
    queue_ahead: float

    @property
    def pnl(self) -> float:
        return self.qty * (self.win - self.price) - self.fee


def favorite_bid(side: str, yes_bid: float, yes_ask: float) -> float:
    """The favorite side's best bid: the YES bid, or for NO the NO bid = 1 - YES ask."""
    v = yes_bid if side == "yes" else 1.0 - yes_ask
    return round(v, 2) if v is not None and math.isfinite(v) else float("nan")


def queue_fill(side: str, secs: Sequence[int], yes_bid: Sequence[float], yes_ask: Sequence[float],
               bid_size: Sequence[float], ask_size: Sequence[float], close_ts: int,
               prints: Iterable[tuple], price: float, horizon_s: float, qty: float,
               maker_fee: Callable[[int, float], float]) -> Optional[Dict]:
    """Replay one contract-side. ``secs``/quotes are the per-second book (ascending). ``prints`` are
    (ts seconds float, yes_price, count, taker_side). Returns None when the order was never posted,
    else {"posted_sec", "queue_ahead", "filled", "fill_ts"}."""
    post = None
    for i, s in enumerate(secs):
        if close_ts - s > horizon_s or s >= close_ts:
            continue
        if abs(favorite_bid(side, yes_bid[i], yes_ask[i]) - price) < EPS:
            post = i
            break
    if post is None:
        return None
    t0 = secs[post] + 1.0                       # rests from the next second
    sz = bid_size[post] if side == "yes" else ask_size[post]
    ahead = float(sz) if sz is not None and math.isfinite(sz) else 0.0
    cum, fill_ts = 0.0, None
    for ts, yp, cnt, taker in sorted(prints):
        if ts < t0 or ts >= close_ts:
            continue
        if side == "yes":                       # YES bid hit: taker buys NO, YES printed at or below p
            hits = taker == "no" and yp <= price + EPS
        else:                                   # NO bid hit: taker buys YES at or above 1 - p
            hits = taker == "yes" and (1.0 - yp) <= price + EPS
        if not hits:
            continue
        cum += float(cnt)
        if fill_ts is None and cum > ahead + EPS:
            fill_ts = ts
    filled = max(0.0, min(qty, cum - ahead))
    return {"posted_sec": secs[post], "queue_ahead": ahead, "filled": filled, "fill_ts": fill_ts}


def contract_fills(ticker: str, close_ts: int, result_yes: Optional[float], book: Dict[str, Sequence],
                   prints: List[tuple], price: float, horizon_s: float, qty: float,
                   maker_fee: Callable[[int, float], float]) -> List[Fill]:
    """Both sides of one contract (only the side whose bid reaches ``price`` can post)."""
    if result_yes is None:
        return []
    out = []
    for side in ("yes", "no"):
        r = queue_fill(side, book["sec"], book["yes_bid"], book["yes_ask"], book["yes_bid_size"],
                       book["yes_ask_size"], close_ts, prints, price, horizon_s, qty, maker_fee)
        if not r or r["filled"] <= 0:
            continue
        q = r["filled"]
        out.append(Fill(ticker, close_ts, side, price, q, result_yes if side == "yes" else 1.0 - result_yes,
                        maker_fee(int(math.ceil(q)), price), close_ts - (r["fill_ts"] or close_ts), r["queue_ahead"]))
    return out


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p), summed in log space (n up to tens of thousands)."""
    if n <= 0:
        return 1.0
    if p <= 0:
        return 1.0
    if p >= 1:
        return 1.0 if k >= n else 0.0
    lp, lq = math.log(p), math.log1p(-p)
    tot = 0.0
    for i in range(0, k + 1):
        tot += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
    return min(1.0, tot)


def judge(fills: Sequence[Fill], price: float, maker_fee_rate: float = 0.0175) -> Dict:
    """Loss-rate test against break-even, plus the cluster-robust mean. One fill per contract-side; a
    15-minute contract is its own event, so clustering by close_ts only matters for hourly ladders."""
    n = len(fills)
    losses = sum(1 for f in fills if f.win < 0.5)
    be_loss = 1.0 - price - maker_fee_rate * price * (1.0 - price)
    qty = sum(f.qty for f in fills)
    pnl = sum(f.pnl for f in fills)
    by_ev: Dict[int, List[float]] = {}
    for f in fills:
        e = by_ev.setdefault(f.close_ts, [0.0, 0.0])
        e[0] += f.pnl
        e[1] += f.qty
    mean = pnl / qty if qty else 0.0
    k = len(by_ev)
    t = None
    if k > 1 and qty:
        ss = sum((s - mean * q) ** 2 for s, q in by_ev.values()) * k / (k - 1)
        se = math.sqrt(ss) / qty
        t = mean / se if se > 0 else None
    return {"fills": n, "events": k, "losses": losses, "loss_rate": losses / n if n else None,
            "break_even_loss_rate": be_loss, "p_binom": _binom_cdf(losses, n, be_loss) if n else None,
            "contracts": qty, "pnl_usd": pnl, "mean_c": 100 * mean, "t": t,
            "worst_usd": min((f.pnl for f in fills), default=0.0)}


def verdict(j: Dict, min_fills: int = MIN_FILLS, alpha: float = ALPHA) -> str:
    if j["fills"] < min_fills:
        return "accruing (%d of %d fills)" % (j["fills"], min_fills)
    if j["mean_c"] > 0 and j["p_binom"] is not None and j["p_binom"] <= alpha:
        return "PASS"
    return "FAIL"
