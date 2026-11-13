"""Live pilot of the in-play mention YES seller (app.services.mention_maker), on the user's own Kalshi cash.

Todd approved it on 2026-11-13 at the size of the cash already in his Kalshi account ($50.74), with no
deposits. What the pilot answers is the one thing the print replay cannot: whether we actually get filled
at the best ask the way the backtest assumes, or whether faster makers sit in front of us.

The rule is the frozen forward-test rule, at a size the account can carry:
- An event goes LIVE at the first print >= 90c in one of its markets right after a print < 70c.
- From 5s to 2.5h after that, rest a post-only YES ask (we buy NO) in every market of the event whose
  YES price is 15-65c: 1c inside the best ask when the spread allows, else at it. At most QTY contracts
  per market over the whole event. Hold every fill to settlement.
- A market that prints at or above CANCEL_AT (a word was probably just said) is cancelled and never
  quoted again; so is a market that leaves the band.

Money limits. The worst a NO position can lose is what it cost, so the pilot never lets realized loss
plus everything it could still lose (open cost and resting collateral) pass LOSS_STOP_USD, and never has
more than MAX_COLLATERAL_USD out at once, or more than the cash on the mention shard. At a realized loss
of LOSS_STOP_USD it halts for good and cancels everything.

The queue measure: while an order of ours rests, every YES-taker print on its ticker at or above our
price is a fill the backtest would have given us ("sim"). Real fills over sim fills is the answer.

Stdlib only, no I/O, so all of it is testable.
"""
from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

from app.services import mention_maker as mm

QTY = 3
LOSS_STOP_USD = 25.0
MAX_COLLATERAL_USD = 45.0
CANCEL_AT = 0.80
REQUOTE_MIN_S = 10.0
LO, HI = mm.LO, mm.HI
DELAY_S, HOLD_S = mm.DELAY_S, mm.HOLD_S
CLIENT_PREFIX = "mp-"


class Triggers:
    """Online version of mention_maker.event_trigger: the first print >= 90c right after a print < 70c in
    any market of an event. Fed the public trade stream in time order."""

    def __init__(self, known: Optional[Dict[str, float]] = None):
        self.last: Dict[str, float] = {}
        self.at: Dict[str, float] = dict(known or {})

    def see(self, event: str, ticker: str, ts: float, price: float) -> Optional[float]:
        """The event's trigger time when this print is the one that sets it, else None."""
        prev = self.last.get(ticker)
        self.last[ticker] = price
        if event in self.at or prev is None:
            return None
        if prev < mm.TRIGGER_FROM_BELOW and price >= mm.TRIGGER_AT:
            self.at[event] = ts
            return ts
        return None

    def live(self, now: float) -> Dict[str, float]:
        """Events inside their quoting window right now: trigger + DELAY_S <= now < trigger + HOLD_S."""
        return {e: t for e, t in self.at.items() if t + DELAY_S <= now < t + HOLD_S}


def best_prices(orderbook: Dict) -> Tuple[Optional[float], Optional[float]]:
    """(best YES bid, best YES ask) from GET /markets/{t}/orderbook. Kalshi lists resting bids on each
    side in ascending price; a NO bid at q is a YES ask at 1 - q."""
    book = orderbook.get("orderbook_fp") or orderbook.get("orderbook") or {}

    def top(levels) -> Optional[float]:
        prices = []
        for lv in levels or []:
            try:
                if float(lv[1]) > 0:
                    prices.append(float(lv[0]))
            except (TypeError, ValueError, IndexError):
                continue
        return max(prices) if prices else None

    yes_bid = top(book.get("yes_dollars"))
    no_bid = top(book.get("no_dollars"))
    return yes_bid, (round(1.0 - no_bid, 4) if no_bid is not None else None)


