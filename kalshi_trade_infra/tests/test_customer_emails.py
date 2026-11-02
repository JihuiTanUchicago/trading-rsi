"""Every email Tau sends a customer, rendered from its builder with made-up data (SES never reached: the
sender is captured and boto3 is replaced by a module that refuses). Each link opens a page the app serves,
nothing reads as an internal key, a paragraph is one line (a phone's mail app wraps it instead of breaking
it mid-sentence), and each says what happened in the app's words: the waitlist confirmation no longer
promises an invite email nothing sends, a profile name that is an address is never the greeting, a mention
says where it is, a perps alert links Funds, and the unsubscribe page names every email that still comes."""
import re
import sys
import types
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.adapters.kalshi_perps import kalshi_perps_watch as watch
from app.adapters.ses import notify
from app.web import settings as web_settings
from app.web.routes import account as account_routes
from app.web.routes import funds as funds_routes

PU = notify.PUBLIC_URL
POST = notify.page_url("/community#post-5")
URL = re.compile(r"https?://\S+")


@pytest.fixture
def mails(monkeypatch):
    sent = []
    refused = types.ModuleType("boto3")

    def _client(*a, **k):  # pragma: no cover - only reached if a builder bypasses _send
        raise AssertionError("an email walk must never reach SES")
    refused.client = _client
    monkeypatch.setitem(sys.modules, "boto3", refused)
    monkeypatch.setattr(notify, "_send", lambda to, subject, body, reply_to=None: sent.append(
        SimpleNamespace(to=to, subject=subject, body=body)) or True)
    return sent


def _all(mails):
    notify.waitlist_request("new@x.invalid", None, 3, open_signup=True)
    notify.waitlist_request("new@x.invalid", None, 3, open_signup=False)
    notify.comment_notice("a@x.invalid", "Ann", "Bob", "My post", "Nice", POST, notify.unsubscribe_url(7))
    notify.mention_notice("a@x.invalid", "Ann", "Bob", "hi @ann", POST, notify.unsubscribe_url(7), where="a reply")
    notify.community_post_notice("a@x.invalid", "Ann", "Bob", "Sharing a strategy.", notify.unsubscribe_url(7), POST)
    for live_open in (True, False):
        notify.live_ready_notice("a@x.invalid", "Ann", "Fade", "40 paper trades", PU + "/strategies/12", "", live_open)
    notify.reload_failed_email("a@x.invalid", "Ann", Decimal("25.00"), "Your card was declined.")
    notify.perps_alert("a@x.invalid", "margin", "Maintenance margin $600 uses 60% of account equity $1,000")
    return [m for m in mails if m.to != notify.OPERATOR_EMAIL]


def test_every_link_in_every_email_opens_a_page_the_app_serves(mails):
    for m in _all(mails):
        links = URL.findall(m.body)
        assert links, m.subject
        for link in links:
            assert link.startswith(PU + "/"), (m.subject, link)
            path = link[len(PU):]
            if path.startswith("/unsubscribe?"):
                uid, token = re.match(r"/unsubscribe\?u=(\d+)&t=(\w+)$", path).groups()
                assert notify.unsubscribe_token_valid(int(uid), token)
            else:
                assert web_settings.SPA_ROUTES.match(path.split("#")[0]), (m.subject, path)


def test_no_email_reads_as_an_internal_key_or_a_price_list(mails):
    for m in _all(mails):
        text = m.subject + "\n" + m.body
        for bad in ("btc15m", "btc_perp", "anthropic", "list price", "Traceback", "None", "us-east-1", "AWS"):
            assert bad not in text, (m.subject, bad)


def test_a_paragraph_is_one_line_so_a_phone_wraps_it(mails):
    """A hard break inside a sentence reads as a broken line on a 360px mail viewer."""
    for m in _all(mails) + [x for x in mails if x.to == notify.OPERATOR_EMAIL]:
        lines = m.body.split("\n")
        for a, b in zip(lines, lines[1:]):
            assert not (a.strip() and b[:1].islower()), (m.subject, a, b)


def test_waitlist_confirmation_says_how_to_get_in_and_promises_no_email_nothing_sends(mails):
    _all(mails)
    open_mail, invite_mail = [m for m in mails if m.to == "new@x.invalid"]
    assert "Sign-up is open now: sign in with Google" in open_mail.body and PU + "/signin" in open_mail.body
    assert "invite-only" not in open_mail.body
    assert "invite-only" in invite_mail.body and PU + "/signin" in invite_mail.body
    for m in (open_mail, invite_mail):
        assert "we'll email you" not in m.body and "spot opens" not in m.body  # approving a request sends nothing
    op_open, op_invite = [m for m in mails if m.to == notify.OPERATOR_EMAIL]
    assert "Sign-up is open" in op_open.body and "Google Cloud console" not in op_open.body
    assert "Google Cloud console" in op_invite.body


