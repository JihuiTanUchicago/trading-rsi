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
import re
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, Optional, Tuple

from app.services import mention_maker as mm

QTY = 3
LOSS_STOP_USD = 25.0
MAX_COLLATERAL_USD = 45.0
CANCEL_AT = 0.80
REQUOTE_MIN_S = 10.0
LO, HI = mm.LO, mm.HI
DELAY_S, HOLD_S = mm.DELAY_S, mm.HOLD_S
CLIENT_PREFIX = "mp-"
EXIT_PREFIX = "mpx-"
# Todd 2026-11-16: once an event's window has closed, sell back NO positions on words the market has
# decided were not said (YES ask at or under EXIT_AT), so unsettled winners stop holding the loss room.
EXIT_AT = 0.02


_EVENT_DATE = re.compile(r"-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})")
_MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}


def event_date(event: str) -> Optional[datetime]:
    """The date in a Kalshi event ticker (KXHILLARYMENTION-26NOV14 -> 2026-11-14 UTC), or None."""
    m = _EVENT_DATE.search(event or "")
    if not m:
        return None
    try:
        return datetime(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)), tzinfo=timezone.utc)
    except ValueError:
        return None


def on_event_day(event: str, ts: float) -> bool:
    """A trigger only counts from the UTC day before the event ticker's date to the UTC day after (shows
    and calls run either side of 00Z). Thin pre-event markets jump to 90c weeks early
    (KXEARNINGSMENTIONCHWY-26DEC09 'triggered' on 11-16 on a 0.90 print): that is not a live event, and
    quoting it would hold NO for weeks. In the 10.5-month non-sport replay (2.5h, 100/word) the kept
    window has 855 events at +8.3c a contract (day before 128 at +10.9c, on/after 727 at +7.9c); the
    excluded 1 to 7 days early lose (62 events, -4.8c) and >7 days early (86, +7.5c) would hold cash for
    weeks. No date in the ticker: no trigger."""
    d = event_date(event)
    if d is None:
        return False
    day = datetime.fromtimestamp(ts, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return d - timedelta(days=1) <= day <= d + timedelta(days=1)


class Triggers:
    """Online version of mention_maker.event_trigger: the first print >= 90c right after a print < 70c in
    any market of an event. Fed the public trade stream in time order."""

    def __init__(self, known: Optional[Dict[str, float]] = None):
        self.last: Dict[str, float] = {}
        # Saved triggers pass the same date check: one saved before the check existed must not come back.
        self.at: Dict[str, float] = {e: t for e, t in (known or {}).items() if on_event_day(e, t)}

    def see(self, event: str, ticker: str, ts: float, price: float) -> Optional[float]:
        """The event's trigger time when this print is the one that sets it, else None."""
        prev = self.last.get(ticker)
        self.last[ticker] = price
        if event in self.at or prev is None or not on_event_day(event, ts):
            return None
        if prev < mm.TRIGGER_FROM_BELOW and price >= mm.TRIGGER_AT:
            self.at[event] = ts
            return ts
        return None

    def live(self, now: float) -> Dict[str, float]:
        """Events inside their quoting window right now: trigger + DELAY_S <= now < trigger + HOLD_S."""
        return {e: t for e, t in self.at.items() if t + DELAY_S <= now < t + HOLD_S}


DROP_S = 300.0     # look-back for the drop filter
DROP_BY = 0.10     # a fall of more than this over DROP_S: stop selling YES in that market


class Tape:
    """Recent prints per market, for the drop filter: no YES ask in a market whose last print is more than
    DROP_BY under the last print at least DROP_S earlier. YES buyers right after a sharp fall are informed
    (the word is coming): in the replay of every non-sport mention print at our size (3 contracts), fills
    after such a fall made +10c a contract vs +23c (Jan-Jun 2026), +2c vs +17c (Jul-Oct), -2c vs +6c
    (10-20..11-18) and -22c vs -1c (forward 11-12..11-18): the same sign in all four samples."""

    def __init__(self) -> None:
        self.h: Dict[str, Deque[Tuple[float, float]]] = {}

    def add(self, ticker: str, ts: float, price: float) -> None:
        d = self.h.setdefault(ticker, deque())
        d.append((ts, price))
        while len(d) > 1 and d[1][0] <= ts - DROP_S:
            d.popleft()   # keep the newest print at least DROP_S old, and everything after it

    def fell(self, ticker: str, now: float) -> bool:
        d = self.h.get(ticker)
        if not d:
            return False
        ref = None
        for ts, p in d:
            if ts <= now - DROP_S:
                ref = p
            else:
                break
        return ref is not None and d[-1][1] - ref < -DROP_BY - 1e-9


def should_exit(yes_ask: Optional[float], window_closed: bool) -> bool:
    """Sell a NO position back now: its event's quoting window is over and YES is offered at EXIT_AT or
    less (the word was not said; the NO is worth 98c or more and only waits for Kalshi to settle)."""
    return window_closed and yes_ask is not None and 0.0 < yes_ask <= EXIT_AT + 1e-9


DEAD_LAST = 0.05   # last print at or under this, and no YES bid over EXIT_AT: the word looks unsaid


def should_bid_exit(yes_bid: Optional[float], last: Optional[float], window_closed: bool) -> bool:
    """Rest a YES bid at EXIT_AT for a NO position whose word looks unsaid but whose book offers no YES at
    EXIT_AT or less (thin markets: last print 1c, cheapest YES offer 13c+). Holders of dead YES sell into it.
    Never during the window; a bid that fills on a word that is later said only closed the NO at 2c."""
    return (window_closed and last is not None and 0.0 < last <= DEAD_LAST + 1e-9
            and (yes_bid or 0.0) <= EXIT_AT + 1e-9)


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


def room(realized_pnl: float, open_cost: float, resting: float, shard_cash: Optional[float],
         open_risk: Optional[float] = None) -> float:
    """Collateral the pilot may still put out. Realized loss plus everything still at risk stays inside
    LOSS_STOP_USD, the total out stays under MAX_COLLATERAL_USD, and it never exceeds the shard's cash.
    Unknown cash (an unreadable balance) means no new orders.

    open_risk, when given, replaces open_cost in the loss leg only: what the open positions would lose
    if sold now at the best NO bid (position_risk). Decided-but-unsettled words (Kalshi can take days to
    settle) then stop blocking the next event. Capital out (MAX_COLLATERAL_USD) still counts full cost."""
    if shard_cash is None or halted(realized_pnl):
        return 0.0
    loss_risk = (open_cost if open_risk is None else open_risk) + resting
    free = min(MAX_COLLATERAL_USD - open_cost - resting, LOSS_STOP_USD + realized_pnl - loss_risk,
               shard_cash - resting)
    return max(0.0, free)


def position_risk(qty: float, cost_with_fees: float, yes_ask: Optional[float]) -> float:
    """What a NO position loses if sold now: cost less qty at the best NO bid (1 - best YES ask). No ask
    to price from means the whole cost is at risk."""
    if yes_ask is None or not (0.0 < yes_ask <= 1.0):
        return cost_with_fees
    return max(0.0, cost_with_fees - qty * (1.0 - yes_ask))


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
