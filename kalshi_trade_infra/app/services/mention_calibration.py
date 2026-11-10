"""Do Kalshi's mention markets ("Will <speaker> say <word>?") misprice what people say?

One-off research test Todd approved on 2026-11-10 (information-edge thread): the only candidate on
the ranked list that is an information edge an AI can hold, since a word's own track record across
past speeches and calls is public but nobody prices it by hand. Two cheap tests come first, from
Kalshi's settled history alone (no transcripts, no LLM):

  1. Calibration: when a word traded at p a day before the event ended, was it said about p of
     the time? Two pre-registered rules: sell YES at 5-40c, buy YES at 60-95c (2h before the event).
  2. Track record: the same word in the same series' EARLIER events (results known by then) gives
     a Laplace-smoothed base rate. Trade when it sits 10c+ past the quote, with 4+ prior events.

A transcript-reading LLM (test 3) is only worth building if one of these shows a gap.

Pure functions; app.mention_calibration_run fetches from Kalshi on the web box.

Anchor: mention markets stay open after the speech, game or call ends, until settlement (all of
an event's words close together, hours later). A quote "24h before close" can therefore come from
after the event, when everyone already knows. Horizons count back from the moment the event was
under way instead: the first hour any word that settled YES traded at 95c+ (said_time). That
picks the time from the outcome's timing, never which words were said; events with no such hour
fall back to the latest close. Every word in one speech is one bet on that speech,
so intervals resample whole events.
"""
from __future__ import annotations

import random
import re
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from app.services.long_dated_calibration import _px, _ts, event_ends, result_of

HORIZONS_H = (24, 6, 2, 1)
MAX_CANDLE_AGE_S = 6 * 3600
TAKER_RATE = 0.07
BOOT = 2000
MIN_PRIOR_EVENTS = 4
EDGE = 0.10
PRICE_BUCKETS = (0.0, 0.05, 0.15, 0.30, 0.50, 0.70, 0.85, 0.95, 1.0)
RULES = (
    {"name": "sell_yes_5_40c_2h", "kind": "calib", "h": 2, "side": "sell", "lo": 0.05, "hi": 0.40},
    {"name": "buy_yes_60_95c_2h", "kind": "calib", "h": 2, "side": "buy", "lo": 0.60, "hi": 0.95},
    {"name": "track_record_10c_2h", "kind": "prior", "h": 2},
    {"name": "track_record_10c_24h", "kind": "prior", "h": 24},
)


def is_mention_series(ticker: str, category: Optional[str] = None, title: Optional[str] = None) -> bool:
    t = (ticker or "").upper()
    if "MENTION" in t:
        return True
    if (category or "").strip().lower() == "mentions":
        return True
    return bool(re.search(r"\bwhat will .{1,60} say\b", (title or "").lower()))


def word_of(m: Dict) -> str:
    """The word or phrase a market is about, normalized (custom_strike, else yes_sub_title)."""
    cs = m.get("custom_strike")
    w = None
    if isinstance(cs, dict) and cs:
        w = " ".join(str(v) for v in cs.values())
    elif isinstance(cs, str) and cs:
        w = cs
    w = w or m.get("yes_sub_title") or ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9/ ]+", " ", str(w).lower())).strip()


def compact(candles: Sequence[Dict]) -> List[List[float]]:
    """Kalshi candles -> [[end_ts, yes_bid close, yes_ask close], ...] (rows without both dropped)."""
    out = []
    for c in candles:
        e = _ts(c.get("end_period_ts"))
        bid, ask = _px(c.get("yes_bid"), "close"), _px(c.get("yes_ask"), "close")
        if e is not None and bid is not None and ask is not None:
            out.append([e, bid, ask])
    return out


def quote_at(candles: Sequence[Sequence[float]], t: int) -> Optional[Tuple[float, float]]:
    """(yes_bid, yes_ask) from the newest compact candle ending at or before t, at most 6h old."""
    best = None
    for e, bid, ask in candles:
        if e > t or t - e > MAX_CANDLE_AGE_S:
            continue
        if best is None or e > best[0]:
            best = (e, bid, ask)
    if best is None:
        return None
    _, bid, ask = best
    if not (0.0 <= bid <= ask <= 1.0) or ask <= 0.0 or bid >= 1.0:
        return None
    return bid, ask