def test_the_waitlist_route_tells_the_email_how_sign_in_works_now(monkeypatch):
    seen = {}
    monkeypatch.setattr(funds_routes.repo, "recent_waitlist_from", lambda *a: 0)
    monkeypatch.setattr(funds_routes.repo, "add_waitlist", lambda *a: True)
    monkeypatch.setattr(funds_routes.repo, "count_waitlist", lambda cur: 4)
    monkeypatch.setattr(funds_routes.notify, "waitlist_request", lambda *a, **k: seen.update(a=a, k=k) or True)
    for flag in (True, False):
        monkeypatch.setattr(web_settings, "OPEN_SIGNUP", flag)
        funds_routes.waitlist(SimpleNamespace(cur=None, body={"email": "n@x.invalid"}))
        assert seen["a"] == ("n@x.invalid", None, 4) and seen["k"] == {"open_signup": flag}


@pytest.mark.parametrize("name,hello", [("Ann", "Hi Ann,"), ("ann@x.invalid", "Hi there,"), (None, "Hi there,"),
                                        ("  ", "Hi there,")])
def test_the_greeting_never_shows_an_email_address(mails, name, hello):
    notify.comment_notice("a@x.invalid", name, "Bob", "p", "r", POST, notify.unsubscribe_url(7))
    notify.mention_notice("a@x.invalid", name, "Bob", "hi", POST, notify.unsubscribe_url(7))
    notify.community_post_notice("a@x.invalid", name, "Bob", "p", notify.unsubscribe_url(7), POST)
    notify.reload_failed_email("a@x.invalid", name, Decimal("5.00"), "Declined.")
    for m in mails:
        assert m.body.startswith(hello + "\n\n"), m.subject


def test_a_mention_email_says_where_the_mention_is(mails):
    notify.mention_notice("a@x.invalid", "Ann", "Bob", "hi", POST, where="a reply")
    notify.mention_notice("a@x.invalid", "Ann", "Bob", "hi", POST, where="a post")
    notify.mention_notice("a@x.invalid", "Ann", "Bob", "hi", POST)
    assert "Bob mentioned you in a reply in the Tau community:" in mails[0].body
    assert "Bob mentioned you in a post in the Tau community:" in mails[1].body
    assert "Bob mentioned you in the Tau community:" in mails[2].body


def test_a_perps_alert_links_funds_says_why_it_came_and_shows_figures_as_funds_does(monkeypatch, mails):
    rows = {}
    monkeypatch.setattr(watch, "repo", SimpleNamespace(get_user=lambda cur, uid: {"email": "p@x.invalid"},
                                                       add_chat=lambda *a: None))
    monkeypatch.setattr(watch, "store", SimpleNamespace(
        open_watch_rows=lambda cur, uid, env: list(rows.values()),
        upsert_watch=lambda cur, uid, env, key, kind, detail: rows.setdefault(key, {"alert_key": key, "kind": kind,
                                                                                    "detail": detail}),
        resolve_watch=lambda *a: None))
    state = {"positions": [], "account": {"account_equity": "1000", "maintenance_margin": "600"},
             "risk": {"positions": [{"market_ticker": "KXBTCPERP-USD", "position": "4", "mark_price": "60000",
                                     "estimated_liquidation_price": "56000"}]}}
    watch.watch_user(None, 1, "production", state, True)
    by_subject = {m.subject: m.body for m in mails}
    liq = by_subject["Tau: Bitcoin perps liquidation alert"]
    assert "mark $60,000, Kalshi's estimated liquidation $56,000, position 4." in liq
    margin = by_subject["Tau: Bitcoin perps margin alert"]
    assert margin.startswith("Maintenance margin $600 uses 60% of account equity $1,000.\n\n")
    for body in (liq, margin):
        assert PU + "/funds" in body and "Tau Funds" not in body
        assert "even with updates and community email off in Settings" in body


def test_unsubscribe_page_names_every_email_that_still_comes(monkeypatch):
    monkeypatch.setattr(account_routes.repo, "set_email_opt_out", lambda cur, uid, off: True)
    ok = SimpleNamespace(cur=None, query={"u": "7", "t": notify.unsubscribe_token(7)})
    _, page = account_routes.unsubscribe(ok)
    assert "Safety alerts about a Bitcoin perps account" in page
    assert "an email when an auto-reload of your credits fails" in page  # as Settings' "Always sent" line says
    # Walk 2026-11-02: "turn optional email back on in Settings" opened Settings at the theme, the switch a screen below on
    # a phone: it opens at the Email section (#email), where the switch is.
    assert "href='/settings#email'" in page and "href='/settings'" not in page


