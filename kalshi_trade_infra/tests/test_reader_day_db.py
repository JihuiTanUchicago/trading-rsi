"""Every "today" and every per-day money figure counts the reader's own calendar day, against a real MySQL.

The sports walk of 2026-11-01 found Home saying "Today (UTC)" to a reader in New York: at 9pm there the day had rolled
over at 7-8pm local and the figure dropped to $0.00, while game times and "today/tomorrow" on the market page already
went by the reader's calendar. The server now counts the day the reader's browser keeps in its cookies (tau_tz, the
offset; tau_tzn, the zone's name: app/services/reader_day.py) and says where that day began (day_start).

One account with paper, demo and live trades closed on both sides of midnight UTC and of the reader's midnight in New
York (on the day its clocks went back), Kolkata and Auckland, read at 02:00 UTC on Nov 2 (9pm in New York):
  - the dashboard's and the autopilot panel's day per book are the reader's, one figure on both;
  - the loss budget (and the live loss the daily limit counts) keeps 00:00 UTC: the risk logic's day, not the reader's;
  - Home's paper days are the reader's dates, a trade before the clock change on its own day by the offset it had;
  - trades.csv's last column says each close in the reader's zone; every older column is as it was;
  - without a zone cookie every figure is the UTC day's, as before.
Display only: what settles and every sum's rows are unchanged. Opt in as for test_app_engine_db.py (APP_TEST_MYSQL=1).
"""
import csv
import json
import io
import os
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from trading_core.domain.money import cents

pytestmark = pytest.mark.skipif(os.getenv("APP_TEST_MYSQL") != "1", reason="needs APP_TEST_MYSQL=1 and a MySQL")

NOW = datetime(2026, 11, 2, 2, 0)  # 21:00 EST Nov 1 in New York, 07:30 Nov 2 in Kolkata, 15:00 Nov 2 in Auckland
# Each slot's close (naive UTC), on either side of one midnight or another.
SLOTS = {
    "dst": datetime(2026, 10, 31, 4, 30),   # 00:30 EDT Oct 31 in New York (23:30 Oct 30 by today's EST offset)
    "ny_before": datetime(2026, 11, 1, 3, 30),   # 23:30 EDT Oct 31: before New York's midnight
    "ny_after": datetime(2026, 11, 1, 4, 30),    # 00:30 EDT Nov 1: after it
    "akl_before": datetime(2026, 11, 1, 10, 30),  # 23:30 NZDT Nov 1
    "akl_after": datetime(2026, 11, 1, 11, 30),   # 00:30 NZDT Nov 2
    "ist_before": datetime(2026, 11, 1, 18, 0),   # 23:30 IST Nov 1
    "ist_after": datetime(2026, 11, 1, 19, 0),    # 00:30 IST Nov 2
    "utc_before": datetime(2026, 11, 1, 23, 30),  # 18:30 EST Nov 1
    "utc_after": datetime(2026, 11, 2, 0, 30),    # 19:30 EST Nov 1
    "evening": datetime(2026, 11, 2, 1, 45),      # 20:45 EST Nov 1
}
# Where each reader's day began, as a naive UTC instant.
DAY = {"America/New_York": datetime(2026, 11, 1, 4, 0), "Asia/Kolkata": datetime(2026, 11, 1, 18, 30),
       "Pacific/Auckland": datetime(2026, 11, 1, 11, 0), None: datetime(2026, 11, 2, 0, 0)}
OFFSET = {"America/New_York": 300, "Asia/Kolkata": -330, "Pacific/Auckland": -780}


@pytest.fixture(scope="module")
def conn():
    from app import schema
    from data_pipeline.common import get_db_conn

    schema.main()
    c = get_db_conn()
    yield c
    c.cursor().execute("UPDATE app_deployments SET status='stopped' WHERE user_id IN "
                       "(SELECT id FROM app_users WHERE google_sub LIKE 'test:%%')")
    c.close()


