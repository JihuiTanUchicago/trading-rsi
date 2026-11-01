"""The bell's line when a research session the customer started finishes (services.research_notice).

Nothing told a customer their session had ended: they went back to Research to check. The words say how
it ended and how many ideas passed, in the Research page's terms, and never carry the run's stored error
(a provider's text can be there), an id or a model name. Autopilot sessions and cancelled ones get none.
The write-once guard against a real MySQL is tests/test_research_notice_db.py.
"""
from app.services import customer_text, research_notice
from app.services.research_notice import body, passed_words


def _run(**over):
    run = {"id": 41, "user_id": 7, "kind": "research", "market": "btc15m", "status": "succeeded", "run_forever": 0,
           "by_autopilot": 0, "hypotheses_tried": 10, "strategies_passed": 2, "error": None, "out_of_credits": 0}
    run.update(over)
    return run


def test_each_way_a_session_ends_reads_in_plain_words():
    on = "Your research session on Bitcoin up or down in 15 minutes"
    assert body(_run()) == on + " finished. 2 of 10 ideas passed."
    assert body(_run(run_forever=1, hypotheses_tried=40, strategies_passed=0)) == (
        on + " finished: it ran out of new ideas to try. None of the 40 ideas it tested passed.")
    assert body(_run(run_forever=1, out_of_credits=1, hypotheses_tried=1200, strategies_passed=3)) == (
        on + " stopped because your credits ran out. 3 of 1,200 ideas passed.")
    assert body(_run(status="failed", error="ThrottlingException: Rate exceeded (us.anthropic.claude-opus-4-6-v1)"),
                passed=1) == on + " stopped on an error. 1 of 10 ideas passed."
    assert body(_run(status="stopped", error="Tested 3 ideas, then the session was interrupted and stopped — start a new one.",
                     hypotheses_tried=3, strategies_passed=0)) == (
        on + " was interrupted and stopped. None of the 3 ideas it tested passed.")
    assert body(_run(status="stopped", error="No settled contracts yet.", hypotheses_tried=0, strategies_passed=0)) == (
        on + " stopped: that market has nothing to research yet. It didn’t test any ideas.")


def test_a_session_with_no_market_yet_or_an_unlisted_one_never_shows_the_key():
    assert body(_run(market=None)) == "Your research session finished. 2 of 10 ideas passed."
    assert body(_run(market="kxfoo_unlisted")) == "Your research session finished. 2 of 10 ideas passed."


def test_counts_read_as_words():
    assert passed_words(0, 0) == "It didn’t test any ideas."
    assert passed_words(1, 0) == "The one idea it tested didn’t pass."
    assert passed_words(1, 1) == "1 of 1 idea passed."
    assert passed_words(3, 5) == "5 of 5 ideas passed."  # never more passed than tested


def test_autopilot_cancelled_running_and_other_kinds_get_no_notice():
    assert body(_run(by_autopilot=1)) is None
    assert body(_run(status="cancelled")) is None
    for st in ("queued", "running", "paused"):
        assert body(_run(status=st)) is None
    assert body(_run(kind="review")) is None
    assert body(_run(kind="chat", status="failed")) is None


def test_no_notice_leaks_an_id_a_model_or_the_stored_error():
    raw = "Traceback (most recent call last): botocore ThrottlingException us.anthropic.claude-opus-4-6-v1 run #41"
    for over in ({}, {"status": "failed"}, {"status": "stopped", "error": "interrupted: " + raw}, {"run_forever": 1, "out_of_credits": 1}):
        text = body(_run(**{"error": raw, **over}))
        assert text and customer_text.leaks(text) == []
        assert "41" not in text and "#" not in text and "btc15m" not in text
    assert research_notice.link(41) == "/research/41"
    assert len(research_notice.KIND) <= 16  # app_notifications.kind


