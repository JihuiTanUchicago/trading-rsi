"""/api/me's one-statement key probe (repo.kalshi_keys_saved) says what the key reads say, against a real MySQL.
Opt in as for test_app_engine_db.py (APP_TEST_MYSQL=1).

For an account with no key, a live key only, a demo key only and both (and beside another account holding both),
kalshi_keys_saved is (get_kalshi_key is not None, get_kalshi_dev_key is not None), in one statement that selects
no key column, and /api/me's kalshi_connected and kalshi_dev_connected follow it, before and after a key is removed.
"""
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


def _user(cur, live, demo):
    from app import repository as repo

    uid = repo.upsert_google_user(cur, "test:" + uuid.uuid4().hex, "keys@example.invalid", "Keys", None)["id"]
    if live:
        repo.save_kalshi_key(cur, uid, "LIVE-placeholder-%d" % uid, b"placeholder-not-a-key")
    if demo:
        repo.set_kalshi_dev_key(cur, uid, "DEMO-placeholder-%d" % uid, b"placeholder-not-a-key")
    return uid


def test_the_one_probe_says_what_the_key_reads_say(conn):
    from app import repository as repo

    cur = conn.cursor()
    _user(cur, True, True)  # another account's keys never answer for this one
    for live in (True, False):
        for demo in (True, False):
            uid = _user(cur, live, demo)
            assert repo.kalshi_keys_saved(cur, uid) == (live, demo) == (
                repo.get_kalshi_key(cur, uid) is not None, repo.get_kalshi_dev_key(cur, uid) is not None)
            if live:
                repo.delete_kalshi_key(cur, uid)
            if demo:
                repo.delete_kalshi_dev_key(cur, uid)
            assert repo.kalshi_keys_saved(cur, uid) == (False, False)


def test_me_says_whether_each_key_is_saved_from_the_one_probe(conn, monkeypatch):
    from types import SimpleNamespace

    from app import repository as repo, server

    cur = conn.cursor()
    sent = []
    real = conn.cursor

    class Counting:
        def __init__(self, c):
            self._c = c

        def execute(self, sql, args=None):
            sent.append(" ".join(str(sql).split()))
            return self._c.execute(sql, args)

        def __getattr__(self, name):
            return getattr(self._c, name)

    for live in (True, False):
        for demo in (True, False):
            uid = _user(cur, live, demo)
            user = repo.get_user(cur, uid)
            sent.clear()
            monkeypatch.setattr(conn, "cursor", lambda: Counting(real()))
            out = server.me(server.Ctx(conn, user, (), {}, {}, SimpleNamespace(headers={})))
            monkeypatch.setattr(conn, "cursor", real)
            assert (out["kalshi_connected"], out["kalshi_dev_connected"]) == (live, demo)
            keys = [s for s in sent if "app_kalshi_keys" in s or "app_kalshi_dev_keys" in s]
            assert len(keys) == 1 and "private_key_enc" not in keys[0] and "key_id" not in keys[0], keys