@pytest.fixture
def pages(monkeypatch):
    """Payments off, live trading closed, no freshness read, the display memos cleared, the clock at NOW."""
    from app import repository as repo
    from app.adapters.stripe import billing
    from app.services import book_memo
    from app.trading import engine
    from app.web.routes import dashboard as dashboard_routes

    monkeypatch.setattr(billing, "STRIPE_SECRET_KEY", "")
    monkeypatch.setattr(engine, "LIVE_TRADING_ENABLED", False)
    monkeypatch.setattr(dashboard_routes, "_freshness", lambda c: {})
    monkeypatch.setattr(repo, "now_utc", lambda: NOW)
    book_memo.clear()
    yield
    book_memo.clear()


def _seed(cur):
    # The rows through their own modules: the fixture freezes the facade's clock for the pages read, which these
    # writes don't read (tests/test_repository_split.py).
    from app import repository as repo
    from app.adapters.db import deployment_repository as deployments, position_repository as positions, strategy_repository as strategies
    from app.trading import spec as specmod

    uid = repo.upsert_google_user(cur, "test:" + uuid.uuid4().hex, "readerday@example.invalid", "Sam", None)["id"]
    spec = specmod.validate({"side": "yes", "entry": {"min_edge": 0.05}, "sizing": {"stake": 5.0, "max_positions": 4}})
    sid = strategies.insert_strategy(cur, uid, None, None, "nfl_game", "custom", "Late-game fade", "t", spec, "passed",
                               {"capital": 40.0, "out_of_sample": {"trades": 40, "pnl": 6.0}})
    tag = uuid.uuid4().hex[:6].upper()
    pnl, n = {}, 0
    for k, mode in enumerate(("paper", "demo", "live")):
        dep = deployments.create_deployment(cur, uid, sid, "nfl_game", mode)
        for j, (slot, at) in enumerate(SLOTS.items()):
            n += 1
            # Each book's and slot's own amount, so a sum that took the wrong rows can't come out right.
            amount = Decimal("%d.%04d" % ((-1) ** (j + k) * (j + 1), 1250 + 37 * j + 401 * k))
            pnl[(mode, slot)] = amount
            positions.insert_closed_position(cur, dep, uid, mode, "KXNFLGAME%s-26NOV01-T%d" % (tag, n), "yes", 2, 0.4, 0.03,
                                        1.0, 0.0, float(amount), "settled", at,
                                        order_ref="%s:rd%d" % ("sim" if mode == "paper" else mode, n),
                                        opened_at=at - timedelta(hours=2), closed_at=at)
    return uid, pnl


def _ctx(conn, user, zone, query=None):
    from app import server

    cookies = {} if zone is None else {"tau_tz": str(OFFSET[zone]), "tau_tzn": quote(zone, safe="")}
    return server.Ctx(conn, user, (), dict(query or {}), {}, SimpleNamespace(cookies=lambda: dict(cookies)))


def _since(pnl, mode, start):
    return cents(sum((v for (m, slot), v in pnl.items() if m == mode and SLOTS[slot] >= start), Decimal(0)))


