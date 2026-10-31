"""Deleting your own community post against a real MySQL: the post goes with its replies, likes,
reactions and photo, so nothing it shared stays readable; someone else's post can't be deleted; a
notice that linked it stays and opens the feed saying the post is gone. Opt in as for
test_app_engine_db.py (APP_TEST_MYSQL=1)."""
import os
import uuid

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
    return repo.upsert_google_user(cur, "test:" + uuid.uuid4().hex, "del-%s@example.com" % uuid.uuid4().hex[:8], "D", None)["id"]


def _count(cur, sql, arg):
    cur.execute(sql, (arg,))
    return int(cur.fetchone()["n"])


def test_the_author_deletes_a_post_with_everything_hanging_off_it(conn):
    from app import repository as repo

    cur = conn.cursor()
    author, other = _user(cur), _user(cur)
    image = repo.store_image(cur, author, "image/png", b"\x89PNG fake")
    pid = repo.create_post(cur, author, "Sharing my results so far.", "pnl",
                           {"kind": "pnl", "paper_pnl": 7.2, "live_pnl": -0.6}, image)
    keep = repo.create_post(cur, other, "Mine stays", "note", None)
    repo.add_comment(cur, pid, other, "nice")
    repo.toggle_like(cur, pid, other)
    repo.toggle_reaction(cur, pid, other, "🔥")
    repo.add_comment(cur, keep, author, "a reply on someone else's post stays")
    repo.add_notification(cur, author, "comment", "D commented on your post.", "/community#post-%d" % pid)

    assert repo.delete_post(cur, pid, other) is False  # not theirs: nothing goes
    assert repo.post_exists(cur, pid)

    assert repo.delete_post(cur, pid, author) is True
    assert not repo.post_exists(cur, pid)
    for table in ("app_post_comments", "app_post_likes", "app_post_reactions"):
        assert _count(cur, "SELECT COUNT(*) AS n FROM %s WHERE post_id=%%s" % table, pid) == 0, table
    assert repo.get_image(cur, image) is None
    assert pid not in [p["id"] for p in repo.list_posts(cur, other)]
    assert repo.post_exists(cur, keep) and len(repo.list_comments(cur, keep)) == 1
    # The bell line stays; the feed says the post is gone when it is opened.
    assert [n["link"] for n in repo.list_notifications(cur, author)] == ["/community#post-%d" % pid]

    assert repo.delete_post(cur, pid, author) is False  # already gone


def test_a_card_stored_before_the_flag_says_later_period_from_the_feeds_own_read(conn):
    """A strategy card shared before its snapshot carried no_gatekeeper_record (#598) said "on unseen data" for a
    pass from the old loose backtest gate. The feed's own read (list_posts) says which cards' strategies have no
    gatekeeper record, and such a "passed" card gets the flag; so does a "failed" (Void) card with none, one the
    first version's simple backtest stored as failed, never judged on held-out data (its Strategies row and page say
    "not held out"); a recorded pass or void, the seeded example and a deleted strategy's card are as stored. No
    statement of its own."""
    from app import repository as repo
    from app.web.routes import community

    cur = conn.cursor()
    author, reader = _user(cur), _user(cur)

    def strategy(status="passed", direction="fade", recorded=False):
        sid = repo.insert_strategy(cur, author, None, None, "btc15m", direction, "Loose", "t", {}, status,
                                   {"out_of_sample": {"pnl": 5.5, "trades": 48}})
        if recorded:
            cur.execute("UPDATE app_strategies SET experiment_id=%s WHERE id=%s", (987654321, sid))
        return sid

    def share(sid, status="passed"):
        # As stored before #598: no no_gatekeeper_record key at all.
        return repo.create_post(cur, author, "", "strategy", {"kind": "strategy", "id": sid, "title": "Loose",
                                                              "market": "btc15m", "status": status, "pnl": 5.5, "trades": 48})

    posts = {"loose": share(strategy()), "kept": share(strategy(recorded=True)),
             "example": share(strategy(direction=repo.DEMO_DIRECTION)), "failed": share(strategy(status="failed"), "failed"),
             "failed_kept": share(strategy(status="failed", recorded=True), "failed"), "gone": share(10 ** 12)}
    note = repo.create_post(cur, author, "just a note", "note", None)
    statements = []
    real = cur.execute

    def counted(*a, **k):
        statements.append(a[0])
        return real(*a, **k)
    cur.execute = counted
    try:
        rows = {p["id"]: p for p in repo.list_posts(cur, reader, mine_only=False)}
    finally:
        cur.execute = real
    assert len(statements) == 2  # the posts, then their reactions, as before
    cards = {k: community._post_out(rows[pid])["snapshot"] for k, pid in posts.items()}
    assert cards["loose"]["no_gatekeeper_record"] is True
    # The void with no record too: this case said its card claimed nothing, so it read "unseen data" (VOID_NO_RECORD_SQL).
    assert cards["failed"]["no_gatekeeper_record"] is True and cards["failed"]["status"] == "failed"
    for k in ("kept", "example", "failed_kept", "gone"):
        assert "no_gatekeeper_record" not in cards[k], k
    assert community._post_out(rows[note])["snapshot"] is None
    assert "id" not in cards["loose"]  # the reader isn't its author