def horizon_quotes(candles: Sequence[Sequence[float]], end: int, horizons: Sequence[int] = HORIZONS_H) -> Dict[int, Tuple[float, float]]:
    out = {}
    for h in horizons:
        q = quote_at(candles, end - h * 3600)
        if q is not None:
            out[h] = q
    return out


def said_time(event_markets: Sequence[Dict], candles: Dict[str, Sequence[Sequence[float]]],
               level: float = 0.95) -> Optional[int]:
    """First candle end at which a market of this event that settled YES had its mid at level+."""
    best = None
    for m in event_markets:
        if result_of(m) != 1.0:
            continue
        for e, bid, ask in candles.get(m.get("ticker")) or ():
            if (bid + ask) / 2.0 >= level and (best is None or e < best):
                best = int(e)
    return best


def anchors(markets: Sequence[Dict], candles: Dict[str, Sequence[Sequence[float]]]) -> Dict[str, int]:
    """{event_ticker: anchor}: said_time where found, else the latest close."""
    ends = event_ends(markets)
    by: Dict[str, List[Dict]] = defaultdict(list)
    for m in markets:
        by[m.get("event_ticker")].append(m)
    out = {}
    for ev, ms in by.items():
        if ev not in ends:
            continue
        st = said_time(ms, candles)
        out[ev] = st if st is not None and st <= ends[ev] else ends[ev]
    return out


def observations(markets: Sequence[Dict], quotes: Dict[str, Dict[int, Tuple[float, float]]],
                 horizons: Sequence[int] = HORIZONS_H, anchor: Optional[Dict[str, int]] = None) -> List[Dict]:
    """One row per (settled market, horizon) where it was open and quoted (quotes[ticker][h] from
    horizon_quotes), with the word's track
    record in the same series from events that ended before the observation time."""
    ends = event_ends(markets)
    # (series, word) -> [(event end, result)] for the track record
    hist: Dict[Tuple[str, str], List[Tuple[int, float]]] = defaultdict(list)
    for m in markets:
        r, ev = result_of(m), m.get("event_ticker")
        if r is not None and ev in ends:
            hist[(m.get("series_ticker") or "", word_of(m))].append((ends[ev], r))
    rows = []
    for m in markets:
        res, tk, ev = result_of(m), m.get("ticker"), m.get("event_ticker")
        if res is None or ev not in ends:
            continue
        end, opened, closed = ends[ev], _ts(m.get("open_time")), _ts(m.get("close_time"))
        at = (anchor or {}).get(ev, end)
        if opened is None or closed is None:
            continue
        series, word = m.get("series_ticker") or "", word_of(m)
        for h in horizons:
            t = at - h * 3600
            if not (opened <= t < closed):
                continue
            q = (quotes.get(tk) or {}).get(h)
            if q is None:
                continue
            prior = [r for e, r in hist[(series, word)] if e <= t]
            n = len(prior)
            rows.append({"ticker": tk, "event": ev, "series": series, "word": word, "h": h, "end": end, "anchor": at,
                         "bid": q[0], "ask": q[1], "mid": (q[0] + q[1]) / 2.0, "yes": res,
                         "prior_n": n, "prior_p": (sum(prior) + 1.0) / (n + 2.0)})
    return rows


def taker_fee(p: float) -> float:
    return TAKER_RATE * p * (1.0 - p)


def sell_pnl(r: Dict) -> float:
    return r["bid"] - taker_fee(r["bid"]) - r["yes"]


def buy_pnl(r: Dict) -> float:
    return r["yes"] - r["ask"] - taker_fee(r["ask"])


def prior_trade(r: Dict) -> Optional[float]:
    """Per-contract P&L of trading on the track record, or None (no trade)."""
    if r["prior_n"] < MIN_PRIOR_EVENTS:
        return None
    p = r["prior_p"]
    if p - r["ask"] - taker_fee(r["ask"]) >= EDGE:
        return buy_pnl(r)
    if r["bid"] - taker_fee(r["bid"]) - p >= EDGE:
        return sell_pnl(r)
    return None