@pytest.mark.parametrize("zone", ["America/New_York", "Asia/Kolkata", "Pacific/Auckland", None])
def test_home_and_the_autopilot_panel_count_the_readers_own_day(conn, pages, zone):
    from app import repository as repo
    from app import server

    cur = conn.cursor()
    uid, pnl = _seed(cur)
    user = repo.get_user(cur, uid)
    dash = server.dashboard(_ctx(conn, user, zone))
    ap = server.autopilot_status(_ctx(conn, user, zone))
    start, utc = DAY[zone], DAY[None]
    assert dash["day_start"] == ap["day_start"] == start
    for m in ("paper", "demo", "live"):
        assert dash[m]["today_pnl"] == _since(pnl, m, start), (zone, m)
    for m in ("paper", "live"):
        assert ap["today_%s_pnl" % m] == dash[m]["today_pnl"], (zone, m)
    # The loss budget and the live loss the daily limit counts keep 00:00 UTC, whatever the reader's day.
    assert ap["utc_day_live_pnl"] == _since(pnl, "live", utc)
    assert ap["loss_headroom"] == cents(max(Decimal(0), Decimal(str(ap["daily_loss_limit"])) + min(Decimal(0), Decimal(str(ap["utc_day_live_pnl"])))))
    if zone == "America/New_York":
        # 9pm in New York: every trade since 00:00 EDT Nov 1 is today's, the evening's after 00:00 UTC included. The
        # UTC day had only the two since 7pm local, and the clock change didn't move the reader's midnight.
        assert dash["paper"]["today_pnl"] != _since(pnl, "paper", utc)
        assert _since(pnl, "paper", start) == cents(sum(v for (m, s), v in pnl.items() if m == "paper" and s not in ("dst", "ny_before")))
    # The paper days, each the reader's date, the same on the kept read.
    want = {}
    for (m, slot), v in pnl.items():
        if m == "paper":
            at = SLOTS[slot]
            if zone is not None:
                from zoneinfo import ZoneInfo
                from datetime import timezone
                d = at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(zone)).date()
            else:
                d = at.date()
            want[str(d)] = want.get(str(d), Decimal(0)) + v
    for read in (dash, server.dashboard(_ctx(conn, user, zone))):
        got = {str(r["d"]): Decimal(str(r["pnl"])) for r in read["paper"]["daily"]}
        assert got == want, (zone, got, want)
    if zone == "America/New_York":
        # 00:30 EDT Oct 31 is Oct 31 there, by the offset it fell under (today's EST offset said Oct 30).
        assert want["2026-10-31"] == pnl[("paper", "dst")] + pnl[("paper", "ny_before")]
        assert "2026-11-02" not in want


def test_a_zone_cookie_naming_another_zone_than_the_offset_goes_by_the_offset(conn, pages):
    from app import repository as repo
    from app import server

    cur = conn.cursor()
    uid, pnl = _seed(cur)
    user = repo.get_user(cur, uid)
    ctx = server.Ctx(conn, user, (), {}, {}, SimpleNamespace(cookies=lambda: {"tau_tz": "300", "tau_tzn": "Asia%2FKolkata"}))
    dash = server.dashboard(ctx)
    assert dash["day_start"] == datetime(2026, 11, 1, 5, 0)  # UTC-5's midnight: the reader moved, the offset wins
    assert dash["paper"]["today_pnl"] == _since(pnl, "paper", datetime(2026, 11, 1, 5, 0))


