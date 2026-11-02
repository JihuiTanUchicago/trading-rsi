"""Each strategy has one verdict, said in the same words everywhere a customer sees it, against a real MySQL. One
account holds a strategy in every verdict state (draft, still testing, rejected, void, passed then in its paper
forward test, passed with an inconclusive or a rejected validation, a validation pass, a deployment candidate, a
missed final test, held back by diversification after its holdout or after paper, forward test passed, forward test
failed, live-ready, and one shared to Community then deleted). For each, the Strategies list, the strategy page, a
research session's rows, Home's new-results strip, a Community share card and the chat's context give the same
verdict and the same stamp (services.verdicts.word, which core.js's MARK and VERDICTS say alike:
test_verdict_everywhere.py). A strategy stored "passed" whose own validation didn't pass read Passed on its list row,
its page's stamp, its session row and its Community card, above a glance saying it didn't pass validation, its
validation figures tinted as passed (walk 2026-11-02). The stored status, the gates and their decisions are unchanged.
Written through the repository layer only (no engine, no order). Opt in as for test_app_engine_db.py
(APP_TEST_MYSQL=1).
"""
import os
import uuid
from datetime import timedelta

import pytest

pytestmark = pytest.mark.skipif(os.getenv("APP_TEST_MYSQL") != "1", reason="needs APP_TEST_MYSQL=1 and a MySQL")

OK = {"p_value": 0.01, "mean_net_return": 0.02, "ci_lo": 0.002, "ci_hi": 0.04, "fdr_alpha_i": 0.0125}
WEAK = {"p_value": 0.18, "mean_net_return": 0.012, "ci_lo": -0.01, "ci_hi": 0.05, "fdr_alpha_i": 0.0125, "placebo_p": 0.33}
LUCK = {"p_value": 0.48, "mean_net_return": -0.003, "ci_lo": -0.03, "ci_hi": 0.025}
SUP, INC, REJ = ("validation", "statistically_supported", OK), ("validation", "inconclusive", WEAK), ("validation", "rejected", LUCK)
HOLD_OK, HOLD_BAD = ("holdout", "forward_validated", OK), ("holdout", "rejected", LUCK)
FWD_DC = ("forward", "deployment_candidate", {})
PAPER_OK, PAPER_DC, PAPER_BAD = ("paper", "forward_validated", OK), ("paper", "deployment_candidate", OK), ("paper", "inconclusive", LUCK)

# key: (stored status, gatekeeper rows or None, deployed on paper, the verdict every answer carries, the stamp).
STATES = {
    "draft": ("pending", None, False, None, "Not judged"),
    "testing": ("queued", None, False, None, "Testing"),
    "rejected": ("rejected", [REJ], False, None, "Rejected"),
    "void": ("failed", [INC], False, None, "Void"),
    "void on paper": ("failed", [INC], True, None, "Void"),
    "passed, in its paper forward test": ("passed", [INC], True, "inconclusive", "Validation inconclusive"),
    "passed, validation inconclusive": ("passed", [INC], False, "inconclusive", "Validation inconclusive"),
    "passed, validation rejected": ("passed", [REJ], False, "missed_validation", "Missed validation"),
    "validation pass": ("passed", [SUP], False, None, "Passed"),
    "candidate": ("passed", [SUP, HOLD_OK, FWD_DC], False, None, "Passed"),
    "missed final test": ("passed", [SUP, HOLD_BAD], False, "missed_final", "Missed final test"),
    "held back after its holdout": ("passed", [SUP, HOLD_OK], False, "held_back", "Held back"),
    "held back after paper": ("failed", [INC, PAPER_OK], True, "held_back", "Held back"),
    "forward test passed": ("passed", [INC, PAPER_OK, PAPER_DC], True, None, "Passed"),
    "forward test failed": ("failed", [INC, PAPER_BAD], True, None, "Void"),
    "live-ready": ("passed", [SUP, HOLD_OK, FWD_DC], True, None, "Passed"),
}
VALIDATION_MISS = {"inconclusive", "missed_validation"}


