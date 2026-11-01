"""The bell lists notices about the same thing in a row as one line with a count, keeps every other
line in order, and reads a bounded number of rows however many notices a user has."""
from app import server
from app.services import bell


def _row(i, kind, link, read=True, body=None):
    return {"id": i, "kind": kind, "body": body or "%s %d" % (kind, i), "link": link,
            "created_at": "2026-10-26T10:%02d:00" % (i % 60), "read_at": "x" if read else None}


def test_adjacent_notices_about_one_page_collapse_into_the_newest():
    rows = [_row(9, "comment", "/community#post-4", read=False, body="Ana commented on your post."),
            _row(8, "comment", "/community#post-4", body="Bo commented on your post."),
            _row(7, "comment", "/community#post-4"),
            _row(6, "paused", "/strategies/3"),
            _row(5, "comment", "/community#post-4"),  # not adjacent to the first run: its own line
            _row(4, "share", "/strategies/11"),
            _row(3, "share", "/strategies/12")]       # same kind, another page: not alike
    out = bell.grouped(rows)
    assert [(n["id"], n["more"]) for n in out] == [(9, 2), (6, 0), (5, 0), (4, 0), (3, 0)]
    assert out[0]["body"] == "Ana commented on your post." and out[0]["read"] is False
    assert set(out[0]) == {"id", "kind", "body", "link", "created_at", "read", "more", "alike", "from", "from_more", "gone"}
    assert not any(n["gone"] for n in out)


def test_a_group_is_unread_when_any_notice_in_it_is():
    out = bell.grouped([_row(2, "paused", "/strategies/3"), _row(1, "paused", "/strategies/3", read=False)])
    assert out == [dict(out[0], more=1, read=False)]


def test_notices_without_a_link_never_group():
    out = bell.grouped([_row(2, "share", None), _row(1, "share", None)])
    assert [n["more"] for n in out] == [0, 0]


def test_the_list_stops_at_its_size_and_a_trailing_run_still_counts():
    rows = [_row(100 - i, "share", "/strategies/%d" % i) for i in range(bell.SHOWN)]
    rows += [_row(50, "share", "/strategies/%d" % (bell.SHOWN - 1)), _row(49, "comment", "/community")]
    out = bell.grouped(rows)
    assert len(out) == bell.SHOWN and out[-1]["more"] == 1


def test_the_endpoint_reads_a_bounded_window_and_groups_it(monkeypatch):
    asked = {}

    def rows(cur, uid, limit=20, before=0):
        asked["limit"] = limit
        return [_row(3, "comment", "/community", read=False), _row(2, "comment", "/community"), _row(1, "share", "/strategies/9")]

    monkeypatch.setattr(server.repo, "list_notifications", rows)
    monkeypatch.setattr(server.repo, "count_unread_notifications", lambda cur, uid: 1)
    out = server.notifications(type("C", (), {"cur": None, "uid": 1, "query": {}})())
    # The window and one row past it, in the same read: whether any notice is older than the list.
    assert asked["limit"] == bell.READ_ROWS + 1
    assert out["unread"] == 1 and [(n["id"], n["more"], n["read"]) for n in out["items"]] == [(3, 1, False), (1, 0, True)]
    assert out["older"] is False


def test_the_unread_notices_are_counted_by_kind_for_the_lists_summary():
    rows = [_row(9, "deployed", "/strategies/9", read=False), _row(8, "deployed", "/strategies/8", read=False),
            _row(7, "paused", "/strategies/3", read=False), _row(6, "paused", "/strategies/3", read=False),
            _row(5, "passed", "/strategies/1"), _row(4, "deployed", "/strategies/4")]
    # Stored rows, not grouped lines: two pauses of one strategy in a row are one line and two notices.
    assert bell.unread_kinds(rows) == {"deployed": 2, "paused": 2}
    assert bell.unread_kinds([_row(1, "share", None)]) == {}


def test_a_notice_about_a_strategy_no_longer_the_users_says_gone():
    # list_notifications joins each strategy link to its row: a deleted strategy (a shared copy removed, one
    # the user deleted) comes back gone=1, so the bell says so instead of linking to "Not found".
    rows = [dict(_row(3, "share", "/strategies/14"), gone=1), dict(_row(2, "passed", "/strategies/6"), gone=0),
            _row(1, "comment", "/community#post-1")]
    out = bell.grouped(rows)
    assert [(n["id"], n["gone"]) for n in out] == [(3, True), (2, False), (1, False)]


