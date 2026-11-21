"""The Bitcoin maker pilot's live inputs and guards (pure: no network, no venue). app.btc_maker_pilot_run wires them.

The pilot runs the lab's confirmed KXBTC policy (r5.1: Bitcoin hourly price ranges) on live books, through the
same trading_core QuotingLoop the replay scored. Its inputs must be built as the replay built them from
features_1s:
- ``spot_move_bp`` is the BTC log return over the policy's move window, in basis points. It feeds
  ``pull_move_bp``, which pulls quotes after a fast move.
- Fair value exists only for a single-strike contract (floor only or cap only). It is
  Phi(ln-distance / (rv_300s * sqrt(seconds to close))), where rv_300s is the stdev of 1-second log
  returns over 300 s. A range contract has no fair value, so the policy prices it off the mid
  (maker_quotes).

Guards, checked every step:
- **Loss stop.** Pilot P&L (realized, plus held contracts marked at the mid, minus fees, plus P&L
  carried from earlier runs) below -loss_stop cancels everything and stops for good (``stopped``
  persists).
- **Collateral cap.** Cash tied up in working orders plus held positions above the cap pauses new
  quotes (they are all cancelled) until it falls back.
- **Size.** The pilot's max_inventory and quote_size override the policy's (a fraction of paper size).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Deque, Dict, Iterable, List, Optional, Tuple

from trading_core.domain import BinaryTerms, Instrument, InstrumentId, InstrumentKind, Quote
from trading_core.engine.maker_policy import to_config
from trading_core.engine.quoting import MakerConfig

SERIES = "KXBTC"
RV_WINDOW_S = 300


class SpotTracker:
    """BTC spot sampled about once a second: returns over a window and the 300 s realized volatility."""

    def __init__(self, keep_s: int = 900) -> None:
        self.keep_s = keep_s
        self.px: Deque[Tuple[int, float]] = deque()      # (whole second, last price that second)

    def add(self, ts: float, price: float) -> None:
        if not price or price <= 0 or not math.isfinite(price):
            return
        s = int(ts)
        if self.px and self.px[-1][0] == s:
            self.px[-1] = (s, price)
        elif not self.px or s > self.px[-1][0]:
            self.px.append((s, price))
        while self.px and self.px[0][0] < s - self.keep_s:
            self.px.popleft()

    def last(self) -> Optional[float]:
        return self.px[-1][1] if self.px else None

    def _at_or_before(self, s: int) -> Optional[float]:
        best = None
        for t, p in self.px:
            if t > s:
                break
            best = p
        return best

    def move_bp(self, window_s: int) -> Optional[float]:
        """Log return over the last ``window_s`` seconds in bp, None until that much history exists."""
        if not self.px:
            return None
        now_s, now_p = self.px[-1]
        then = self._at_or_before(now_s - window_s)
        return None if then is None else math.log(now_p / then) * 1e4

    def rv(self, window_s: int = RV_WINDOW_S) -> Optional[float]:
        """Stdev of 1-second log returns over ``window_s`` (forward-filled seconds), None if under half full."""
        if len(self.px) < 2:
            return None
        end = self.px[-1][0]
        start = end - window_s
        filled, j, last = [], 0, None
        pts = list(self.px)
        for s in range(start, end + 1):
            while j < len(pts) and pts[j][0] <= s:
                last = pts[j][1]
                j += 1
            if last is not None:
                filled.append(last)
        if len(filled) < window_s // 2:
            return None
        r = [math.log(b / a) for a, b in zip(filled, filled[1:])]
        if len(r) < 2:
            return None
        m = sum(r) / len(r)
        return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1))


def tail_fair(spot: Optional[float], floor: Optional[float], cap: Optional[float], rv: Optional[float],
              secs_to_close: Optional[float]) -> Optional[float]:
    """Phi(dist_sigma) for a single-strike contract, None for a range or without inputs (as in the replay)."""
    if spot is None or rv is None or secs_to_close is None or (floor is None) == (cap is None):
        return None
    denom = rv * math.sqrt(max(secs_to_close, 0.0))
    if denom <= 0:
        return None
    d = math.log(spot / floor) / denom if floor is not None else math.log(cap / spot) / denom
    return 0.5 * (1.0 + math.erf(d / math.sqrt(2.0)))


@dataclass(frozen=True)
class Market:
    ticker: str
    close_ts: float
    floor: Optional[float]
    cap: Optional[float]
    listed_mid: Optional[float] = None    # from the /markets listing (refreshed each minute): the band pre-filter


def _num(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _ts(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def markets_from(page: Dict, now: float, max_secs_to_close: float) -> List[Market]:
    """Open KXBTC contracts closing within ``max_secs_to_close`` (the current hour's event)."""
    out = []
    for m in page.get("markets") or []:
        tk, close = m.get("ticker"), _ts(m.get("close_time"))
        if not tk or not str(tk).startswith(SERIES + "-") or close is None or not 0 < close - now <= max_secs_to_close:
            continue
        bid, ask = _num(m.get("yes_bid_dollars")), _num(m.get("yes_ask_dollars"))
        mid = (bid + ask) / 2 if bid is not None and ask is not None and ask > 0 else None
        out.append(Market(tk, close, _num(m.get("floor_strike")), _num(m.get("cap_strike")), mid))
    return out


def instruments_for(markets: Iterable[Market], into: Dict[InstrumentId, Instrument]) -> None:
    """Add each contract's YES and NO instrument (QuotingLoop books fills against either)."""
    from datetime import timezone
    for m in markets:
        yid = InstrumentId("kalshi", m.ticker, "yes")
        if yid in into:
            continue
        inst = Instrument(yid, InstrumentKind.BINARY, tick_size=0.01, series=SERIES,
                          terms=BinaryTerms(expiry=datetime.fromtimestamp(m.close_ts, timezone.utc)))
        into[yid] = inst
        into[inst.complement().id] = inst.complement()


def quote_from_book(ticker: str, book: Dict, now: float, move_bp: Optional[float]) -> Optional[Quote]:
    """Top of book from /markets/{t}/orderbook (orderbook_fp: [price, size] ascending, best last).
    YES bid = best YES bid; YES ask = 1 - best NO bid. None for a one-sided or crossed book."""
    ob = book.get("orderbook_fp") or book.get("orderbook") or {}
    yes, no = ob.get("yes_dollars") or [], ob.get("no_dollars") or []
    if not yes or not no:
        return None
    by, bys = _num(yes[-1][0]), _num(yes[-1][1])
    bn, bns = _num(no[-1][0]), _num(no[-1][1])
    if by is None or bn is None or by + bn >= 1.0:
        return None
    return Quote(InstrumentId("kalshi", ticker, "yes"), now, by, round(1.0 - bn, 4), bys, bns,
                 features={} if move_bp is None else {"spot_move_bp": move_bp})


def pilot_config(policy: Dict, max_inventory: float, quote_size: float) -> MakerConfig:
    """The policy's levers at pilot size."""
    return replace(to_config(policy), max_inventory=float(max_inventory), quote_size=float(quote_size))


def pilot_pnl(book, mids: Dict[str, float], carried: float = 0.0) -> float:
    """Realized + held contracts marked at the YES mid (NO at 1 - mid) - fees, plus earlier runs' P&L."""
    total = carried
    for iid, pos in book.positions.items():
        mid = mids.get(iid.symbol)
        mark = None if mid is None else (mid if iid.outcome == "yes" else 1.0 - mid)
        total += pos.realized + pos.unrealized(mark) - pos.fees
    return total


def position_collateral(book) -> float:
    return sum(abs(p.qty) * p.avg_price for p in book.open_positions().values())


def pick_universe(quotes: Dict[InstrumentId, Quote], held: Iterable[str], budget: float,
                  quote_size: float, quoted: Iterable[str] = (), margin: int = 2) -> List[InstrumentId]:
    """The contracts to quote within the collateral budget. A contract quoted on both sides ties up at
    most quote_size dollars (its YES bid + NO bid < $1 each), so the budget buys floor(budget / quote_size)
    contracts: held ones first (they must be able to work back to flat), then the rest nearest 50c, where
    the flow is. Without this a small cap posts everything, trips the cap, cancels, and reposts each step.

    Hysteresis: a contract we already quote (``quoted``) keeps its slot while it ranks within ``margin``
    places of the cut. Without it two contracts near the cut swap on every tick of the mid, and each swap
    cancels and reposts both sides, which throws away queue position the replay credited."""
    held, quoted = set(held), set(quoted)
    first = [y for y in quotes if y.symbol in held]
    rest = sorted((y for y in quotes if y.symbol not in held),
                  key=lambda y: abs((quotes[y].mid if quotes[y].mid is not None else 0.0) - 0.5))
    n = max(0, int(budget // max(quote_size, 1e-9)))
    slots = max(0, n - len(first))
    keep = [y for y in rest[:slots + margin] if y.symbol in quoted][:slots]
    fill = [y for y in rest if y not in keep][:slots - len(keep)]
    return first + keep + fill


@dataclass
class GuardVerdict:
    stop: bool = False
    pause: bool = False
    reason: str = ""


def guard(pnl: float, collateral: float, loss_stop: float, collateral_cap: float) -> GuardVerdict:
    if pnl <= -abs(loss_stop):
        return GuardVerdict(stop=True, reason="loss stop: pilot P&L $%.2f <= -$%.2f" % (pnl, abs(loss_stop)))
    if collateral > collateral_cap:
        return GuardVerdict(pause=True, reason="collateral $%.2f over the $%.2f cap" % (collateral, collateral_cap))
    return GuardVerdict()