@pytest.fixture(scope="module")
def conn():
    from app import schema
    from data_pipeline.common import get_db_conn

    schema.main()
    c = get_db_conn()
    c.autocommit(True)
    yield c
    c.cursor().execute("UPDATE app_deployments SET status='stopped' WHERE user_id IN "
                       "(SELECT id FROM app_users WHERE google_sub LIKE 'test:%%')")
    c.close()


@pytest.fixture
def quiet(monkeypatch):
    from app.adapters.stripe import billing
    from app.services import book_memo
    from app.trading import engine
    from app.web.routes import dashboard as dashboard_routes

    monkeypatch.setattr(billing, "STRIPE_SECRET_KEY", "")
    monkeypatch.setattr(engine, "LIVE_TRADING_ENABLED", False)
    monkeypatch.setattr(dashboard_routes, "_freshness", lambda c: {})
    book_memo.clear()
    yield
    book_memo.clear()


def test_every_place_says_each_strategys_one_verdict(conn, quiet):
    from app import repository as repo
    from app import server
    from app.research import agent
    from app.research import research_repository as rr
    from app.services import chat_facts, verdicts
    from app.trading import spec as specmod
    from app.web.routes import community, dashboard

    cur = conn.cursor()
    uid = repo.upsert_google_user(cur, "test:" + uuid.uuid4().hex, "everywhere@example.invalid", "E", None)["id"]
    spec = specmod.validate({"side": "yes", "entry": {"min_edge": 0.05}, "sizing": {"stake": 5.0, "max_positions": 1}})
    run = repo.create_run(cur, uid, "research", "btc15m", "custom", "idea")
    curve = [[1761000000 + 3600 * i, 0.05 if i % 3 else -0.04] for i in range(40)]

    def strategy(title, status, rows, paper):
        sid = repo.insert_strategy(cur, uid, run, None, "btc15m", "custom", title, "t", spec, status,
                                   {"classification": "statistically_supported", "out_of_sample": {"pnl": 3.1, "trades": 40}})
        if rows is not None:
            eid = rr.preregister(cur, {"user_id": uid, "run_id": run, "strategy_id": sid, "market": "btc15m",
                                       "hypothesis_hash": uuid.uuid4().hex, "spec": spec, "economic_rationale": "{}",
                                       "primary_metric": "m", "null_hypothesis": "", "alt_hypothesis": "", "planned_test": "t",
                                       "falsification_criteria": "", "min_sample": 40, "cost_assumptions": {},
                                       "research_partition_id": 0, "planned_n_tests": 2, "correction_method": "BH"})
            for phase, cls, m in rows:
                rr.append_result(cur, eid, phase, cls, 48, metrics=dict(m, holdout_consumed=phase == "holdout"), equity_curve=curve)
            cur.execute("UPDATE app_strategies SET experiment_id=%s WHERE id=%s", (eid, sid))
        if paper:
            repo.create_deployment(cur, uid, sid, "btc15m", "paper")
        return sid

    sid = {k: strategy("State %d" % i, st, rows, paper) for i, (k, (st, rows, paper, _, _)) in enumerate(STATES.items())}
    gone = strategy("Shared then deleted", "passed", [INC], False)
    want = {sid[k]: (v[3], v[4]) for k, v in STATES.items()}
    name = {s: k for k, s in sid.items()}
    user = repo.get_user(cur, uid)
    ctx = lambda args=(), q=None: server.Ctx(conn, user, args, q or {}, {}, None)  # noqa: E731

    # Community: a share card freezes the verdict and its stamp with the status, and keeps them once the strategy
    # is deleted.
    snaps = {s: community._post_snapshot(cur, uid, "strategy", s) for s in list(sid.values()) + [gone]}
    cur.execute("DELETE FROM app_strategies WHERE id=%s", (gone,))
    assert (snaps[gone].get("verdict"), verdicts.word(snaps[gone]["status"], snaps[gone].get("verdict"))) == (
        "inconclusive", "Validation inconclusive")

    # The Strategies list, every view: the same verdict, and the chips count by it.
    listed = {}
    for status in ("", "failed", "rejected", "passed", "not_cleared"):
        got = server.list_strategies(ctx(q={"status": status, "page": "0"}))
        listed.update({c["id"]: c for c in got["strategies"]})
    counts = got["counts"]
    run_page = {c["id"]: c for c in server.get_run(ctx((str(run),)))["strategies"]}
    stamps = chat_facts.page_stamps(cur, uid, repo.list_strategies(cur, uid, run_id=run))
    for s, (verdict, stamp) in want.items():
        k = name[s]
        row = listed[s]
        assert (row.get("verdict"), verdicts.word(row["status"], row.get("verdict"))) == (verdict, stamp), k + ": list"
        page = server.get_strategy(ctx((str(s),)))
        assert page["status"] == STATES[k][0], k  # the stored status is unchanged
        assert (page.get("verdict"), verdicts.word(page["status"], page.get("verdict"))) == (verdict, stamp), k + ": page"
        r = run_page[s]
        assert (r.get("verdict"), verdicts.word(r["status"], r.get("verdict"))) == (verdict, stamp), k + ": session row"
        snap = snaps[s]
        assert (snap.get("verdict"), verdicts.word(snap["status"], snap.get("verdict"))) == (verdict, stamp), k + ": Community"
        assert stamps[s] == stamp, k + ": chat"
        # Its validation figures are tinted as its stamp reads: none passes a validation that didn't pass.
        tints = ((page.get("experiment") or {}).get("gate_tints") or {}).get("validation") or {}
        if verdict in VALIDATION_MISS:
            assert tints.get("p_value") == "fail", (k, tints)
        elif STATES[k][1] and STATES[k][1][0] == SUP:
            assert set(tints.values()) == {"pass"}, (k, tints)
    # Passed counts only what the rows stamp Passed; the stored passes they say weren't cleared count as not cleared.
    passed = [s for s, (v, w) in want.items() if w == "Passed"]
    not_cleared = [s for s, (v, w) in want.items() if v]
    assert (counts["passed"], counts["not_cleared"]) == (len(passed), len(not_cleared)) == (4, 6)

    # Home's new-results strip: what passed, and the stored passes left uncleared, each with its verdict.
    since = dashboard._passed_since(cur, uid, repo.now_utc() - timedelta(hours=1))
    stored = [s for k, s in sid.items() if STATES[k][0] == "passed"]
    assert since["n"] == len(passed) and {i["id"] for i in since["items"]} <= set(passed)  # it names the newest 3
    assert since["not_cleared_n"] == len([s for s in stored if want[s][0]]) == 5
    assert len(since["not_cleared"]) == 3 and all(i["verdict"] == want[i["id"]][0] for i in since["not_cleared"])

    # The chat's context: a stored pass not on paper whose stamp isn't Passed says its stamp and that it isn't
    # passed; a dead end shows the stamp its page shows, never the raw key.
    brief = agent._copilot_context(cur, repo.get_user(cur, uid))
    lines = brief.splitlines()
    for k, s in sid.items():
        st, _, paper, verdict, stamp = STATES[k]
        title = '"State %d"' % list(sid).index(k)
        if st == "passed" and not paper:  # under "Passed strategies not deployed"
            line = next(ln for ln in lines if ln.startswith("- strategy_id=%d %s" % (s, title)))
            assert (('stamp it "%s", not Passed' % stamp) in line) == (stamp != "Passed"), (k, line)
        elif st in ("failed", "rejected"):  # under the dead ends
            line = next(ln for ln in lines if ln.startswith("- %s (" % title))
            assert line.endswith("— " + stamp), (k, line)
    assert "— failed" not in brief and "— rejected" not in brief
