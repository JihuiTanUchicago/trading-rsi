"""app/services/reader_day.py: the reader's own calendar day, read from the zone their browser keeps in its cookies.

Home said "Today (UTC)" to a reader in New York and its figure dropped to $0.00 at 7-8pm local (walk 2026-11-01).
Every page that counts a day (Home, the autopilot panel, Home's paper days, Billing's statement, trades.csv) now
reads the reader's zone here, once. These pin how: the named zone wins only when it agrees with the page's offset,
a day that crossed a clock change still starts at the reader's own midnight, and without a zone it is the UTC day.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.adapters.db import sql
from app.services import reader_day

NOW = datetime(2026, 11, 2, 2, 0)  # 9pm EST Nov 1 in New York, on the day its clocks went back


def _ctx(cookies=None, query=None):
    return SimpleNamespace(query=query or {}, handler=SimpleNamespace(cookies=lambda: dict(cookies or {})))


@pytest.mark.parametrize("zone, offset, start", [
    ("America/New_York", "300", datetime(2026, 11, 1, 4, 0)),    # 00:00 EDT: the clock change at 06:00 UTC doesn't move it
    ("Asia/Kolkata", "-330", datetime(2026, 11, 1, 18, 30)),
    ("Pacific/Auckland", "-780", datetime(2026, 11, 1, 11, 0)),
    ("UTC", "0", datetime(2026, 11, 2, 0, 0)),
])
def test_the_day_starts_at_the_readers_own_midnight(zone, offset, start):
    z = reader_day.reader_zone(_ctx({"tau_tz": offset, "tau_tzn": zone.replace("/", "%2F")}), NOW)
    assert getattr(z, "key", None) == zone
    assert reader_day.day_start(z, NOW) == start


def test_without_a_zone_the_day_is_the_utc_day_and_an_offset_alone_is_a_fixed_offset():
    assert reader_day.reader_zone(_ctx(), NOW) is None
    assert reader_day.day_start(None, NOW) == datetime(2026, 11, 2, 0, 0)
    z = reader_day.reader_zone(_ctx({"tau_tz": "300"}), NOW)
    assert z == timezone(timedelta(hours=-5)) and reader_day.day_start(z, NOW) == datetime(2026, 11, 1, 5, 0)
    # A page's own ?tz= wins over the cookie's offset; nonsense is no zone at all.
    assert reader_day.reader_zone(_ctx({"tau_tz": "300"}, {"tz": "-330"}), NOW) == timezone(timedelta(hours=5, minutes=30))
    for bad in ("", "abc", "99999", "1e3", "-0x1"):
        assert reader_day.reader_zone(_ctx({"tau_tz": bad}), NOW) is None, bad


def test_a_zone_name_that_disagrees_with_the_offset_or_is_not_a_zone_is_left_out():
    # The reader moved since the name was kept: the page's offset wins.
    assert reader_day.reader_zone(_ctx({"tau_tz": "300", "tau_tzn": "Asia%2FKolkata"}), NOW) == timezone(timedelta(hours=-5))
    for name in ("Not%2FAZone", "..%2F..%2Fetc%2Fpasswd", "x" * 80, "<script>"):
        assert reader_day.named_zone(name, None, NOW) is None, name
    assert reader_day.named_zone("Asia%2FKolkata", None, NOW).key == "Asia/Kolkata"


def test_offsets_follow_a_clock_change_to_the_minute_for_sql_that_groups_by_day():
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    assert reader_day.offsets(ny, datetime(2026, 10, 1), NOW) == [(None, 240), (datetime(2026, 11, 1, 6, 0), 300)]
    assert reader_day.offsets(ZoneInfo("Asia/Kolkata"), datetime(2026, 10, 1), NOW) == -330
    assert reader_day.offsets(None, datetime(2026, 10, 1), NOW) == 0
    shift, params = sql.day_shift_sql(reader_day.offsets(ny, datetime(2026, 10, 1), NOW), "closed_at")
    assert shift == "(CASE WHEN closed_at < %s THEN %s ELSE %s END)" and params == (datetime(2026, 11, 1, 6, 0), 240, 300)
    assert sql.day_shift_sql(-330, "closed_at") == ("%s", (-330,))


def test_local_iso_says_each_instant_with_its_own_offset():
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    assert reader_day.local_iso("2026-11-02T00:30:00Z", ny) == "2026-11-01T19:30:00-05:00"
    assert reader_day.local_iso("2026-10-31T04:30:00Z", ny) == "2026-10-31T00:30:00-04:00"
    assert reader_day.local_iso(datetime(2026, 11, 2, 0, 30), None) == "2026-11-02T00:30:00+00:00"
    assert reader_day.local_iso("", ny) == reader_day.local_iso(None, ny) == reader_day.local_iso("garbage", ny) == ""


def test_billings_statement_days_read_the_same_zone_as_before(monkeypatch):
    # Billing's statement already went by the reader's day; it now reads it through the same helper, same answers.
    from app.web.routes import billing as billing_routes

    monkeypatch.setattr(reader_day.repo, "now_utc", lambda: NOW)
    ctx = SimpleNamespace(query={"tz": "300"}, handler=SimpleNamespace(cookies=lambda: {"tau_tzn": "America%2FChicago"}))
    assert billing_routes._statement_tz(ctx, NOW - timedelta(days=30)) == 300  # Chicago is at 360 now: the offset wins
    ctx = SimpleNamespace(query={"tz": "300"}, handler=SimpleNamespace(cookies=lambda: {"tau_tzn": "America%2FNew_York"}))
    assert billing_routes._statement_tz(ctx, NOW - timedelta(days=30)) == [(None, 240), (datetime(2026, 11, 1, 6, 0), 300)]
    ctx = SimpleNamespace(query={"tz": "-330"}, handler=SimpleNamespace(cookies=lambda: {"tau_tzn": "Asia%2FKolkata"}))
    assert billing_routes._statement_tz(ctx, NOW - timedelta(days=30)) == -330
