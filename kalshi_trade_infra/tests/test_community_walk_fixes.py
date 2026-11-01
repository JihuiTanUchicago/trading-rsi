"""What a community walk as three customers found (2026-10-31), held through the real routes with the repo stubbed:
a reactor whose name is their email address was named by it in every reader's feed; the @-autocomplete matched on
such a name, so a reader could spell out another person's address a letter at a time; the feed had no way past its
newest 30 posts; and the author's card for a strategy they had deleted linked a page that said "Not found".
No DB, no network."""
import json
from types import SimpleNamespace

import pytest

from app import repository as repo
from app import server
from app.web.routes import community

ADDRESS = "dana.private@example.org"


def _ctx(params=(), query=None, body=None):
    return server.Ctx(SimpleNamespace(cursor=lambda: object()), {"id": 7, "email": "me@example.invalid", "name": "Me"},
                      tuple(params), query or {}, body or {}, SimpleNamespace())


def _row(**over):
    row = {"id": 5, "body": "gm", "kind": "note", "snapshot": None, "likes_count": 0, "comments_count": 0,
           "created_at": None, "name": "Ann", "username": "ann", "picture_url": None, "mine": False,
           "reactions": [{"emoji": "🔥", "count": 3, "mine": False, "names": [ADDRESS, "Bob", "  "],
                          "handles": ["danap", "bob", ""]}]}
    row.update(over)
    return row


def test_a_reactor_whose_name_is_their_address_reads_as_their_handle_in_the_feed_and_after_a_reaction(monkeypatch):
    out = community._post_out(_row())
    assert out["reactions"][0]["names"] == ["danap", "Bob", "Trader"]
    assert out["reactions"][0]["handles"] == ["danap", "bob", ""] and out["reactions"][0]["count"] == 3
    assert ADDRESS not in json.dumps(out)
    # The reaction a press returns, which repaints the pills, says it the same way.
    monkeypatch.setattr(repo, "post_exists", lambda cur, pid: True)
    monkeypatch.setattr(repo, "toggle_reaction", lambda cur, pid, uid, emoji: True)
    monkeypatch.setattr(repo, "reactions_for", lambda cur, ids, uid: {5: _row()["reactions"]})
    got = community.react_post(_ctx(("5",), body={"emoji": "🔥"}))
    assert got["reactions"][0]["names"] == ["danap", "Bob", "Trader"] and ADDRESS not in json.dumps(got)


def test_reactor_names_and_handles_stay_paired_when_a_reactor_has_no_name_or_handle():
    # The read lists names and handles position for position: dropping a missing one shifted the rest, so an address
    # name could take another reactor's handle.
    from app.adapters.db import community_repository as cr

    class Cur:
        def execute(self, sql, args): self.sql = sql
        def fetchall(self):
            return [{"post_id": 5, "emoji": "🔥", "n": 3, "mine": 0,
                     "names": "\x1f".join(["", ADDRESS, "Bob"]), "handles": "\x1f".join(["ann", "", "bob"])}]
    rx = cr.reactions_for(Cur(), [5], 7)[5]
    assert community._reactions_out(rx)[0]["names"] == ["ann", "Trader", "Bob"]


@pytest.mark.parametrize("q", ["dana.private@exa", "example.org", "private", "@example"])
def test_the_mention_search_never_matches_on_a_name_that_is_an_address(monkeypatch, q):
    # The read matches a name anywhere in it (LIKE %q%); a hit on an address must not come back.
    monkeypatch.setattr(repo, "mention_candidates", lambda cur, query: [{"username": "danap", "name": ADDRESS}])
    assert community.mention_candidates(_ctx(query={"q": q}))["users"] == []


def test_the_mention_search_still_finds_a_person_by_handle_or_by_a_real_name(monkeypatch):
    monkeypatch.setattr(repo, "mention_candidates", lambda cur, query: [{"username": "danap", "name": ADDRESS},
                                                                         {"username": "cq", "name": "Dana Quant"}])
    assert community.mention_candidates(_ctx(query={"q": "dan"}))["users"] == [
        {"username": "danap", "name": "danap"}, {"username": "cq", "name": "Dana Quant"}]
    assert community.mention_candidates(_ctx(query={"q": "Quant"}))["users"] == [{"username": "cq", "name": "Dana Quant"}]


