"""Are Kalshi's long-dated crypto contracts priced right? Calibration of settled history.

The lead (2026-11-06, information-edge thread): on every day from 2026-10-25 to 2026-11-06 Kalshi
priced its long-dated Bitcoin "above $X by <date>" contracts (KXBTCMAXY, KXBTCMAX100, KXBTCPRICE,
...) 1.5 to 5.5c above what Deribit's options surface implies for the same touch, and 2-3x above
what realized volatility implies. A forward test would take months, so this asks Kalshi's own
settled history instead: when a long-dated contract traded at p, did it settle YES about p of the
time?

Everything here is pure (no network): the run module (app.long_dated_calibration_run) fetches
markets and daily candles and hands them in.

Kinds, from the title and strike type:
  touch_up    "... above $X by <date>"  (strike_type greater): any minute's trimmed BRTI mean above X
  touch_down  "... below $X by <date>"  (strike_type less)
  terminal    price at one time: above / below / between strikes (KX<coin>Y year-end ranges)

Look-ahead: a touch contract closes EARLY when it is hit, so its own close_time knows the answer.
Horizons are measured back from the EVENT's scheduled end (the latest close among its markets; the
strikes nobody touched run to the end), and a market counts at a horizon only if it was listed and
still open then.

Clustering: every strike of every coin ending on the same date is one bet on crypto's path, so
confidence intervals resample whole end dates, never contracts.
"""
from __future__ import annotations

import math
import random
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

COINS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "HYPE")
# Long-dated families only: the 15-minute, hourly/daily ladders and ranges are other studies.
SERIES_RE = re.compile(r"^KX(%s)(MAX|MIN|Y$|PRICE|ABOVE|20\d\d)" % "|".join(COINS))
HORIZONS_D = (90, 60, 30, 14, 7, 3, 1)
PRICE_BUCKETS = (0.0, 0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.90, 0.95, 0.98, 1.0)
MAX_CANDLE_AGE_S = 2 * 86400
TAKER_RATE = 0.07
BOOT = 2000
# Pre-registered rules (written before the history was fetched). Selling YES at the bid = buying NO.
RULES = (
    {"name": "sell_up_touch_2_30c_30d", "kind": "touch_up", "h": 30, "lo": 0.02, "hi": 0.30},
    {"name": "sell_up_touch_2_30c_7d", "kind": "touch_up", "h": 7, "lo": 0.02, "hi": 0.30},
    {"name": "sell_down_touch_2_30c_30d", "kind": "touch_down", "h": 30, "lo": 0.02, "hi": 0.30},
    {"name": "sell_terminal_tails_1_10c_30d", "kind": "terminal", "h": 30, "lo": 0.01, "hi": 0.10},
)


def is_long_dated(series: str) -> bool:
    return bool(SERIES_RE.match(series or "")) and not series.endswith(("15M", "D"))


def coin_of(series: str) -> Optional[str]:
    m = SERIES_RE.match(series or "")
    return m.group(1) if m else None


def kind_of(m: Dict) -> Optional[str]:
    st = (m.get("strike_type") or "").lower()
    title = (m.get("title") or "").lower()
    touch = " by " in title or "before" in title
    if st in ("greater", "greater_or_equal"):
        return "touch_up" if touch else "terminal"
    if st in ("less", "less_or_equal"):
        return "touch_down" if touch else "terminal"
    if st == "between":
        return "terminal"
    return None


def _ts(v) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())
    except ValueError:
        return None


def result_of(m: Dict) -> Optional[float]:
    r = (m.get("result") or "").lower()
    return 1.0 if r == "yes" else 0.0 if r == "no" else None


def event_ends(markets: Iterable[Dict]) -> Dict[str, int]:
    """{event_ticker: scheduled end} = the latest close_time among the event's markets."""
    out: Dict[str, int] = {}
    for m in markets:
        c = _ts(m.get("close_time"))
        e = m.get("event_ticker")
        if c is not None and e:
            out[e] = max(out.get(e, c), c)
    return out


def _px(d: Dict, field: str):
    v = (d or {}).get(field + "_dollars")
    v = (d or {}).get(field) if v is None else v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def quote_at(candles: Sequence[Dict], t: int) -> Optional[Tuple[float, float]]:
    """(yes_bid, yes_ask) from the newest candle that ended at or before t (and not too long before)."""
    best = None
    for c in candles:
        e = _ts(c.get("end_period_ts"))
        if e is None or e > t or t - e > MAX_CANDLE_AGE_S:
            continue
        if best is None or e > best[0]:
            best = (e, c)
    if best is None:
        return None
    c = best[1]
    bid, ask = _px(c.get("yes_bid"), "close"), _px(c.get("yes_ask"), "close")
    if bid is None or ask is None or not (0.0 <= bid <= ask <= 1.0) or ask <= 0.0 or bid >= 1.0:
        return None
    return bid, ask


