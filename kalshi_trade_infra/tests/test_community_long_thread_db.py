"""A long community thread against a real MySQL: the replies read is the newest page, and older pages are read by
reply id, so every reply can be reached. It read the oldest 100: on a heavy account's post with 121 replies the
thread never showed the newest 21, a reply just sent among them, and said nothing (walk 2026-11-01). Opt in as for
test_app_engine_db.py (APP_TEST_MYSQL=1)."""
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


def _user(cur, name):
    from app import repository as repo
    return repo.upsert_google_user(cur, "test:" + uuid.uuid4().hex, "thread-%s@example.com" % uuid.uuid4().hex[:8], name, None)


def _ctx(conn, user, pid, query=None):
    from app import server
    return server.Ctx(conn, user, (str(pid),), dict(query or {}), {}, SimpleNamespace())


def test_a_thread_past_one_page_reads_its_newest_replies_first_and_every_older_one_on_request(conn):
    from app import repository as repo
    from app.web.routes import community

    cur = conn.cursor()
    author, other = _user(cur, "Ana"), _user(cur, "Bo")
    pid = repo.create_post(cur, author["id"], "A popular post.", "note", None)
    for i in range(121):
        repo.add_comment(cur, pid, other["id"] if i % 2 else author["id"], "Reply %d" % (i + 1))
    noise = repo.create_post(cur, other["id"], "Another post.", "note", None)
    repo.add_comment(cur, noise, author["id"], "Not in the thread")
    conn.commit()

    first = community.post_comments(_ctx(conn, author, pid))
    bodies = [c["body"] for c in first["comments"]]
    assert len(bodies) == community.COMMENTS_PAGE == 100
    assert bodies[-1] == "Reply 121", "the newest reply is in the first read, by the reply box"
    assert bodies[0] == "Reply 22" and bodies == ["Reply %d" % i for i in range(22, 122)], "oldest first, no gaps"
    assert first["older"] is True

    older = community.post_comments(_ctx(conn, author, pid, {"before": first["comments"][0]["id"]}))
    assert [c["body"] for c in older["comments"]] == ["Reply %d" % i for i in range(1, 22)]
    assert older["older"] is False
    assert "Not in the thread" not in [c["body"] for c in first["comments"] + older["comments"]]

    # A thread that fits one page says nothing is older; the repository keeps its old call working.
    assert community.post_comments(_ctx(conn, author, noise))["older"] is False
    assert [c["body"] for c in repo.list_comments(cur, noise)] == ["Not in the thread"]
    # A bad `before` reads the newest page, never an error page.
    assert community.post_comments(_ctx(conn, author, pid, {"before": "x"}))["comments"][-1]["body"] == "Reply 121"
