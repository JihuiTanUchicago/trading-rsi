"""Odd input from a signed-in user answers a plain 4xx in Tau's words, through the real HTTP dispatch: never a 500, a
hung request, or a write before the refusal. Another user's row answers exactly as a missing one. Synthetic
session and database; no Kalshi, model, Stripe or email call."""
import socket
from http.server import ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest
import requests

from app import server
from app.web.routes import community as community_routes
from app.web.routes import explore as explore_routes
from app.web.routes import research as research_routes
from app.web.routes import strategies as strategy_routes

SERVER_ERROR = "Something went wrong on our side"


@pytest.fixture
def web(monkeypatch):
    connection = SimpleNamespace(cursor=lambda: None, close=lambda: None)
    monkeypatch.setattr(server, "get_db_conn", lambda: connection)
    monkeypatch.setattr(server.repo, "session_user", lambda cur, token: {"id": 7, "autopilot": 0})
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = Thread(target=http.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    url = "http://127.0.0.1:%s" % http.server_port
    headers = {"Cookie": "rsid=synthetic-session", "X-Recursive": "1"}
    yield SimpleNamespace(url=url, port=http.server_port, headers=headers,
                          get=lambda path: requests.get(url + path, headers=headers, timeout=3),
                          post=lambda path, body: requests.post(url + path, json=body, headers=headers, timeout=3))
    http.shutdown()
    http.server_close()
    thread.join(timeout=2)


BAD_NUMBERS = ["abc", "1.5", "1e3", "NaN", "inf", "0x10", "%E2%82%AC", "x" * 300, "99999999999999999999999999"]


@pytest.mark.parametrize("value", BAD_NUMBERS)
def test_chat_reads_from_the_start_when_its_cursor_is_not_a_number(web, monkeypatch, value):
    asked = []
    monkeypatch.setattr(server.repo, "active_agent_run", lambda cur, uid: None)
    monkeypatch.setattr(server.repo, "chat_messages", lambda cur, uid, after: asked.append(after) or [])
    r = web.get("/api/chat?after=" + value)
    assert r.status_code == 200, r.text
    assert r.json()["messages"] == []
    assert asked == [2 ** 63 - 1 if value.isdigit() else 0]  # held to MySQL's range, else from the start


@pytest.mark.parametrize("value", BAD_NUMBERS + ["-5"])
def test_community_feed_reads_its_first_page_when_before_is_not_a_number(web, monkeypatch, value):
    asked = []
    monkeypatch.setattr(server.repo, "list_posts", lambda cur, uid, before, limit, mine_only: asked.append(before) or [])
    r = web.get("/api/community?before=" + value)
    assert r.status_code == 200 and r.json() == {"posts": []}
    assert asked == [2 ** 63 - 1 if value.isdigit() else 0]


class Reached(Exception):
    pass


@pytest.mark.parametrize("query", ["before=abc", "after=abc", "before=1.5&after=%E2%82%AC", "before=&after=-3"])
def test_session_page_reads_its_first_page_when_its_cursors_are_not_numbers(web, monkeypatch, query):
    monkeypatch.setattr(server.repo, "get_run", lambda cur, uid, rid: {"id": rid, "kind": "research"} if rid == 5 else None)
    monkeypatch.setattr(research_routes, "_earlier_run_events", lambda *a: pytest.fail("no earlier page was asked for"))

    def first_page(*a, **k):  # the first page's own reads start here: the cursors were read without a failure
        raise server.ApiError(409, "first page")
    monkeypatch.setattr(server.repo, "list_strategies", first_page)
    r = web.get("/api/runs/5?" + query)
    assert r.status_code == 409 and r.json()["error"] == "first page"


def test_another_users_session_answers_as_a_missing_one(web, monkeypatch):
    # get_run is asked with the signed-in user's id, so another user's session reads as None: the same 404.
    monkeypatch.setattr(server.repo, "get_run", lambda cur, uid, rid: {"id": 5, "kind": "research"} if (uid, rid) == (7, 5) else None)
    other, missing = web.get("/api/runs/6?before=abc"), web.get("/api/runs/999999999?before=abc")
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json() == {"error": "No such research session."}


def test_strategy_list_holds_a_huge_page_to_one_mysql_can_read(web, monkeypatch):
    seen = {}
    monkeypatch.setattr(server.repo, "count_strategies", lambda *a: {"total": 0})
    monkeypatch.setattr(server.repo, "passed_strategies", lambda *a, **k: [])
    monkeypatch.setattr(server.repo, "list_strategies", lambda *a, **k: seen.update(k) or [])
    r = web.get("/api/strategies?page=99999999999999999999")
    assert r.status_code == 200, r.text
    assert r.json()["strategies"] == [] and r.json()["page"] == strategy_routes.MAX_LIST_PAGE
    assert seen["offset"] == strategy_routes.MAX_LIST_PAGE * 50 < 2 ** 63
    assert web.get("/api/strategies?page=abc").json()["page"] == 0


@pytest.mark.parametrize("body,words", [
    ({"market": ["btc15m"], "prompt": "x"}, "Pick a market Tau can research, or let the agent choose."),
    ({"market": {"a": 1}}, "Pick a market Tau can research, or let the agent choose."),
    ({"model": ["x"]}, "Pick a model from the list, or leave it on the default."),
    ({"model": {"a": 1}}, "Pick a model from the list, or leave it on the default."),
])
def test_research_form_refuses_a_market_or_model_that_is_not_text(web, monkeypatch, body, words):
    monkeypatch.setattr(server.repo, "create_run", lambda *a, **k: pytest.fail("nothing is written"))
    r = web.post("/api/runs", body)
    assert r.status_code == 400 and r.json() == {"error": words}


def test_dig_deeper_refuses_a_model_that_is_not_text_and_another_users_strategy_reads_as_missing(web, monkeypatch):
    owned = {"id": 3, "status": "passed", "market": "btc15m", "spec": {}}
    monkeypatch.setattr(server.repo, "get_strategy", lambda cur, uid, sid: owned if (uid, sid) == (7, 3) else None)
    monkeypatch.setattr(research_routes.agent, "research_unavailable_reason", lambda market: None)
    monkeypatch.setattr(research_routes.agent, "dig_deeper_run", lambda *a, **k: pytest.fail("nothing is written"))
    for model in (["x"], {"a": 1}):
        r = web.post("/api/strategies/3/dig-deeper", {"model": model})
        assert r.status_code == 400 and r.json() == {"error": "Pick a model from the list, or leave it on the default."}
    other, missing = web.post("/api/strategies/4/dig-deeper", {}), web.post("/api/strategies/999999999/dig-deeper", {})
    assert other.status_code == missing.status_code == 404 and other.json() == missing.json() == {"error": "No such strategy."}


@pytest.mark.parametrize("model", [5, True, ["x"], {"a": 1}])
def test_autopilot_model_refuses_a_model_that_is_not_text(web, monkeypatch, model):
    monkeypatch.setattr(server.repo, "set_autopilot_model", lambda *a: pytest.fail("nothing is written"))
    r = web.post("/api/autopilot/model", {"model": model})
    assert r.status_code == 400 and r.json() == {"error": "Pick a model from the list, or leave it on the default."}


def test_a_share_of_another_users_or_an_unreadable_row_answers_as_a_missing_one_and_stores_nothing(web, monkeypatch):
    monkeypatch.setattr(server.repo, "recent_posts_count", lambda *a: 0)
    monkeypatch.setattr(server.repo, "get_strategy", lambda cur, uid, sid: None)  # none of these is the user's
    monkeypatch.setattr(server.repo, "get_deployment", lambda cur, uid, did: None)
    monkeypatch.setattr(server.repo, "store_image", lambda *a: pytest.fail("a refused share stores no photo"))
    monkeypatch.setattr(server.repo, "create_post", lambda *a: pytest.fail("nothing is posted"))
    monkeypatch.setattr(community_routes, "_decode_image", lambda data: ("image/png", b"\x89PNG"))
    for kind, words in (("strategy", "That strategy isn’t yours or doesn’t exist."),
                        ("deployment", "That deployment isn’t yours or doesn’t exist.")):
        answers = [web.post("/api/community", {"kind": kind, "body": "look", "ref_id": ref, "image": "data:image/png;base64,AA"})
                   for ref in (4, 999999999, "abc", "1.5", 1.5, [1], True, 99999999999999999999999)]
        assert {(a.status_code, a.json()["error"]) for a in answers} == {(404, words)}


def test_waitlist_refuses_an_email_that_is_not_text(web, monkeypatch):
    monkeypatch.setattr(server.repo, "add_waitlist", lambda *a: pytest.fail("nothing is written"))
    for email in (["a@b.co"], {"a": "a@b.co"}, 5):
        r = web.post("/api/waitlist", {"email": email})
        assert r.status_code == 400 and r.json() == {"error": "Enter a valid email address."}


@pytest.mark.parametrize("length", ["abc", "-1", "-5", "1.5", "²"])
def test_an_unreadable_content_length_is_refused_at_once(web, length):
    s = socket.create_connection(("127.0.0.1", web.port), timeout=2)  # requests would fix the header up
    s.sendall(("POST /api/ui-theme HTTP/1.1\r\nHost: x\r\nCookie: rsid=synthetic-session\r\nX-Recursive: 1\r\n"
               "Content-Type: application/json\r\nContent-Length: %s\r\n\r\n{}" % length).encode("latin-1"))
    chunks = []
    while True:  # the answer says Connection: close, so it ends when the server hangs up (-1 waited for the client)
        chunk = s.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
    s.close()
    reply = b"".join(chunks).decode("utf-8", "replace")
    assert reply.startswith("HTTP/1.0 400") or reply.startswith("HTTP/1.1 400"), reply[:80]
    assert "Tau couldn" in reply and SERVER_ERROR not in reply


@pytest.mark.parametrize("ticker", ["../../etc", "KX?x=1", "a/b", "-KX", "x" * 101, "KX..1", "€"])
def test_explore_market_answers_a_malformed_ticker_as_missing_without_asking_kalshi(web, monkeypatch, ticker):
    import data_pipeline.common as common
    monkeypatch.setattr(common, "api_get", lambda *a, **k: pytest.fail("Kalshi is never asked"))
    r = requests.get(web.url + "/api/explore/market", params={"ticker": ticker}, headers=web.headers, timeout=3)
    assert r.status_code == 404 and r.json() == {"error": explore_routes.NO_MARKET}
