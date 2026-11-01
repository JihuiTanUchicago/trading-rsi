"""The reads the autopilot panel's two merged reads replaced, put back together.

repo.autopilot_pulse and repo.credit_week answer in one statement each what the panel used to read one
statement at a time. Built here from those single reads (looked up on `repo` at call time, so a test's
stubs of them apply), they are:
  - the reference the real-MySQL test holds the merged reads to (tests/test_autopilot_status_db.py);
  - the stand-ins the unit tests patch in, so a test that stubs one single read (a review in flight,
    the week's research, the balance) still drives the panel through it.
"""
from datetime import datetime

from app import repository as repo

EPOCH = datetime(1970, 1, 1)  # research_week from here: every research session the user ever started


class _Parts(dict):
    """A pulse whose every part is read on first use, so a test stubs only the reads its code reaches."""

    def __init__(self, reads):
        super().__init__()
        self._reads = reads

    def __missing__(self, key):
        val = self[key] = self._reads[key]()
        return val


def _tokens_by_model(rows):
    """repo.research_token_rows summed per model, in model order (the pulse's research_24h)."""
    by = {}
    for r in rows:
        t = by.setdefault(r.get("model"), [0, 0])
        t[0] += int(r.get("input_tokens") or 0)
        t[1] += int(r.get("output_tokens") or 0)
    return sorted(({"model": m, "input_tokens": i, "output_tokens": o} for m, (i, o) in by.items()),
                  key=lambda x: str(x["model"] or ""))


def pulse_from_parts(cur, uid, since, review=False, engine=False, full=False, day_since=None):
    """repo.autopilot_pulse from the single reads. `full` adds the chat's version and the deployments'
    strategies (two more reads on a real cursor); without it they are None, which the panel reads as
    unknown: it reads the last move itself and lets the book memo read the deployments."""
    week = []

    def research_week():
        if not week:
            week.append(repo.research_week(cur, uid, since))
        return week[0]

    def chat():
        cur.execute("SELECT COUNT(*) AS n, COALESCE(MAX(id),0) AS top FROM app_chat_messages WHERE user_id=%s", (uid,))
        row = cur.fetchone()
        return int(row["n"]), int(row["top"])

    return _Parts({
        "positions": lambda: repo.positions_version(cur, uid),
        "dep_strategies": lambda: repo.deployment_strategies(cur, uid) if full else None,
        "kalshi_key": lambda: repo.get_kalshi_key(cur, uid) is not None,
        "researched": lambda: sum(int(r.get("runs") or 0) for r in repo.research_week(cur, uid, EPOCH)) > 0,
        "chat": lambda: chat() if full else None,
        "last_review_at": lambda: repo.last_review_at(cur, uid),
        "runs_7d": lambda: sum(int(r.get("runs") or 0) for r in research_week()),
        "tested_7d": lambda: sum(int(r.get("tried") or 0) for r in research_week()),
        "passed_7d": lambda: repo.strategies_passed_since(cur, uid, since),
        "research_live": lambda: [{k: r.get(k) for k in ("id", "market", "status", "created_at", "started_at", "heartbeat_at")}
                                  for r in repo.list_runs(cur, uid, limit=50) if r.get("status") in ("queued", "running")],
        "review": lambda: repo.active_agent_run(cur, uid, "review") if review else None,
        "engine_age": lambda: repo.engine_last_tick_age_seconds(cur) if engine else None,
        "research_24h": lambda: _tokens_by_model(repo.research_token_rows(cur, uid, day_since)) if day_since is not None else None,
    })


def pulse_reference(cur, uid, since, review=False, engine=False, day_since=None):
    """Every part of pulse_from_parts(full=True), read now (the real-MySQL reference)."""
    p = pulse_from_parts(cur, uid, since, review, engine, full=True, day_since=day_since)
    return {k: p[k] for k in p._reads}


def credit_week_from_parts(cur, uid, since, balance=False):
    """repo.credit_week from the single reads, each read on first use."""
    return _Parts({"research": lambda: repo.research_spent_since(cur, uid, since),
                   "by_source": lambda: repo.usage_by_source(cur, uid, since),
                   "spent": lambda: repo.credit_spent_total(cur, uid, since),
                   "balance": lambda: repo.credit_balance(cur, uid) if balance else None,
                   "card_charged": lambda: repo.card_charged(cur, uid) if balance else None})


def credit_reference(cur, uid, since, balance=False):
    """Every part of credit_week_from_parts, read now."""
    p = credit_week_from_parts(cur, uid, since, balance)
    return {k: p[k] for k in p._reads}


def stub_merged_reads(monkeypatch):
    """Drive the panel through the single reads (each test stubs the ones it cares about)."""
    monkeypatch.setattr(repo, "autopilot_pulse", pulse_from_parts)
    monkeypatch.setattr(repo, "credit_week", credit_week_from_parts)