def test_the_feed_says_which_of_the_readers_own_cards_lost_their_strategy_in_the_same_read(conn):
    """The author's card for a strategy they deleted since linked its page, which said "Not found" (walked
    2026-10-31). The feed’s own read says so (strategy_gone), for every reader’s card since: still two statements,
    and a page more of the feed (Show older posts) is the same read with a larger limit."""
    from app import repository as repo
    from app.web.routes import community

    cur = conn.cursor()
    author, reader = _user(cur), _user(cur)

    def strategy(title):
        return repo.insert_strategy(cur, author, None, None, "btc15m", "fade", title, "t", {}, "passed",
                                    {"out_of_sample": {"pnl": 5.5, "trades": 48}})

    def share(sid, title):
        return repo.create_post(cur, author, "", "strategy", {"kind": "strategy", "id": sid, "title": title,
                                                              "market": "btc15m", "status": "passed", "pnl": 5.5})

    sid, kept = strategy("Deleted later"), strategy("Kept")
    gone_post, kept_post = share(sid, "Deleted later"), share(kept, "Kept")
    cur.execute("DELETE FROM app_strategies WHERE id=%s AND user_id=%s", (sid, author))
    statements, real = [], cur.execute

    def counted(*a, **k):
        statements.append(a[0])
        return real(*a, **k)
    cur.execute = counted
    try:
        mine = {p["id"]: p for p in repo.list_posts(cur, author, mine_only=True)}
    finally:
        cur.execute = real
    assert len(statements) == 2
    assert mine[gone_post]["strategy_gone"] is True and mine[kept_post]["strategy_gone"] is False
    assert community._post_out(mine[gone_post])["snapshot"].get("gone") is True
    assert community._post_out(mine[kept_post])["snapshot"]["id"] == kept
    theirs = {p["id"]: p for p in repo.list_posts(cur, reader, limit=300)}
    # Another reader's card says it is gone too: it said "Ask them to share a copy from its page" (walked 2026-10-31).
    assert theirs[gone_post]["strategy_gone"] is True and theirs[kept_post]["strategy_gone"] is False
    assert community._post_out(theirs[gone_post])["snapshot"].get("gone") is True
    assert "id" not in community._post_out(theirs[gone_post])["snapshot"]
    assert len(repo.list_posts(cur, reader, limit=1)) == 1


def test_the_feed_says_which_shared_deployments_were_removed_since_in_the_same_read(conn):
    """A shared deployment its author removed since read "realized … since Oct 10" as a run still trading (walked
    2026-10-31). The feed's own read says so (deployment_removed) for every reader: a stopped one or a missing row,
    never a running or paused one, and never a strategy or P&L card."""
    from app import repository as repo
    from app.web.routes import community

    cur = conn.cursor()
    author, reader = _user(cur), _user(cur)
    sid = repo.insert_strategy(cur, author, None, None, "btc15m", "fade", "Drift", "t", {}, "passed",
                               {"out_of_sample": {"pnl": 5.5, "trades": 48}})
    running, stopped, paused = (repo.create_deployment(cur, author, sid, "btc15m", m) for m in ("paper", "demo", "live"))
    repo.set_deployment_status(cur, author, stopped, "stopped", "By you")
    repo.set_deployment_status(cur, author, paused, "paused", "By you")

    def share(did):
        return repo.create_post(cur, author, "", "deployment", {"kind": "deployment", "id": did, "title": "Drift",
                                                                "market": "btc15m", "mode": "paper", "realized_pnl": 1.0})
    posts = {"running": share(running), "stopped": share(stopped), "paused": share(paused), "missing": share(10 ** 12),
             "pnl": repo.create_post(cur, author, "", "pnl", {"kind": "pnl", "paper_pnl": 1.0, "live_pnl": 0.0})}
    rows = {p["id"]: p for p in repo.list_posts(cur, reader, limit=300)}
    got = {k: rows[pid]["deployment_removed"] for k, pid in posts.items()}
    assert got == {"running": False, "stopped": True, "paused": False, "missing": True, "pnl": False}
    assert community._post_out(rows[posts["stopped"]])["snapshot"].get("removed") is True