def test_a_group_says_whether_its_notices_say_the_same_and_whom_replies_are_from():
    # Walk 2026-10-31: "Bob replied to your post." over replies from Dana, Carol and Bob read "and 3 more like
    # this", as if Bob had written all four; a live pause over an earlier paper pause (another reason) read as more
    # of the same. A group says whether they all say the same words, and replies and mentions name their people.
    post, strat = "/community#post-1", "/strategies/13"
    rows = [_row(20, "comment", post, read=False, body="Bob replied to your post."),
            _row(19, "comment", post, body="Dana replied to your post."),
            _row(18, "comment", post, body="Carol replied to your post."),
            _row(17, "comment", post, body="Bob replied to your post."),
            _row(16, "mention", post, body="Carol mentioned you in a reply."),
            _row(15, "mention", post, body="Bob mentioned you in a post."),
            _row(14, "comment", "/community#post-2", body="Carol replied to your post."),
            _row(13, "comment", "/community#post-2", body="Carol replied to your post."),
            _row(12, "paused", strat, body="“Fade” was paused automatically. Live is down $4.20."),
            _row(11, "paused", strat, body="“Fade” was paused automatically. Lost $12.34.")]
    out = bell.grouped(rows)
    assert [(n["id"], n["more"], n["alike"], n["from"], n["from_more"]) for n in out] == [
        (20, 3, False, ["Dana", "Carol", "Bob"], 0),  # Bob's own second reply names him too
        (16, 1, False, ["Bob"], 0),
        (14, 1, True, ["Carol"], 0),
        (12, 1, False, [], 0)]                      # a pause names no person
    # The words shown are each notice's own, unchanged.
    assert [n["body"] for n in out] == [rows[0]["body"], rows[4]["body"], rows[6]["body"], rows[8]["body"]]


def test_a_group_names_three_people_and_counts_the_rest_and_a_name_reads_whole():
    post = "/community#post-9"
    names = ["Ann", "Bo", "Cy", "Di", "Ed", "Bo"]
    rows = [_row(30, "comment", post, body="Zed replied to your post.")]
    rows += [_row(29 - i, "comment", post, body="%s replied to your post." % n) for i, n in enumerate(names)]
    out = bell.grouped(rows)
    assert (out[0]["more"], out[0]["from"], out[0]["from_more"]) == (6, ["Ann", "Bo", "Cy"], 2)
    # A name with the notice's own words in it reads whole; a body in other words names no one.
    odd = bell.grouped([_row(2, "comment", post, body="Al replied to your post."),
                        _row(1, "comment", post, body="Me replied to your post. replied to your post.")])
    assert odd[0]["from"] == ["Me replied to your post."]
    other = bell.grouped([_row(2, "comment", post, body="Al replied to your post."),
                          _row(1, "comment", post, body="There’s an update on your account.")])
    assert (other[0]["alike"], other[0]["from"]) == (False, [])


def test_a_pause_opens_at_since_deploying_and_a_failed_reload_at_billings_notice_the_stored_link_groups():
    rows = [_row(5, "paused", "/strategies/13", read=False), _row(4, "paused", "/strategies/13"),
            _row(3, "reload_failed", "/billing"), _row(2, "passed", "/strategies/13"), _row(1, "comment", "/community#post-4")]
    out = bell.grouped(rows)
    assert [(n["link"], n["more"]) for n in out] == [("/strategies/13#since-deploying", 1), ("/billing#autorel-fail", 0),
                                                     ("/strategies/13", 0), ("/community#post-4", 0)]
    # Only the plain page link takes the part: anything else is left as stored.
    assert bell.shown_link("paused", "/strategies/13#x") == "/strategies/13#x"
    assert bell.shown_link("paused", None) is None
    assert bell.shown_link("reload_failed", "/billing?x=1") == "/billing?x=1"


def test_the_bell_says_when_older_notices_are_past_its_lines(monkeypatch):
    many = [_row(500 - i, "share", "/strategies/%d" % i) for i in range(bell.READ_ROWS + 1)]
    monkeypatch.setattr(server.repo, "count_unread_notifications", lambda cur, uid: 0)
    ctx = type("C", (), {"cur": None, "uid": 1, "query": {}})()
    for rows, older in ((many, True), (many[:bell.SHOWN], False), (many[:bell.SHOWN + 1], True), ([], False)):
        monkeypatch.setattr(server.repo, "list_notifications", lambda cur, uid, limit=20, before=0, rows=rows: rows[:limit])
        out = server.notifications(ctx)
        assert out["older"] is older, len(rows)
        assert len(out["items"]) == min(len(rows), bell.SHOWN)
    # The 101st row only says there is more: the summary still counts the window of 100.
    unread = [dict(r, read_at=None) for r in many]
    monkeypatch.setattr(server.repo, "list_notifications", lambda cur, uid, limit=20, before=0: unread[:limit])
    assert server.notifications(ctx)["unread_kinds"] == {"share": bell.READ_ROWS}
    # One group that swallows the whole window still has older rows behind it.
    run = [_row(300 - i, "comment", "/community#post-1") for i in range(bell.READ_ROWS + 1)]
    monkeypatch.setattr(server.repo, "list_notifications", lambda cur, uid, limit=20, before=0: run[:limit])
    out = server.notifications(ctx)
    assert (len(out["items"]), out["items"][0]["more"], out["older"]) == (1, bell.READ_ROWS - 1, True)


