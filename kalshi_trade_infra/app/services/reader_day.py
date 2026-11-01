"""The reader's own calendar day, for every figure that says "today" or groups money by day.

Home said "Today (UTC)" to a reader in New York: at 9pm there the day had rolled over at 7-8pm local and
the paper figure had dropped to $0.00, while game times and "today/tomorrow" on the market page already
went by the reader's calendar. Only the browser knows its zone: boot.js keeps its UTC offset in the tau_tz
cookie (getTimezoneOffset(), minutes, 240 for UTC-4) and its zone's name in tau_tzn (Intl, URL-encoded) on
every load, so every request the page makes after that carries both, and a page shell carries them from
the load before.

reader_zone() reads them: the named zone when it is at the page's offset now (so a day that crossed a clock
change still starts at the reader's own midnight), else the offset alone, else None (no cookie yet: a
first load on this device), and a figure then counts the UTC day as before. Each payload that counts a
day says where its day started (day_start), so the page names it in the reader's words or as the UTC day
it was, never one for the other.

Display only: grouping and labels. Nothing here decides what settles, what a risk limit counts (the daily
loss limit and its loss budget keep 00:00 UTC) or what is billed.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone, tzinfo
from typing import List, Optional, Tuple, Union
from urllib.parse import unquote

from app import repository as repo

TZ_COOKIE = "tau_tz"         # getTimezoneOffset() in minutes, as boot.js writes it on every load
TZ_NAME_COOKIE = "tau_tzn"   # the browser's time zone name (Intl), URL-encoded, as boot.js writes it on every load
_TZ_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+){0,2}$")
_OFFSET = re.compile(r"^-?\d{1,4}$")

Offsets = Union[int, List[Tuple[Optional[datetime], int]]]


def offset_minutes(raw) -> Optional[int]:
    """A getTimezoneOffset() value (minutes behind UTC) from a query or cookie, or None when absent or not sane."""
    s = str(raw if raw is not None else "").strip()
    if not _OFFSET.match(s):
        return None
    v = int(s)
    return v if -840 <= v <= 840 else None


def zone_offset(zone: tzinfo, at: datetime) -> int:
    """The zone's offset from UTC at the naive UTC instant `at`, in minutes as getTimezoneOffset() says it (360 for UTC-6)."""
    return -int(at.replace(tzinfo=timezone.utc).astimezone(zone).utcoffset().total_seconds()) // 60


def named_zone(name: Optional[str], offset: Optional[int], now: Optional[datetime] = None) -> Optional[tzinfo]:
    """The IANA zone the browser names, when it is a zone this server knows and (with an offset) is at that offset
    now; None otherwise (a cookie naming another zone than the page's offset: the reader moved, and the offset wins)."""
    name = unquote(name or "")
    if not name or len(name) > 64 or not _TZ_NAME.match(name):
        return None
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(name)
        if offset is not None and zone_offset(zone, now or repo.now_utc()) != offset:
            return None
        return zone
    except Exception:  # noqa: BLE001 - an unknown zone or no zone data: the offset alone, as before
        return None


def _cookies(ctx) -> dict:
    handler = getattr(ctx, "handler", None)
    try:
        return handler.cookies() if handler is not None and hasattr(handler, "cookies") else {}
    except Exception:  # noqa: BLE001 - no headers (a test's or shell's stand-in): no zone
        return {}


def reader_zone(ctx, now: Optional[datetime] = None) -> Optional[tzinfo]:
    """The reader's time zone for this request: ?tz= (the page's offset, where a page sends one) or else the tau_tz
    cookie for the offset, and the tau_tzn cookie for the zone's name. The named zone when it agrees with the
    offset; a fixed offset when only that is known; None when neither is (a figure then counts the UTC day)."""
    cookies = _cookies(ctx)
    query = getattr(ctx, "query", None) or {}
    offset = offset_minutes(query.get("tz")) if query.get("tz") is not None else None
    if offset is None:
        offset = offset_minutes(cookies.get(TZ_COOKIE))
    zone = named_zone(cookies.get(TZ_NAME_COOKIE), offset, now)
    if zone is not None:
        return zone
    return None if offset is None else timezone(timedelta(minutes=-offset))


def day_start(zone: Optional[tzinfo], now: Optional[datetime] = None) -> datetime:
    """The naive UTC instant the reader's day began (their last midnight), or 00:00 UTC today without a zone."""
    now = now or repo.now_utc()
    if zone is None:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    local = now.replace(tzinfo=timezone.utc).astimezone(zone)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)  # the zone's offset at midnight, not now's
    return midnight.astimezone(timezone.utc).replace(tzinfo=None)


def local_date(at: datetime, zone: Optional[tzinfo]):
    """The reader's calendar date of the naive UTC instant `at` (its UTC date without a zone)."""
    return at.date() if zone is None else at.replace(tzinfo=timezone.utc).astimezone(zone).date()


def offsets(zone: Optional[tzinfo], since: datetime, now: Optional[datetime] = None) -> Offsets:
    """The reader's offset for each row from `since` on, as SQL that buckets by the reader's day needs it
    (billing_repository._day_shift, position_repository.daily_pnl): one offset in getTimezoneOffset() minutes when
    it held over the whole window, else [(from, minutes)] oldest first, the first from None, the offset in force
    from each naive UTC instant on. With one offset for every row, the days before a clock change were cut an hour
    off the reader's midnight. 0 (UTC) without a zone."""
    if zone is None:
        return 0
    now = now or repo.now_utc()
    try:
        t = since.replace(second=0, microsecond=0)  # clock changes fall on whole minutes
        segs: List[Tuple[Optional[datetime], int]] = [(None, zone_offset(zone, t))]
        while t < now:  # a day at a time; a change inside a day is found to the minute
            nxt = min(t + timedelta(days=1), now)
            if zone_offset(zone, nxt) != segs[-1][1]:
                lo, hi = 0, int((nxt - t).total_seconds() // 60) + 1
                while hi - lo > 1:
                    mid = (lo + hi) // 2
                    lo, hi = (mid, hi) if zone_offset(zone, t + timedelta(minutes=mid)) == segs[-1][1] else (lo, mid)
                at = t + timedelta(minutes=hi)
                segs.append((at, zone_offset(zone, at)))
            t = nxt
    except Exception:  # noqa: BLE001 - no zone data: the offset now, as before
        return zone_offset(zone, now)
    return segs if len(segs) > 1 else segs[0][1]


def offsets_key(tz: Offsets) -> Tuple:
    """A hashable form of offsets(), for a kept read's key."""
    return (tz,) if isinstance(tz, int) else tuple(tz)


def local_iso(at: Union[datetime, str, None], zone: Optional[tzinfo]) -> str:
    """A naive UTC instant (or the trades file's "...Z" text) as ISO 8601 in the reader's zone with its offset
    ("2026-10-31T21:05:00-04:00"), "" for none; "+00:00" without a zone, so the value always says its offset."""
    if at is None or at == "":
        return ""
    if isinstance(at, str):
        try:
            at = datetime.fromisoformat(at[:-1] if at.endswith("Z") else at)
        except ValueError:
            return ""
    if at.tzinfo is not None:
        at = at.astimezone(timezone.utc).replace(tzinfo=None)
    return at.replace(microsecond=0, tzinfo=timezone.utc).astimezone(zone or timezone.utc).isoformat()