def test_a_bad_unsubscribe_link_points_at_the_settings_switch():
    _, page = account_routes.unsubscribe(SimpleNamespace(cur=None, query={"u": "7", "t": "forged"}))
    assert "isn't valid, so nothing changed" in page and "href='/settings#email'" in page
    assert "reply to one" not in page


class _Now:  # runs the new-post dispatch on this thread
    def __init__(self, target, daemon):
        self.target = target

    def start(self):
        self.target()


def _post_email(monkeypatch, mails, snap, body=""):
    """A share posted through create_community_post, its new-post email rendered by the real template."""
    from app.web.routes import community
    monkeypatch.setattr(community, "_post_snapshot", lambda cur, uid, kind, ref: snap)
    monkeypatch.setattr(community.repo, "recent_posts_count", lambda cur, uid, since: 0)
    monkeypatch.setattr(community.repo, "create_post", lambda *a: 5)
    monkeypatch.setattr(community.repo, "get_user", lambda cur, uid: {"name": "Bob"})
    monkeypatch.setattr(community, "get_db_conn", lambda: SimpleNamespace(cursor=lambda: None, close=lambda: None))
    monkeypatch.setattr(community.repo, "community_email_recipients",
                        lambda c, uid: [{"id": 3, "email": "c@x.invalid", "name": "Cat"}])
    monkeypatch.setattr(community.repo, "mark_community_emailed", lambda c, ids: None)
    monkeypatch.setattr(community.threading, "Thread", _Now)
    del mails[:]
    community.create_community_post(SimpleNamespace(cur=None, uid=1, body={"kind": snap["kind"], "ref_id": 9, "body": body},
                                                    query={}, params=()))
    [m] = mails
    return m.body


@pytest.mark.parametrize("market,series", [("btc_daily", "KXBTCD"), ("btc15m", "KXBTC15M"), ("eth15m", "KXETH15M"),
                                           ("high_ny", "KXHIGHNY"), ("nfl_game", "KXNFLGAME")])
def test_a_share_email_names_what_is_shared_by_the_markets_long_name(monkeypatch, mails, market, series):
    """A share with no words of its own read only “Sharing a strategy.”: nothing said which strategy, or on what.
    The email now heads it as its card does: kind, title, the market's long name (never its key or series), book."""
    from app.trading import markets
    label = markets.customer_name(market)
    assert label != "an unlisted market"
    strat = _post_email(monkeypatch, mails, {"kind": "strategy", "id": 9, "title": "Late-day fade", "market": market,
                                             "status": "passed", "pnl": 4.21, "trades": 64})
    assert "  “Sharing a strategy.”\n\n  Strategy: Late-day fade · %s\n\n" % label in strat
    bodies = [strat]
    for mode, mark in (("live", "Live"), ("demo", "Demo"), ("paper", "Paper")):
        dep = _post_email(monkeypatch, mails, {"kind": "deployment", "id": 9, "title": "Home dogs", "market": market,
                                               "mode": mode, "realized_pnl": -3.38, "closed_trades": 4})
        assert "  “Sharing a deployment.”\n\n  Deployment: Home dogs · %s · %s\n\n" % (label, mark) in dep
        bodies.append(dep)
    for body in bodies:
        assert market not in body and series not in body and "$X" not in body
        assert "$" not in body  # a figure without its card's notes is left to the feed
    words = _post_email(monkeypatch, mails, {"kind": "strategy", "id": 9, "title": "Fade", "market": market}, body="Look")
    assert "  “Look”\n\n  Strategy: Fade · %s\n\n" % label in words


def test_a_pnl_share_or_a_note_email_quotes_no_figure_and_names_no_card(monkeypatch, mails):
    body = _post_email(monkeypatch, mails, {"kind": "pnl", "paper_pnl": 12.5, "demo_pnl": 3.0, "live_pnl": -1.25,
                                            "paper_trades": 3, "demo_trades": 1, "live_trades": 1})
    assert "“Sharing my results so far.”\n\nRead it and join in:" in body and "$" not in body


def test_a_perps_mismatch_alert_says_position_mismatch_as_settings_names_it(mails):
    notify.perps_alert("a@x.invalid", watch.MISMATCH, "Kalshi holds 3 KXBTCPERP-USD contracts that no Tau order filled")
    notify.perps_alert("a@x.invalid", "unheard-of", "Something")
    assert [m.subject for m in mails] == ["Tau: Bitcoin perps position mismatch alert", "Tau: Bitcoin perps safety alert"]
