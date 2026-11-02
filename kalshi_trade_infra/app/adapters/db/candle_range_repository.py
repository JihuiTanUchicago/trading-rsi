"""Settled contracts and their candles WITH the period's ranges, for app.services.maker_scan.

load_history reads only each candle's closing bid/ask. A maker fill needs to know whether the next
period traded THROUGH a resting price, which lives in the candle's slimmed raw_json (market_history.
slim_candle_raw keeps al/ah, bl/bh, pl/ph: YES ask, YES bid and trade low/high). Older rows that were
never slimmed carry the full Kalshi candle, so both shapes are read.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from app.adapters.db.sql import _loads


def settled_contracts(cur, series: str, since: datetime, until: datetime) -> List[Dict]:
    """{ticker, event_ticker, close_time, result, settle_value} for the series' settled contracts with
    candles whose close falls in [since, until), oldest first."""
    cur.execute(
        """SELECT ticker, event_ticker, close_time, result, settle_value FROM hist_markets
           WHERE series_ticker=%s AND close_time >= %s AND close_time < %s AND candles_done=1
           ORDER BY close_time, ticker""",
        (series, since, until),
    )
    return list(cur.fetchall())


def _candle_num(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _candle_range(raw, slim: str, side: str, field: str) -> Optional[float]:
    if not isinstance(raw, dict):
        return None
    if slim in raw:
        return _candle_num(raw[slim])
    d = raw.get(side) or {}
    v = d.get(field + "_dollars")
    return _candle_num(v if v is not None else d.get(field))


def candle_ranges(cur, ticker: str, minutes: int) -> List[Dict]:
    """One contract's candles, oldest first: end_ts, bid, ask (closes) and ask_low, bid_high,
    trade_low, trade_high (None when the period had no such print)."""
    cur.execute(
        """SELECT end_period_ts, yes_bid_close, yes_ask_close, raw_json FROM market_candles
           WHERE ticker=%s AND period_interval_minutes=%s ORDER BY end_period_ts""",
        (ticker, minutes),
    )
    out = []
    for r in cur.fetchall():
        raw = _loads(r.get("raw_json"))
        out.append({
            "end_ts": int(r["end_period_ts"]),
            "bid": _candle_num(r.get("yes_bid_close")),
            "ask": _candle_num(r.get("yes_ask_close")),
            "ask_low": _candle_range(raw, "al", "yes_ask", "low"),
            "bid_high": _candle_range(raw, "bh", "yes_bid", "high"),
            "trade_low": _candle_range(raw, "pl", "price", "low"),
            "trade_high": _candle_range(raw, "ph", "price", "high"),
        })
    return out