def test_researchs_per_day_bars_are_the_readers_own_days_on_one_kept_board(conn, monkeypatch):
    # Research's per-day bars were grouped by UTC day and said "(UTC)". Sessions on either side of yesterday's midnight
    # in each zone (timed off the real clock, which the board's window reads: UTC_TIMESTAMP()), each with its own
    # count of ideas (a power of two), so each day's sum names exactly the sessions it took. Each reader's bars are
    # their own days, from one board worked out once for every zone; every other figure is the same for all of them.
    from datetime import timezone
    from zoneinfo import ZoneInfo

    from app import repository as repo
    from app import server
    from app.adapters.db import agent_run_repository as agent_runs
    from app.research import rsi

    cur = conn.cursor()
    uid = repo.upsert_google_user(cur, "test:" + uuid.uuid4().hex, "readerdays@example.invalid", "Ria", None)["id"]
    user = repo.get_user(cur, uid)
    cur.execute("SELECT UTC_TIMESTAMP() AS t")
    now = cur.fetchone()["t"]
    zones = ["America/New_York", "Asia/Kolkata", "Pacific/Auckland"]

    def midnight(zone):  # yesterday's midnight there (naive UTC): always in the past, within the board's 14 days
        at = (now - timedelta(days=1)).replace(tzinfo=timezone.utc)
        loc = at.astimezone(ZoneInfo(zone)) if zone else at
        return loc.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)

    times = sorted({midnight(z) + timedelta(minutes=d) for z in zones + [None] for d in (-20, 20)})
    ideas = {}
    for j, at in enumerate(times):
        rid = agent_runs.create_run(cur, uid, "research", "nfl_game", None, "Idea %d" % j)
        cur.execute("UPDATE app_agent_runs SET status='succeeded', created_at=%s, started_at=%s, finished_at=%s, "
                    "hypotheses_tried=%s WHERE id=%s", (at, at, at + timedelta(minutes=5), 2 ** j, rid))
        ideas[at] = 2 ** j
    built = []
    real = rsi.load_scoreboard
    monkeypatch.setattr(rsi, "load_scoreboard", lambda *a, **k: built.append(1) or real(*a, **k))
    server._SHARED.clear()
    boards = {}
    for zone in zones + [None]:
        if zone is None:
            cookies = {}
        else:
            off = -int(now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(zone)).utcoffset().total_seconds()) // 60
            cookies = {"tau_tz": str(off), "tau_tzn": quote(zone, safe="")}
        ctx = server.Ctx(conn, user, (), {"days": "14"}, {}, SimpleNamespace(cookies=lambda c=cookies: dict(c)))
        board = boards[zone] = server.research_scoreboard(ctx)
        want = {}
        for at, n in ideas.items():
            d = at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(zone)).date() if zone else at.date()
            want[d.isoformat()] = want.get(d.isoformat(), 0) + n
        assert {d["day"]: d["explored"] for d in board["days"]} == want, zone
        assert board.get("days_local", False) is (zone is not None) and "day_cards" not in json.dumps(board, default=str)
    assert len(built) == 1  # one board for every zone
    assert len({tuple(d["day"] for d in b["days"]) for b in boards.values()}) > 1  # and the zones' days differ
    for b in boards.values():
        assert {k: v for k, v in b.items() if k not in ("days", "days_local")} == \
            {k: v for k, v in boards[None].items() if k not in ("days", "days_local")}
        assert sum(d["explored"] for d in b["days"]) == b["total"]["explored"] == sum(ideas.values())


@pytest.mark.parametrize("zone", ["America/New_York", "Asia/Kolkata", None])
def test_the_trades_file_says_each_close_in_the_readers_zone_in_a_last_column(conn, pages, zone):
    from app import repository as repo
    from app import server

    cur = conn.cursor()
    uid, pnl = _seed(cur)
    user = repo.get_user(cur, uid)
    _, _, _, data = server.trades_csv(_ctx(conn, user, zone, {"mode": "paper"}))
    rows = list(csv.reader(io.StringIO(data)))
    assert rows[0][:15] == ["closed_at_utc", "opened_at_utc", "mode", "strategy", "strategy_title", "ticker", "side",
                            "contracts", "entry_price", "exit_price", "entry_fee", "exit_fee", "realized_pnl",
                            "close_reason", "market"]
    assert rows[0][15:] == ["closed_at_local"]
    local = {r[0]: r[15] for r in rows[1:]}
    assert len(local) == len(SLOTS)
    want = {"America/New_York": "2026-11-01T19:30:00-05:00", "Asia/Kolkata": "2026-11-02T06:00:00+05:30",
            None: "2026-11-02T00:30:00+00:00"}[zone]
    assert local["2026-11-02T00:30:00Z"] == want
    if zone == "America/New_York":
        assert local["2026-10-31T04:30:00Z"] == "2026-10-31T00:30:00-04:00"  # before the clock change, EDT
    # The file's realized_pnl still sums to the book's all-time figure.
    assert cents(sum(Decimal(r[12]) for r in rows[1:])) == cents(sum(v for (m, _), v in pnl.items() if m == "paper"))
