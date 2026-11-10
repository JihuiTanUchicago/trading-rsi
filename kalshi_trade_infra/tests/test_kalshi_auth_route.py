"""Authenticated Kalshi calls go to Kalshi's origin (external-api) only after a signed read works there,
fall back to CloudFront on connection/gateway failures, never re-send an order, and retry a GET once."""
import pytest
import requests

from data_pipeline import common

ORIGIN, CF = "https://origin.test", "https://cf.test"


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body, self.text = status, body if body is not None else {"ok": True}, "x"

    def json(self):
        return self._body


class _Sess:
    """Scripted session: answers[(method, host)] is a list of _Resp or exceptions, popped in order."""
    def __init__(self, host, log, answers):
        self.host, self.log, self.answers = host, log, answers

    def _do(self, method, url, **k):
        self.log.append((method, url))
        q = self.answers.get((method, self.host)) or [_Resp()]
        a = q.pop(0) if len(q) > 1 else q[0]
        if isinstance(a, Exception):
            raise a
        return a

    def get(self, url, **k):
        return self._do("GET", url, **k)

    def post(self, url, **k):
        return self._do("POST", url, **k)


@pytest.fixture
def route(monkeypatch):
    log, answers, sessions = [], {}, {}
    r = common._AuthRoute(ORIGIN, CF, keepalive_s=0)
    monkeypatch.setattr(r, "session", lambda host: sessions.setdefault(host, _Sess(host, log, answers)))
    monkeypatch.setattr(common, "AUTH_ROUTE", r)
    monkeypatch.setattr(common, "kalshi_auth_headers", lambda *a, **k: {})
    return r, log, answers


def test_signed_check_then_orders_go_to_origin(route):
    r, log, _ = route
    common.api_post_auth("/trade-api/v2/portfolio/events/orders", {"a": 1}, timing=(t := {}))
    assert log[0] == ("GET", ORIGIN + "/trade-api/v2/portfolio/balance")      # the read-only check first
    assert log[1] == ("POST", ORIGIN + "/trade-api/v2/portfolio/events/orders")
    assert r.state == "ok" and t["host"] == "origin"
    common.api_get_auth("/trade-api/v2/portfolio/positions")
    assert log[2] == ("GET", ORIGIN + "/trade-api/v2/portfolio/positions") and len(log) == 3   # checked once


def test_failed_check_keeps_cloudfront_until_recheck(route, monkeypatch):
    r, log, answers = route
    answers[("GET", ORIGIN)] = [_Resp(401)]
    common.api_post_auth("/trade-api/v2/portfolio/events/orders", {}, timing=(t := {}))
    assert r.state == "bad" and log[-1] == ("POST", CF + "/trade-api/v2/portfolio/events/orders")
    assert t["host"] == "cloudfront"
    n = len(log)
    common.api_get_auth("/trade-api/v2/portfolio/positions")
    assert len(log) == n + 1 and log[-1][1].startswith(CF)                     # no re-check inside the window
    monkeypatch.setattr(common.time, "time", lambda: r.retry_at + 1)
    answers[("GET", ORIGIN)] = [_Resp()]
    common.api_get_auth("/trade-api/v2/portfolio/positions")
    assert r.state == "ok" and log[-1][1].startswith(ORIGIN)                   # re-checked and back on origin


def test_order_connection_error_is_not_resent(route):
    r, log, answers = route
    answers[("POST", ORIGIN)] = [requests.ConnectionError("reset")]
    with pytest.raises(requests.ConnectionError):
        common.api_post_auth("/trade-api/v2/portfolio/events/orders", {})
    assert [m for m, _ in log].count("POST") == 1                             # one POST, never repeated on CF
    assert r.state == "bad"
    common.api_post_auth("/trade-api/v2/portfolio/events/orders", {})
    assert log[-1] == ("POST", CF + "/trade-api/v2/portfolio/events/orders")  # the NEXT order uses CloudFront


def test_order_gateway_status_flips_route_and_still_raises(route):
    r, log, answers = route
    answers[("POST", ORIGIN)] = [_Resp(502)]
    with pytest.raises(RuntimeError):
        common.api_post_auth("/trade-api/v2/portfolio/events/orders", {})
    assert r.state == "bad"


def test_order_rejection_keeps_origin(route):
    r, _, answers = route
    answers[("POST", ORIGIN)] = [_Resp(400, {"error": "insufficient_balance"})]
    with pytest.raises(RuntimeError):
        common.api_post_auth("/trade-api/v2/portfolio/events/orders", {})
    assert r.state == "ok"                                                    # an exchange answer is not a route failure


def test_get_retries_once_on_cloudfront(route):
    r, log, answers = route
    answers[("GET", ORIGIN)] = [_Resp(), requests.Timeout("slow")]           # check ok, then the read times out
    out = common.api_get_auth("/trade-api/v2/portfolio/positions")
    assert out == {"ok": True} and log[-1] == ("GET", CF + "/trade-api/v2/portfolio/positions")
    assert r.state == "bad"


def test_route_off_for_non_production_host():
    r = common._AuthRoute(None, "https://demo-api.kalshi.co", keepalive_s=0)
    assert r.host() == "https://demo-api.kalshi.co" and r.state == "off"


def test_make_route_only_direct_for_cloudfront_default(monkeypatch):
    monkeypatch.setattr(common, "KALSHI_API_HOST", "https://demo-api.kalshi.co")
    assert common._make_auth_route().primary is None
    monkeypatch.setattr(common, "KALSHI_API_HOST", common.KALSHI_CLOUDFRONT_HOST)
    assert common._make_auth_route().primary == common.KALSHI_ORIGIN_HOST
    monkeypatch.setattr(common, "KALSHI_DIRECT_ENABLED", False)
    assert common._make_auth_route().primary is None