@pytest.mark.parametrize("query,limit", [({}, 30), ({"limit": "60"}, 60), ({"limit": "100000"}, 300),
                                         ({"limit": "0"}, 1), ({"limit": "abc"}, 30)])
def test_show_older_posts_reads_more_of_the_feed_in_one_read_up_to_a_cap(monkeypatch, query, limit):
    seen = []

    def list_posts(cur, uid, before, limit=30, mine_only=False):
        seen.append((uid, before, limit, mine_only))
        return []
    monkeypatch.setattr(repo, "list_posts", list_posts)
    assert community.community(_ctx(query=dict(query, before="40", mine="1"))) == {"posts": []}
    assert seen == [(7, 40, limit, True)]


def test_the_authors_card_for_a_strategy_they_deleted_since_has_no_link_and_says_it_is_gone():
    snap = {"kind": "strategy", "id": 4711, "title": "Fade", "market": "btc15m", "status": "passed", "pnl": 5.5}
    gone = community._post_out(_row(kind="strategy", snapshot=dict(snap), mine=True, strategy_gone=True))["snapshot"]
    assert gone.get("gone") is True and "id" not in gone and gone["pnl"] == 5.5
    kept = community._post_out(_row(kind="strategy", snapshot=dict(snap), mine=True))["snapshot"]
    assert kept["id"] == 4711 and "gone" not in kept
    # Anyone else's card is no link either way. It says the strategy is gone too: it said "Ask them to share a copy
    # from its page" of a page no longer there (walked 2026-10-31).
    other = community._post_out(_row(kind="strategy", snapshot=dict(snap), strategy_gone=True))["snapshot"]
    assert "id" not in other and other.get("gone") is True and other["pnl"] == 5.5
    assert "gone" not in community._post_out(_row(kind="strategy", snapshot=dict(snap)))["snapshot"]


def test_a_shared_deployment_removed_since_says_so_to_every_reader_and_keeps_its_figures():
    """"realized +$1.39 · 3 trades since Oct 10" read as a run still trading after its author removed it (walked
    2026-10-31). The read's deployment_removed rides as `removed`; the figures stand as shared, and no id leaks."""
    snap = {"kind": "deployment", "id": 99, "title": "Drift", "market": "btc15m", "mode": "paper",
            "realized_pnl": 1.39, "closed_trades": 3, "since": "2026-10-10T19:29:39Z"}
    for mine in (False, True):
        card = community._post_out(_row(kind="deployment", snapshot=dict(snap), mine=mine, deployment_removed=True))["snapshot"]
        assert card["removed"] is True and card["realized_pnl"] == 1.39 and ("id" in card) == mine
        assert "removed" not in community._post_out(_row(kind="deployment", snapshot=dict(snap), mine=mine))["snapshot"]
    # A strategy card never carries it.
    strat = {"kind": "strategy", "id": 4711, "title": "Fade", "market": "btc15m", "status": "passed", "pnl": 5.5}
    assert "removed" not in community._post_out(_row(kind="strategy", snapshot=strat, deployment_removed=True))["snapshot"]


def test_a_pnl_share_carries_the_demo_book_on_its_own_line_never_added_in(monkeypatch):
    """A demo-only trader's Share my P&L read "Paper: no trades yet · Live (real money): no trades yet" under "Realized
    P&L, all time": the share left the demo book out (walked 2026-10-31). Each book's all-time realized figure through
    cents(), with its closed-trade count, each apart; a reader sees those and nothing more."""
    sums = {"paper": -3.14159, "demo": -2.305, "live": 0.0}
    monkeypatch.setattr(repo, "realized_pnl", lambda cur, uid, mode, since=None: sums[mode])
    monkeypatch.setattr(repo, "closed_trade_counts", lambda cur, uid: {"paper": 4, "demo": 3})
    snap = community._post_snapshot(object(), 7, "pnl", 0)
    assert snap == {"kind": "pnl", "paper_pnl": -3.14, "demo_pnl": -2.31, "live_pnl": 0.0,
                    "paper_trades": 4, "demo_trades": 3, "live_trades": 0}
    card = community._post_out(_row(kind="pnl", snapshot=dict(snap, private=1)))["snapshot"]
    assert card == snap