def cluster_boot(pnls: List[Tuple[str, float]], seed: int = 13) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """(mean, 5th, 95th pct) resampling whole events."""
    if not pnls:
        return None, None, None
    by: Dict[str, List[float]] = defaultdict(list)
    for ev, x in pnls:
        by[ev].append(x)
    keys = list(by)
    mean = sum(x for _, x in pnls) / len(pnls)
    if len(keys) < 3:
        return mean, None, None
    sums = [sum(by[k]) for k in keys]
    cnts = [len(by[k]) for k in keys]
    rng = random.Random(seed)
    means = []
    for _ in range(BOOT):
        s = n = 0.0
        for _ in keys:
            i = rng.randrange(len(keys))
            s += sums[i]
            n += cnts[i]
        means.append(s / n)
    means.sort()
    return mean, means[int(0.05 * BOOT)], means[int(0.95 * BOOT) - 1]


def _stats(pnls: List[Tuple[str, float]]) -> Dict:
    m, lo, hi = cluster_boot(pnls)
    events = len({e for e, _ in pnls})
    verdict = ("too_few_events" if events < 30 else "edge" if (lo or -1) > 0 else
               "loses" if (hi or 1) < 0 else "inconclusive")
    r = lambda v: None if v is None else round(100 * v, 2)
    return {"trades": len(pnls), "events": events, "pnl_c": r(m), "pnl_c_p5": r(lo), "pnl_c_p95": r(hi), "verdict": verdict}


def _bucket(p: float) -> str:
    for lo, hi in zip(PRICE_BUCKETS, PRICE_BUCKETS[1:]):
        if p < hi or hi == 1.0:
            return "%02d-%02dc" % (round(lo * 100), round(hi * 100))
    return "?"


def summarize(rows: List[Dict]) -> Dict:
    rules = []
    for rule in RULES:
        rs = [r for r in rows if r["h"] == rule["h"]]
        if rule["kind"] == "calib":
            key = "bid" if rule["side"] == "sell" else "ask"
            fn = sell_pnl if rule["side"] == "sell" else buy_pnl
            pnls = [(r["event"], fn(r)) for r in rs if rule["lo"] <= r[key] <= rule["hi"]]
        else:
            pnls = [(r["event"], x) for r in rs for x in (prior_trade(r),) if x is not None]
        ends = sorted({r["end"] for r in rs})
        half = ends[len(ends) // 2] if ends else 0
        by_end = {r["event"]: r["end"] for r in rs}
        early = [(e, x) for e, x in pnls if by_end.get(e, 0) < half]
        late = [(e, x) for e, x in pnls if by_end.get(e, 0) >= half]
        rules.append(dict(rule, **_stats(pnls), first_half=_stats(early), second_half=_stats(late)))
    calib = defaultdict(list)
    for r in rows:
        calib[(r["h"], _bucket(r["mid"]))].append(r)
    table = []
    for (h, b), rs in sorted(calib.items()):
        table.append({"h": h, "bucket": b, "n": len(rs), "events": len({r["event"] for r in rs}),
                      "mean_mid": round(sum(r["mid"] for r in rs) / len(rs), 4),
                      "yes_rate": round(sum(r["yes"] for r in rs) / len(rs), 4),
                      "sell": _stats([(r["event"], sell_pnl(r)) for r in rs]),
                      "buy": _stats([(r["event"], buy_pnl(r)) for r in rs])})
    by_series = defaultdict(list)
    for r in rows:
        if r["h"] == 2:
            by_series[r["series"]].append(r)
    series = sorted(({"series": s, "n": len(rs), "events": len({r["event"] for r in rs}),
                      "mean_mid": round(sum(r["mid"] for r in rs) / len(rs), 4),
                      "yes_rate": round(sum(r["yes"] for r in rs) / len(rs), 4)} for s, rs in by_series.items()),
                    key=lambda x: -x["n"])
    return {"rows": len(rows), "markets": len({r["ticker"] for r in rows}), "events": len({r["event"] for r in rows}),
            "series_n": len(by_series), "rules": rules, "calibration": table, "by_series_2h": series[:80]}