class _Cur:
    """Enough of a cursor for ring(): finished_run's row, then the notice insert."""

    def __init__(self, row, fail=False):
        self.row, self.fail, self.sql, self.rowcount, self.lastrowid = row, fail, [], 0, 0

    def execute(self, sql, params=()):
        if self.fail:
            raise RuntimeError("db gone")
        self.sql.append((sql, params))
        if sql.startswith("INSERT INTO app_notifications"):
            self.rowcount, self.lastrowid = 1, 99

    def fetchone(self):
        return self.row


def test_ring_writes_the_line_once_through_the_idempotent_insert_and_never_raises():
    cur = _Cur(_run())
    assert research_notice.ring(cur, 41) is True
    insert = [s for s in cur.sql if s[0].startswith("INSERT")]
    assert len(insert) == 1 and "WHERE NOT EXISTS" in insert[0][0]
    assert insert[0][1][:4] == (7, "research_done", "Your research session on Bitcoin up or down in 15 minutes finished. "
                                "2 of 10 ideas passed.", "/research/41")
    # An autopilot session: read, nothing written.
    cur = _Cur(_run(by_autopilot=1))
    assert research_notice.ring(cur, 41) is False and not [s for s in cur.sql if s[0].startswith("INSERT")]
    # A bell hiccup never fails the finish that called it.
    assert research_notice.ring(_Cur(_run(), fail=True), 41) is False


def _wired(monkeypatch, took=True):
    """agent.run_research on the in-memory store test_app_agent uses (a scripted model: no LLM call), with
    finish_run answering whether this call ended the run, and the notices it rings recorded."""
    from tests.test_app_agent import Store
    from app.research import agent

    store, rung = Store(monkeypatch), []
    monkeypatch.setattr(agent.repo, "finish_run", lambda cur, run_id, status, error=None, tried=0, passed=0, *a:
                        setattr(store, "finished", (status, error, tried, passed)) or took)
    monkeypatch.setattr(agent.research_notice, "ring", lambda cur, run_id, passed=None: rung.append((run_id, passed)) or True)
    return agent, store, rung


def test_the_agent_rings_once_where_it_marks_the_session_finished(monkeypatch):
    from tests.test_app_agent import Conn, RUN, WINNER, LOSER, scripted, plan, hyps
    from tests.test_app_backtest import _dataset

    agent, store, rung = _wired(monkeypatch)
    model, _ = scripted([plan(), hyps(WINNER, LOSER), hyps(WINNER)])
    agent.run_research(Conn(), {**RUN, "max_hypotheses": 3}, model=model, dataset_fn=lambda m, h=None: _dataset(n_contracts=60))
    assert store.finished[0] == "succeeded" and rung == [(1, None)]
    # Ended on an error: the row keeps 0 passed, so the notice is handed the count that passed before it.
    agent, store, rung = _wired(monkeypatch)
    model, _ = scripted([plan()] + ["no json here"] * 6)
    agent.run_research(Conn(), RUN, model=model, dataset_fn=lambda m, h=None: _dataset(n_contracts=60))
    assert store.finished[0] == "failed" and rung == [(1, 0)]
    agent, store, rung = _wired(monkeypatch)
    agent.run_research(Conn(), RUN, model=scripted([])[0], dataset_fn=lambda m, h=None: _dataset(n_contracts=2))
    assert store.finished[0] == "stopped" and rung == [(1, 0)]


def test_a_finish_that_did_not_end_the_run_rings_nothing(monkeypatch):
    from tests.test_app_agent import Conn, RUN, WINNER, LOSER, scripted, plan, hyps
    from tests.test_app_backtest import _dataset

    # The reaper (or another worker) ended it first: this call's UPDATE took no row.
    agent, store, rung = _wired(monkeypatch, took=False)
    model, _ = scripted([plan(), hyps(WINNER, LOSER), hyps(WINNER)])
    agent.run_research(Conn(), {**RUN, "max_hypotheses": 3}, model=model, dataset_fn=lambda m, h=None: _dataset(n_contracts=60))
    assert store.finished[0] == "succeeded" and rung == []