# -- the feed open in two places at once (walked 2026-11-01 with two pages on one account) ----------------------------

def test_delete_on_a_post_already_taken_down_elsewhere_says_so_and_not_your_own_posts(monkeypatch):
    """The author deleted a post in one tab; Delete on it in the other tab said "You can only delete your own posts.",
    of their own post. A post that is gone says it was already deleted; someone else's post still says whose it is."""
    from app.web.http import ApiError
    monkeypatch.setattr(repo, "delete_post", lambda cur, pid, uid: False)
    monkeypatch.setattr(repo, "post_exists", lambda cur, pid: False)
    with pytest.raises(ApiError) as gone:
        community.delete_community_post(_ctx(("5",)))
    assert gone.value.status == 404 and str(gone.value) == "This post was already deleted."
    monkeypatch.setattr(repo, "post_exists", lambda cur, pid: True)
    with pytest.raises(ApiError) as theirs:
        community.delete_community_post(_ctx(("5",)))
    assert theirs.value.status == 403 and "your own posts" in str(theirs.value)
    # A reaction or a reply on it says it was deleted, never "No such post."
    monkeypatch.setattr(repo, "post_exists", lambda cur, pid: False)
    monkeypatch.setattr(repo, "post_author", lambda cur, pid: None)
    for call, body in ((community.react_post, {"emoji": "🔥"}), (community.like_post, {}), (community.create_comment, {"body": "hi"})):
        with pytest.raises(ApiError) as said:
            call(_ctx(("5",), body=body))
        assert said.value.status == 404 and str(said.value) == "This post was deleted.", call.__name__


def test_a_reaction_press_asks_for_on_or_off_so_a_stale_feed_never_takes_back_one_added_elsewhere(monkeypatch):
    """The reaction was added in one tab; the other tab's pill (drawn before) pressed to add it took it straight back
    off, since a press toggled. A press carrying `on` sets that state; one without it toggles, as before."""
    calls = []
    monkeypatch.setattr(repo, "post_exists", lambda cur, pid: True)
    monkeypatch.setattr(repo, "set_reaction", lambda cur, pid, uid, emoji, on: calls.append(("set", pid, uid, emoji, on)) or on)
    monkeypatch.setattr(repo, "toggle_reaction", lambda cur, pid, uid, emoji: calls.append(("toggle", pid, uid, emoji)) or True)
    monkeypatch.setattr(repo, "reactions_for", lambda cur, ids, uid: {5: [{"emoji": "🔥", "count": 2, "mine": True,
                                                                           "names": ["Ann", "Me"], "handles": ["ann", "me"]}]})
    got = community.react_post(_ctx(("5",), body={"emoji": "🔥", "on": True}))
    assert got["reactions"][0]["mine"] is True and got["reactions"][0]["count"] == 2
    community.react_post(_ctx(("5",), body={"emoji": "🔥", "on": False}))
    community.react_post(_ctx(("5",), body={"emoji": "🔥"}))
    community.react_post(_ctx(("5",), body={"emoji": "🔥", "on": "yes"}))  # not a yes/no: toggles, as before
    assert calls == [("set", 5, 7, "🔥", True), ("set", 5, 7, "🔥", False), ("toggle", 5, 7, "🔥"), ("toggle", 5, 7, "🔥")]


def test_set_reaction_adds_once_and_takes_off_whatever_it_was():
    from app.adapters.db import community_repository as cr

    class Cur:
        def __init__(self, have):
            self.have, self.sql = have, []

        def execute(self, sql, args):
            self.sql.append(sql.split()[0])

        def fetchone(self):
            return {"1": 1} if self.have else None
    on_already = Cur(True)
    assert cr.set_reaction(on_already, 5, 7, "🔥", True) is True and on_already.sql == ["SELECT"]
    off_now = Cur(False)
    assert cr.set_reaction(off_now, 5, 7, "🔥", True) is True and off_now.sql == ["SELECT", "INSERT"]
    for have in (True, False):
        c = Cur(have)
        assert cr.set_reaction(c, 5, 7, "🔥", False) is False and c.sql == ["DELETE"]