def test_the_bell_says_how_many_unread_notices_its_lines_stand_for(monkeypatch):
    # 47 unread and 20 lines: the list shows 20 new ones and says the other 27 are older, so the count agrees.
    rows = [_row(500 - i, "deployed", "/strategies/%d" % i, read=True) for i in range(bell.READ_ROWS + 1)]
    for r in rows[:47]:
        r["read_at"] = None
    monkeypatch.setattr(server.repo, "list_notifications", lambda cur, uid, limit=20, before=0: rows[:limit])
    monkeypatch.setattr(server.repo, "count_unread_notifications", lambda cur, uid: 47)
    out = server.notifications(type("C", (), {"cur": None, "uid": 1, "query": {}})())
    assert len(out["items"]) == bell.SHOWN and out["unread_listed"] == bell.SHOWN and out["older"] is True
    # A grouped line counts every unread notice it stands for, and a read one in it none.
    grouped = [_row(9, "comment", "/community#post-4", read=False), _row(8, "comment", "/community#post-4"),
               _row(7, "comment", "/community#post-4", read=False), _row(6, "share", None, read=False)]
    assert bell.unread_listed(grouped, bell.grouped(grouped)) == 3
    assert bell.unread_listed(grouped, bell.grouped(grouped, shown=1)) == 2
    assert bell.unread_listed([], []) == 0


def test_show_older_notices_reads_the_next_page_by_cursor_grouped_alike_with_no_count_and_no_mark(monkeypatch):
    # "Older notices aren't listed here" led nowhere: past the newest 20 lines nothing older could be read. ?before=<id>
    # (the page above's next_before) reads the next page through the same endpoint, grouped as the first page is.
    asked = []
    store = [_row(400 - i, "comment", "/community#post-%d" % (i // 5), read=i % 3 == 0) for i in range(230)]

    def rows(cur, uid, limit=20, before=0):
        asked.append((limit, before))
        return [dict(r) for r in store if not before or r["id"] < before][:limit]

    def boom(*a, **k):
        raise AssertionError("an older page neither counts nor marks anything read")

    monkeypatch.setattr(server.repo, "list_notifications", rows)
    monkeypatch.setattr(server.repo, "count_unread_notifications", lambda cur, uid: 77)
    monkeypatch.setattr(server.repo, "mark_notifications_read", boom)
    ctx = lambda q=None: type("C", (), {"cur": None, "uid": 1, "query": dict(q or {})})()
    first = server.notifications(ctx())
    assert asked[-1] == (bell.READ_ROWS + 1, 0)
    assert first["older"] is True and first["unread"] == 77
    assert first["next_before"] == 301, "the oldest notice the 20 lines stand for (20 runs of 5)"
    monkeypatch.setattr(server.repo, "count_unread_notifications", boom)
    seen, pages, page = sum(1 + n["more"] for n in first["items"]), 1, first
    while page["older"]:
        cursor = page["next_before"]
        page = server.notifications(ctx({"before": str(cursor)}))
        assert asked[-1] == (bell.READ_ROWS + 1, cursor), "the same bounded read, older than the cursor"
        assert set(page) == {"items", "older", "next_before"}
        assert all(n["more"] == 4 for n in page["items"]), "grouped as the first page: one line per post's 5 replies"
        assert not any(n["read"] for n in page["items"]), "each run holds an unread one, and says so"
        seen += sum(1 + n["more"] for n in page["items"])
        pages += 1
    assert (seen, pages) == (len(store), 3), "every notice, once"
    assert page["next_before"] is None
    # A bad cursor reads the first page, never an error page.
    monkeypatch.setattr(server.repo, "count_unread_notifications", lambda cur, uid: 77)
    for bad in ("x", "-5", "0", ""):
        assert server.notifications(ctx({"before": bad}))["unread"] == 77


def test_a_run_the_window_cuts_off_is_left_whole_for_the_next_page():
    # 16 single lines, then 90 replies to one post: the 100-row window cuts that run at its 84th reply. Kept, the first
    # page read "and 83 more" and the next page started with the same post's last 6 as a line of its own.
    rows = [_row(500 - i, "share", "/strategies/%d" % i) for i in range(16)]
    rows += [_row(484 - i, "comment", "/community#post-1") for i in range(90)]
    items, older, nxt = bell.page(rows[:bell.READ_ROWS + 1])
    assert [n["kind"] for n in items] == ["share"] * 16 and older is True and nxt == rows[15]["id"]
    rest, _, _ = bell.page([r for r in rows if r["id"] < nxt][:bell.READ_ROWS + 1])
    assert (rest[0]["kind"], rest[0]["more"]) == ("comment", 89), "the whole run on one line"
    # A run that ends with the window is kept: the row past it is about something else.
    ended = rows[:bell.READ_ROWS] + [_row(1, "passed", "/strategies/9")]
    items, older, nxt = bell.page(ended)
    assert (len(items), items[-1]["more"], older, nxt) == (17, 83, True, ended[bell.READ_ROWS - 1]["id"])
    # Nothing older: no cursor.
    assert bell.page(rows[:5])[1:] == (False, None)
