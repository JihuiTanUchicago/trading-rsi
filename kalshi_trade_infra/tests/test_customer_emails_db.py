"""The community emails against a real MySQL, through the routes that send them: a shared strategy or deployment
is named in its new-post email as its card names it (title, the market's long name, the book), and a reply email
quotes a long post cut with an ellipsis, never mid-word with nothing to say it goes on (post_author read only its
first 140 characters, so the email's own cut never ran). No email is sent: the sender is captured and boto3 refused.
Opt in as for test_app_engine_db.py (APP_TEST_MYSQL=1)."""
import os
import re
import sys
import types
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
    c.autocommit(True)
    yield c
    c.close()


class _Now:  # runs the new-post dispatch on this thread
    def __init__(self, target, daemon):
        self.target = target

    def start(self):
        self.target()


@pytest.fixture
def mails(monkeypatch, conn):
    from app.adapters.ses import notify
    from app.web.routes import community

    sent = []
    refused = types.ModuleType("boto3")

    def _client(*a, **k):  # pragma: no cover - only reached if a builder bypasses _send
        raise AssertionError("an email test must never reach SES")
    refused.client = _client
    monkeypatch.setitem(sys.modules, "boto3", refused)
    monkeypatch.setattr(notify, "_send", lambda to, subject, body, reply_to=None: sent.append(
        SimpleNamespace(to=to, subject=subject, body=body)) or True)
    monkeypatch.setattr(community.threading, "Thread", _Now)
    return sent


def _user(cur, repo, name):
    tag = uuid.uuid4().hex[:8]
    uid = repo.upsert_google_user(cur, "test:" + tag, "%s-%s@example.com" % (name.split()[0].lower(), tag), name, None)["id"]
    cur.execute("UPDATE app_users SET email_opt_out=0, last_community_email_at=NULL WHERE id=%s", (uid,))
    return repo.get_user(cur, uid)


def _ctx(conn, user, body=None, params=()):
    from app.web.http import Ctx
    return Ctx(conn=conn, user=user, params=tuple(str(p) for p in params), query={}, body=body or {}, handler=None)


SPEC = {"side": "yes", "entry": {"min_edge": 0.05, "min_price": 0.2, "max_price": 0.8, "filters": []},
        "exit": {"take_profit": None, "stop_loss": 0.3}, "sizing": {"stake": 5.0, "max_positions": 8}}
RESULT = {"capital": 40.0, "out_of_sample": {"trades": 64, "pnl": 4.205, "win_rate": 0.54, "days": 8}}


def _mine(mails, to):
    return [m for m in mails if m.to == to]


def test_a_shared_strategy_or_deployment_is_named_as_its_card_names_it(conn, mails):
    from app import repository as repo
    from app.trading import markets
    from app.web.routes import community

    cur = conn.cursor()
    bob, ann = _user(cur, repo, "Bob Share"), _user(cur, repo, "Ann Reader")
    for market, mode in (("btc_daily", "live"), ("high_ny", "paper"), ("nfl_game", "demo"), ("eth15m", "paper")):
        sid = repo.insert_strategy(cur, bob["id"], None, None, market, "momentum", "Fade %s" % mode, "t",
                                   dict(SPEC, market=market), "passed", RESULT)
        dep = repo.create_deployment(cur, bob["id"], sid, market, mode)
        label = markets.customer_name(market)
        for kind, ref, line in (("strategy", sid, "Strategy: Fade %s · %s" % (mode, label)),
                                ("deployment", dep, "Deployment: Fade %s · %s · %s" % (mode, label, mode.capitalize()))):
            cur.execute("UPDATE app_users SET last_community_email_at=NULL")  # the 4h cooldown is not under test
            del mails[:]
            pid = community.create_community_post(_ctx(conn, bob, {"kind": kind, "ref_id": ref}))["id"]
            [m] = _mine(mails, ann["email"])
            assert "  “Sharing a %s.”\n\n  %s\n\n" % (kind, line) in m.body
            assert "/community#post-%d\n" % pid in m.body
            series = markets.MARKETS[market].series
            assert market not in m.body and series not in m.body and "$" not in m.body
    # The P&L share names no card and quotes no figure: its books stay apart on the feed, with their notes.
    cur.execute("UPDATE app_users SET last_community_email_at=NULL")
    del mails[:]
    community.create_community_post(_ctx(conn, bob, {"kind": "pnl"}))
    [m] = _mine(mails, ann["email"])
    assert "“Sharing my results so far.”\n\nRead it and join in:" in m.body and "$" not in m.body


def test_a_reply_email_quotes_a_long_post_cut_with_an_ellipsis(conn, mails):
    from app import repository as repo
    from app.web.routes import community

    cur = conn.cursor()
    ann, bob = _user(cur, repo, "Ann Poster"), _user(cur, repo, "Bob Replier")
    long_body = "The **NYC high** keeps landing in the band priced lowest, " + "again and again, " * 20 + "every week."
    pid = community.create_community_post(_ctx(conn, ann, {"kind": "note", "body": long_body}))["id"]
    del mails[:]
    community.create_comment(_ctx(conn, bob, {"body": "Same here."}, params=(pid,)))
    [m] = _mine(mails, ann["email"])
    quoted = re.search(r"Your post: “(.*)”\n", m.body).group(1)
    assert quoted.startswith("The NYC high keeps landing") and "**" not in quoted
    assert 130 <= len(quoted) <= 140 and quoted.endswith("…")  # notify._plain's cut, trailing space dropped
    # A short post is quoted whole, with no ellipsis.
    pid = community.create_community_post(_ctx(conn, ann, {"kind": "note", "body": "Short one."}))["id"]
    del mails[:]
    community.create_comment(_ctx(conn, bob, {"body": "Ok."}, params=(pid,)))
    [m] = _mine(mails, ann["email"])
    assert "  Your post: “Short one.”\n" in m.body
