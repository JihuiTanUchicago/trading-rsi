"""What a research session cost, on its page, in the Research list and on Billing, agrees against a real MySQL.

One account with research sessions in every state a customer sees: running (a run-until-stopped session started
over 24 hours ago, still debiting), finished, failed on its first short call, stopped by the user (the call in
flight billed 20 seconds after the stop), started by the autopilot, and one that ran across midnight in
America/Chicago the night before the clock went back (2026-11-01 07:00 UTC), plus an autopilot check-in. The
usage rows are written straight into the ledger, as billing.charge_usage writes them; no model is called and
nothing is billed. Then:

- each session's cost on its page and its row in the Research list is its own ledger rows, to the cent;
- Billing's lines for a session add up to those rows exactly, its totals end at the balance, and its split by
  source counts only the autopilot's own sessions as autopilot research;
- the statement's days are the viewer's days on both sides of the clock change: with the browser's zone
  named (the tau_tzn cookie boot.js keeps) the midnight session is an Oct 31 line and a Nov 1 line, and
  "Today" holds only today's calls.

Display reads only; no billing math is touched. Opt in as for test_app_engine_db.py (APP_TEST_MYSQL=1).
"""
import os
import uuid
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(os.getenv("APP_TEST_MYSQL") != "1", reason="needs APP_TEST_MYSQL=1 and a MySQL")

NOW = datetime(2026, 11, 1, 15, 34)  # 09:34 CST, the day Chicago's clocks went back (07:00 UTC)
MEMO = "Agent usage · us.anthropic.claude-opus-4-6-v1"
CALL = Decimal("0.425000")