def quote_price(yes_bid: Optional[float], yes_ask: Optional[float]) -> Optional[float]:
    """Our YES ask: 1c inside the best ask when the spread is 2c or more (so we are alone at the front),
    else at the best ask. None when there is no ask to price from, or the price is out of band."""
    if yes_ask is None:
        return None
    px = yes_ask - 0.01 if (yes_bid is not None and yes_ask - yes_bid >= 0.02 - 1e-9) else yes_ask
    px = round(px, 2)
    if yes_bid is not None and px <= yes_bid + 1e-9:
        return None  # post-only would cross
    if not (LO <= px < HI):
        return None
    return px


def collateral(qty: float, yes_price: float) -> float:
    """What a resting YES ask (a NO buy at 1 - p) holds back, and the most the position can lose."""
    return qty * (1.0 - yes_price)


def room(realized_pnl: float, open_cost: float, resting: float, shard_cash: Optional[float]) -> float:
    """Collateral the pilot may still put out. Realized loss plus everything still at risk stays inside
    LOSS_STOP_USD, the total out stays under MAX_COLLATERAL_USD, and it never exceeds the shard's cash.
    Unknown cash (an unreadable balance) means no new orders."""
    if shard_cash is None or halted(realized_pnl):
        return 0.0
    at_risk = open_cost + resting
    free = min(MAX_COLLATERAL_USD - at_risk, LOSS_STOP_USD + realized_pnl - at_risk, shard_cash - resting)
    return max(0.0, free)


def halted(realized_pnl: float) -> bool:
    return realized_pnl <= -LOSS_STOP_USD


def order_size(filled: float, resting_qty: float, room_usd: float, yes_price: float) -> int:
    """Whole contracts to post now: what the market still has of its QTY, cut to the room left."""
    left = QTY - filled - resting_qty
    per = 1.0 - yes_price
    if left <= 0 or per <= 0:
        return 0
    return int(max(0, min(math.floor(left + 1e-9), math.floor(room_usd / per + 1e-9))))


def sim_fill(order_price: float, order_left: float, print_price: float, count: float, taker_yes: bool) -> float:
    """What the backtest would credit a resting YES ask at `order_price` from one public print: a YES
    taker paying at least our price would have hit us first had we been at the front."""
    if not taker_yes or order_left <= 0 or print_price + 1e-9 < order_price:
        return 0.0
    return min(float(count), order_left)


def should_cancel(said: bool, in_window: bool, want_price: Optional[float], resting_price: float) -> bool:
    """Pull a resting ask when the word was (probably) said, the window closed, the market left the band
    or the price we would post now differs from the one resting (requote)."""
    return said or not in_window or want_price is None or abs(want_price - resting_price) > 1e-9


def fill_terms(fill: Dict) -> Optional[Tuple[float, float, float]]:
    """(contracts, NO price paid, fee) from a /portfolio/fills row of one of our YES asks, or None when the
    row cannot be read. Our asks open NO positions: the cost basis is 1 - the YES print price."""
    try:
        qty = float(fill.get("count_fp") or fill.get("count") or 0)
        no_px = fill.get("no_price_dollars")
        no_px = float(no_px) if no_px is not None else 1.0 - float(fill["yes_price_dollars"])
        fee = float(fill.get("fee_cost") or 0)
    except (TypeError, ValueError, KeyError):
        return None
    if qty <= 0 or not (0.0 < no_px < 1.0):
        return None
    return qty, no_px, fee


def summary(orders: Dict[str, Dict], realized_pnl: float, open_cost: float, settled: int) -> Dict:
    """The pilot's readout: real vs backtest fills (the queue answer) and money."""
    real = sum(float(o.get("filled") or 0) for o in orders.values())
    sim = sum(float(o.get("sim_filled") or 0) for o in orders.values())
    return dict(orders=len(orders), filled=round(real, 2), sim_filled=round(sim, 2),
                fill_ratio=round(real / sim, 3) if sim > 0 else None,
                realized_pnl=round(realized_pnl, 2), open_cost=round(open_cost, 2), settled_positions=settled,
                halted=halted(realized_pnl), loss_stop=LOSS_STOP_USD, max_collateral=MAX_COLLATERAL_USD,
                qty_per_market=QTY)
