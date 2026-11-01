"""services.page_poll: whether the demo-funds check waits on the demo cash moves without a write (the demo cash memo
ages out every minute), so it rides each "same" answer instead of moving the version. On a heavy account the Deploy
board (95KB) and the dashboard (79KB) each sent their whole answer twice a minute for that flag alone: the pending
answer, then the one after the page's demo cash read (walk 2026-11-01). The check's line itself still moves it."""
from app.services import page_poll


def _board(**over):
    return {"deployments": [{"id": 1, "unrealized_pnl": 0.1, "title": "Fade"}], "open_positions": [], "demo_health": None, **over}


def _dash(**demo):
    return {"paper": {"realized_pnl": 1.0}, "demo": {"realized_pnl": 2.0, "health": None, **demo}, "deployments": [],
            "open_positions": []}


def test_the_demo_checks_pending_flag_never_moves_a_polled_pages_version():
    assert page_poll.version(_board(demo_health_pending=True)) == page_poll.version(_board())
    assert page_poll.version(_dash(health_pending=True)) == page_poll.version(_dash())


def test_the_flag_rides_the_same_answer_and_only_while_it_is_on():
    v = page_poll.version(_board())
    same = page_poll.answer(_board(demo_health_pending=True), v)
    assert same["same"] is True and same["demo_health_pending"] is True
    assert "demo_health_pending" not in page_poll.answer(_board(), v)
    same = page_poll.answer(_dash(health_pending=True), page_poll.version(_dash()))
    assert same["same"] is True and same["demo_health_pending"] is True
    assert "demo_health_pending" not in page_poll.answer(_dash(), page_poll.version(_dash()))


def test_a_changed_demo_line_still_moves_the_version_and_a_full_answer_keeps_its_flag():
    line = {"code": "demo_funds", "line": "Demo isn't trading: your Kalshi demo account has $0.00."}
    assert page_poll.version(_board(demo_health=line)) != page_poll.version(_board())
    assert page_poll.version(_dash(health=line)) != page_poll.version(_dash())
    full = page_poll.answer(_board(demo_health_pending=True), "old")
    assert full["demo_health_pending"] is True and not full.get("same")
    full = page_poll.answer(_dash(health_pending=True), "old")
    assert full["demo"]["health_pending"] is True