def _cents(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@pytest.fixture
def account(monkeypatch):
    from app import repository as repo
    from app import schema
    from app.adapters.db import agent_run_repository as runs_repo  # its own clock: the run's times are set below
    from data_pipeline.common import get_db_conn

    schema.main()
    conn = get_db_conn()
    cur = conn.cursor()
    monkeypatch.setattr(repo, "now_utc", lambda: NOW)
    user = repo.upsert_google_user(cur, "test:" + uuid.uuid4().hex, "agree-%s@example.com" % uuid.uuid4().hex[:8], "A", None)
    uid = user["id"]
    cur.execute("UPDATE app_users SET created_at=%s, autopilot=1 WHERE id=%s", (NOW - timedelta(days=8), uid))
    cur.execute("DELETE FROM app_credit_ledger WHERE user_id=%s", (uid,))
    cur.execute("INSERT INTO app_credit_ledger (user_id, kind, amount_usd, memo, created_at) VALUES (%s,'bonus','20','Welcome credits',%s)",
                (uid, NOW - timedelta(days=8)))
    exact = {}

    def session(key, status, created, calls, brief=False, forever=False, kind="research", amount=CALL, finished=None):
        rid = runs_repo.create_run(cur, uid, kind, "btc15m" if kind == "research" else None, None, "Audit " + key,
                              max_hypotheses=None if forever else 5, run_forever=forever, brief=brief)
        cur.execute("UPDATE app_agent_runs SET status=%s, created_at=%s, started_at=%s, heartbeat_at=%s, finished_at=%s WHERE id=%s",
                    (status, created, created, NOW if status == "running" else calls[-1], finished, rid))
        for t in calls:
            cur.execute("INSERT INTO app_credit_ledger (user_id, kind, amount_usd, memo, run_id, created_at) "
                        "VALUES (%s,'usage',%s,%s,%s,%s)", (uid, str(-amount), MEMO, rid, t))
        exact[key] = (rid, amount * len(calls), calls)
        return rid

    c1 = NOW - timedelta(hours=26, minutes=30)
    session("running", "running", c1, [c1 + timedelta(hours=h) for h in range(0, 27, 2)], forever=True)
    c2 = NOW - timedelta(hours=25, minutes=30)
    session("finished", "succeeded", c2, [c2 + timedelta(minutes=4 * i) for i in range(1, 6)], finished=c2 + timedelta(minutes=21))
    c3 = NOW - timedelta(hours=23)
    session("failed", "failed", c3, [c3 + timedelta(seconds=40)], amount=Decimal("0.011000"), finished=c3 + timedelta(seconds=45))
    c4 = NOW - timedelta(hours=5, minutes=30)
    session("cancelled", "cancelled", c4, [c4 + timedelta(minutes=3), c4 + timedelta(minutes=8), c4 + timedelta(minutes=12, seconds=20)],
            forever=True, finished=c4 + timedelta(minutes=12))
    c5 = NOW - timedelta(hours=3, minutes=30)
    session("autopilot", "succeeded", c5, [c5 + timedelta(minutes=6 * i) for i in range(1, 5)], brief=True, finished=c5 + timedelta(minutes=26))
    c6 = datetime(2026, 10, 31, 23, 30)  # 18:30 CDT Oct 31 to 00:20 CDT Nov 1
    session("midnight", "succeeded", c6, [c6 + timedelta(minutes=50 * i) for i in range(1, 8)], finished=c6 + timedelta(hours=6))
    session("review", "succeeded", NOW - timedelta(hours=2), [NOW - timedelta(hours=2)], kind="review", amount=Decimal("0.110000"))
    conn.commit()
    yield conn, cur, uid, exact
    cur.execute("DELETE FROM app_credit_ledger WHERE user_id=%s", (uid,))
    cur.execute("DELETE FROM app_agent_runs WHERE user_id=%s", (uid,))
    cur.execute("DELETE FROM app_users WHERE id=%s", (uid,))
    conn.commit()
    conn.close()


def _ctx(conn, cur, uid, params=(), query=None, cookies=None):
    from app import repository as repo
    from app.web.http import Ctx

    handler = SimpleNamespace(cookies=lambda: dict(cookies or {}))
    return Ctx(conn, repo.get_user(cur, uid), tuple(str(p) for p in params), query or {}, {}, handler)


def test_each_sessions_cost_is_its_ledger_rows_on_its_page_its_row_and_billing(account):
    from app.web.routes import billing as billing_routes
    from app.web.routes import research as research_routes

    conn, cur, uid, exact = account
    listed = {r["id"]: r for r in research_routes.list_runs(_ctx(conn, cur, uid))["runs"]}
    statement = billing_routes.billing_figures(_ctx(conn, cur, uid, query={"statement": "1", "tz": "360"},
                                                    cookies={"tau_tzn": "America%2FChicago"}))
    for key, (rid, total, _calls) in exact.items():
        if key == "review":
            continue
        page = research_routes.get_run(_ctx(conn, cur, uid, params=(rid,), query={"after": "0"}))["run"]
        assert Decimal(str(page["spent_usd"])) == _cents(total), key  # a stopped or failed session: what it took
        assert listed[rid]["spent_usd"] == page["spent_usd"], key
        lines = [ln for ln in statement["ledger"] if (ln.get("session") or {}).get("id") == rid]
        assert lines and sum(Decimal(str(ln["amount"])) for ln in lines) == -total, key  # Billing's lines, exactly
    usage = sum(t for _r, t, _c in exact.values())
    math = statement["math"]
    assert Decimal(str(math["usage"])) == _cents(usage)
    assert Decimal(str(math["end"])) == _cents(Decimal(20) - usage) == _cents(statement["balance"])
    by = statement["spent_7d_by"]  # the autopilot's research is its own sessions only; the check-in apart
    assert Decimal(str(by["autopilot_research"])) == _cents(exact["autopilot"][1])
    assert Decimal(str(by["autopilot_review"])) == _cents(exact["review"][1])


def test_the_statements_days_are_the_viewers_on_both_sides_of_the_clock_change(account):
    from app.web.routes import billing as billing_routes

    conn, cur, uid, exact = account
    q = {"statement": "1", "tz": "360"}  # CST now; the evening of Oct 31 was CDT (300)
    rid = exact["midnight"][0]

    def lines(cookies):
        led = billing_routes.billing_figures(_ctx(conn, cur, uid, query=q, cookies=cookies))["ledger"]
        return led, sorted((ln["day"], ln["calls"]) for ln in led if (ln.get("session") or {}).get("id") == rid)

    led, midnight = lines({"tau_tzn": "America%2FChicago"})
    # 00:20 CDT Nov 1 is Nov 1's; the six calls before Chicago's midnight are Oct 31's.
    assert midnight == [("2026-10-31", 6), ("2026-11-01", 1)]
    today = sum(Decimal(str(ln["amount"])) for ln in led if ln["day"] == "2026-11-01" and ln["kind"] == "usage")
    # Today's usage: every call from 05:00 UTC (Chicago's midnight, CDT) on.
    want = sum(CALL if t >= datetime(2026, 11, 1, 5) else Decimal(0)
               for key, (_r, _t, calls) in exact.items() if key not in ("failed", "review") for t in calls)
    assert today == -(want + exact["review"][1])
    # Without the zone (cookies blocked, or an older page) the browser's offset counts every row, as before.
    assert lines({})[1] == [("2026-10-31", 7)]
    # A zone that isn't at the page's offset now (the cookie is from another zone) is not used either.
    assert lines({"tau_tzn": "Europe%2FLondon"})[1] == [("2026-10-31", 7)]
    assert lines({"tau_tzn": "..%2F..%2Fetc%2Fpasswd"})[1] == [("2026-10-31", 7)]
