"""Every JSON reply names each theme's build as its page shell carries it now (X-Tau-Build), so a tab left open across
a deploy learns it runs an older bundle from the replies it already reads (api() in app.js and app.meridian.js, core.js
buildMoved): Tau asked /api/version only on a page change, and Meridian never asked (walk 2026-11-01). Through the real
dispatch, with synthetic sessions and no database or outside requests."""
import re
from http.server import ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest
import requests

from app import server
from app.web import page_modules


@pytest.fixture
def web(monkeypatch):
    connection = SimpleNamespace(cursor=lambda: None, close=lambda: None)
    monkeypatch.setattr(server, "get_db_conn", lambda: connection)
    seven = server.auth.hash_token("session-seven")
    monkeypatch.setattr(server.repo, "session_user", lambda cur, token: {"id": 7} if token == seven else None)

    def refused(ctx):
        raise server.ApiError(404, "No such strategy.")
    monkeypatch.setattr(server, "ROUTES", [
        ("GET", re.compile("^/api/probe$"), lambda ctx: {"ok": True}, True),
        ("GET", re.compile("^/api/refused$"), refused, True),
        ("GET", re.compile("^/api/public$"), lambda ctx: {"ok": True}, False),
    ])
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = Thread(target=http.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%s" % http.server_port
    http.shutdown()
    http.server_close()
    thread.join(timeout=2)


def cookie(token):
    return {"Cookie": "%s=%s" % (server.auth.SESSION_COOKIE, token)}


def test_every_json_reply_names_each_theme_s_current_build(web):
    versions = server._static_versions()
    want = "tau:%s,meridian:%s" % (page_modules.build_version("tau", versions), page_modules.build_version("meridian", versions))
    # A read, a refusal Tau answered, a signed-out reply (the session ended) and an unknown route all carry it: the
    # page compares whatever it reads.
    for path, headers, status in (("/api/probe", cookie("session-seven"), 200), ("/api/refused", cookie("session-seven"), 404),
                                  ("/api/probe", cookie("expired"), 401), ("/api/public", {}, 200), ("/api/nowhere", {}, 404)):
        r = requests.get(web + path, headers=headers, timeout=2)
        assert r.status_code == status and r.headers["X-Tau-Build"] == want, path


def test_the_header_names_the_builds_the_page_shell_carries():
    # The same hashes the shell puts in data-tau-ver / data-mer-ver (window.__BUILD__): names and hex only.
    versions = {name: "%010x" % i for i, name in enumerate(server._STATIC_NAMES)}
    said = page_modules.builds_header(versions)
    assert said == "tau:%s,meridian:%s" % (page_modules.build_version("tau", versions), page_modules.build_version("meridian", versions))
    assert re.fullmatch(r"tau:[0-9a-f]{10},meridian:[0-9a-f]{10}", said)
    # A deploy that changes one theme's page file moves that theme's build only; a new versions read is worked out anew.
    moved = dict(versions, **{"app.meridian-funds.js": "ffffffffff"})
    again = page_modules.builds_header(moved)
    assert again.split(",")[0] == said.split(",")[0] and again.split(",")[1] != said.split(",")[1]
    # The core is in both themes' builds.
    both = page_modules.builds_header(dict(versions, **{page_modules.CORE: "eeeeeeeeee"}))
    assert all(a != b for a, b in zip(both.split(","), said.split(",")))
