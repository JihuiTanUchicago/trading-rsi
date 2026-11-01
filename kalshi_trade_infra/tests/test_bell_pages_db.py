"""The bell's older pages against a real MySQL: Show older notices reads ?before=<notice id> through the same endpoint,
every notice is reached once, older pages group as the first page does, reading them marks nothing read, and the read
is LIMIT-bounded on the user's own index. "Older notices aren't listed here" led nowhere: past the newest 20 lines a
customer couldn't read an older notice at all (walk 2026-11-01). Opt in as for test_app_engine_db.py (APP_TEST_MYSQL=1)."""
import os
import uuid
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(os.getenv("APP_TEST_MYSQL") != "1", reason="needs APP_TEST_MYSQL=1 and a MySQL")


@pytest.fixture(scope="module")
def conn():
    from app import schema
    from data_pipeline.common import get_db_conn

    schema.main()
    c = get_db_conn()
    yield c
    c.close()


def _user(cur):
    from app import repository as repo
    return repo.upsert_google_user(cur, "test:" + uuid.uuid4().hex, "pages-%s@example.com" % uuid.uuid4().hex[:8], "P", None)


def _ctx(conn, user, query=None):
    from app import server
    return server.Ctx(conn, user, (), dict(query or {}), {}, SimpleNamespace())


def test_show_older_notices_reaches_every_notice_once_grouped_alike_and_marks_nothing_read(conn):
    from app import repository as repo
    from app.web.routes import account

    cur = conn.cursor()
    user, other = _user(cur), _user(cur)
    uid = user["id"]
    # 260 notices: replies to 52 posts in runs of 5, the oldest 40 read; another user's notices between them.
    for i in range(260):
        repo.add_notification(cur, uid, "comment", "Reply %d" % i, "/community#post-%d" % (i // 5))
        if i % 7 == 0:
            repo.add_notification(cur, other["id"], "comment", "Not yours %d" % i, "/community#post-%d" % (i // 5))
    cur.execute("UPDATE app_notifications SET read_at=UTC_TIMESTAMP() WHERE user_id=%s ORDER BY id LIMIT 40", (uid,))
    conn.commit()
    cur.execute("SELECT id, read_at FROM app_notifications WHERE user_id=%s", (uid,))
    before_state = {r["id"]: r["read_at"] for r in cur.fetchall()}

    first = account.notifications(_ctx(conn, user))
    assert first["older"] is True and first["unread"] == 220 and isinstance(first["next_before"], int)
    lines, page = list(first["items"]), first
    while page["older"]:
        page = account.notifications(_ctx(conn, user, {"before": page["next_before"]}))
        assert set(page) == {"items", "older", "next_before"}
        lines += page["items"]
    assert page["next_before"] is None
    bodies = [n["body"] for n in lines]
    assert bodies == ["Reply %d" % i for i in range(259, -1, -5)], "newest first, one line per post, no gaps or repeats"
    assert all(n["more"] == 4 and n["alike"] is False for n in lines)
    assert sum(1 + n["more"] for n in lines) == 260
    assert [n["read"] for n in lines[-8:]] == [True] * 8 and lines[-9]["read"] is False
    assert not any("Not yours" in b for b in bodies)
    # Reading the older pages changed no notice's read state; marking read is POST /api/notifications/read's alone.
    cur.execute("SELECT id, read_at FROM app_notifications WHERE user_id=%s", (uid,))
    assert {r["id"]: r["read_at"] for r in cur.fetchall()} == before_state
    assert repo.count_unread_notifications(cur, uid) == 220


def test_an_older_page_is_a_limit_bounded_read_on_the_users_index(conn):
    from app import repository as repo
    from app.services import bell

    cur = conn.cursor()
    uid = _user(cur)["id"]
    for i in range(150):
        repo.add_notification(cur, uid, "deployed", "Notice %d" % i, "/strategies/%d" % i)
    conn.commit()
    cur.execute("ANALYZE TABLE app_notifications")
    cur.fetchall()
    newest = repo.list_notifications(cur, uid, limit=bell.READ_ROWS + 1)
    cursor = newest[bell.READ_ROWS - 1]["id"]
    older = repo.list_notifications(cur, uid, limit=bell.READ_ROWS + 1, before=cursor)
    assert [r["body"] for r in older] == ["Notice %d" % i for i in range(49, -1, -1)]
    statements = []
    real = cur.execute

    def spy(sql, args=None):
        statements.append((sql, args))
        return real(sql, args)

    cur.execute = spy
    try:
        repo.list_notifications(cur, uid, limit=bell.READ_ROWS + 1, before=cursor)
    finally:
        cur.execute = real
    sql, args = statements[-1]
    assert "LIMIT %s" in sql and args[-1] == bell.READ_ROWS + 1
    cur.execute("EXPLAIN " + sql, args)
    plan = {p["table"]: p for p in cur.fetchall()}
    # Never the primary key walked down from the cursor through every user's notices: the user's index (user_id,
    # read_at, id), the user's rows only. No index has (user_id, id), so they are sorted (a filesort the LIMIT bounds).
    assert plan["n"]["key"] == "idx_notif_user", plan
    assert plan["st"]["type"] == "eq_ref", plan