def observations(markets: Sequence[Dict], candles: Dict[str, Sequence[Dict]],
                 horizons: Sequence[int] = HORIZONS_D) -> List[Dict]:
    """One row per (settled market, horizon) where the market was open and quoted at that horizon."""
    ends = event_ends(markets)
    rows = []
    for m in markets:
        res, kind = result_of(m), kind_of(m)
        tk, ev = m.get("ticker"), m.get("event_ticker")
        if res is None or kind is None or ev not in ends:
            continue
        end, opened, closed = ends[ev], _ts(m.get("open_time")), _ts(m.get("close_time"))
        if opened is None or closed is None:
            continue
        series = m.get("series_ticker") or tk.split("-")[0]
        for h in horizons:
            t = end - h * 86400
            if not (opened <= t < closed):
                continue
            q = quote_at(candles.get(tk) or (), t)
            if q is None:
                continue
            bid, ask = q
            rows.append({"ticker": tk, "event": ev, "series": series, "coin": coin_of(series), "kind": kind,
                         "h": h, "end": end, "end_date": datetime.fromtimestamp(end, timezone.utc).strftime("%Y-%m-%d"),
                         "bid": bid, "ask": ask, "mid": (bid + ask) / 2.0, "yes": res})
    return rows


def taker_fee(p: float) -> float:
    return TAKER_RATE * p * (1.0 - p)


def sell_pnl(r: Dict) -> float:
    """Per contract: sell YES at the bid (buy NO at 1 - bid) as a taker, hold to settlement."""
    return r["bid"] - taker_fee(r["bid"]) - r["yes"]


def buy_pnl(r: Dict) -> float:
    return r["yes"] - r["ask"] - taker_fee(r["ask"])


def _cluster_boot(rows: List[Dict], fn, seed: int = 11) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """(mean, 5th pct, 95th pct) of fn over rows, resampling whole end dates."""
    if not rows:
        return None, None, None
    by: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        by[r["end_date"]].append(fn(r))
    keys = list(by)
    mean = sum(sum(v) for v in by.values()) / sum(len(v) for v in by.values())
    if len(keys) < 3:
        return mean, None, None
    rng = random.Random(seed)
    means = []
    for _ in range(BOOT):
        s = n = 0.0
        for _ in keys:
            v = by[keys[rng.randrange(len(keys))]]
            s += sum(v)
            n += len(v)
        means.append(s / n)
    means.sort()
    return mean, means[int(0.05 * BOOT)], means[int(0.95 * BOOT) - 1]


def _bucket(p: float) -> str:
    for lo, hi in zip(PRICE_BUCKETS, PRICE_BUCKETS[1:]):
        if p < hi or hi == 1.0:
            return "%02d-%02dc" % (round(lo * 100), round(hi * 100))
    return "?"


def summarize(rows: List[Dict]) -> Dict:
    """Calibration table (kind x horizon x mid bucket) and the pre-registered rules."""
    def stats(rs: List[Dict]) -> Dict:
        m, lo, hi = _cluster_boot(rs, sell_pnl)
        return {"n": len(rs), "end_dates": len({r["end_date"] for r in rs}), "events": len({r["event"] for r in rs}),
                "mean_mid": round(sum(r["mid"] for r in rs) / len(rs), 4) if rs else None,
                "yes_rate": round(sum(r["yes"] for r in rs) / len(rs), 4) if rs else None,
                "sell_c": None if m is None else round(100 * m, 2),
                "sell_c_p5": None if lo is None else round(100 * lo, 2),
                "sell_c_p95": None if hi is None else round(100 * hi, 2)}

    table = defaultdict(list)
    for r in rows:
        table[(r["kind"], r["h"], _bucket(r["mid"]))].append(r)
    calib = [dict(kind=k, h=h, bucket=b, **stats(rs)) for (k, h, b), rs in sorted(table.items())]
    rules = []
    for rule in RULES:
        rs = [r for r in rows if r["kind"] == rule["kind"] and r["h"] == rule["h"] and rule["lo"] <= r["bid"] <= rule["hi"]]
        s = stats(rs)
        by_coin = {c: stats([r for r in rs if r["coin"] == c]) for c in sorted({r["coin"] for r in rs})}
        verdict = ("too_few_dates" if s["end_dates"] < 6 else
                   "edge" if (s["sell_c_p5"] or -1) > 0 else
                   "loses" if (s["sell_c_p95"] or 1) < 0 else "inconclusive")
        rules.append(dict(rule, **s, verdict=verdict, by_coin=by_coin))
    return {"rows": len(rows), "markets": len({r["ticker"] for r in rows}), "end_dates": len({r["end_date"] for r in rows}),
            "coins": sorted({r["coin"] for r in rows if r["coin"]}), "rules": rules, "calibration": calib}
