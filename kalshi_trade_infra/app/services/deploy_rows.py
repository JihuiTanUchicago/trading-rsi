"""A deployment row's "why" as Deploy, its strategy page and the autopilot panel say it: the demo-copy pause, a
paused or stopped row's reason in plain words, and what Resume would do.

Moved verbatim from app.web.views (which re-imports every name) so the chat's context (research.agent) says a row's
why in the row's own words: research may not reach the web layer. Display only: the engine, the demo-copy sync and
the stored reasons are unchanged."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from app import repository as repo
from app.services import customer_text

# The engine pauses a demo deployment with this in its reason when the user has no demo key.
_DEMO_KEY_PAUSE = "demo (sandbox) key"


def _demo_key_back(cur, uid: int, deps: List[Dict], demo_key: Optional[bool] = None) -> bool:
    """True when a demo deployment is paused for a missing demo key and the key is connected now. The
    engine leaves it paused once the key is added, and its row still said to add the key, beside a
    Funds page saying it is connected. Reads whether the key is saved (never its id or encrypted bytes) only
    when such a deployment is listed, and only when the request doesn't know (`demo_key`)."""
    if not any(d["mode"] == "demo" and d["status"] == "paused" and _DEMO_KEY_PAUSE in (d.get("status_reason") or "")
               for d in deps):
        return False
    return repo.has_kalshi_dev_key(cur, uid) if demo_key is None else demo_key


def _paper_books(deps: List[Dict]) -> Dict[int, str]:
    """{strategy_id: "running" | "paused"} for each strategy with a paper deployment that isn't stopped
    (running when any of them runs): what a demo copy the demo-copy sync paused is waiting on."""
    out: Dict[int, str] = {}
    for d in deps:
        if d["mode"] == "paper" and d["status"] in ("running", "paused"):
            out[d["strategy_id"]] = "running" if d["status"] == "running" or out.get(d["strategy_id"]) == "running" else "paused"
    return out


def _demo_copy_pause(d: Dict, paper_books: Optional[Dict[int, str]]) -> Optional[Tuple[str, Optional[str]]]:
    """(why, resume_block) for a demo deployment the demo-copy sync paused (app.trading.demo_twins), or
    None for any other row. The sync stores PAUSE_REASON ("... so the demo copy stopped too") and matches
    those exact words to resume the copy once its paper deployment runs again, so the stored text stays
    and its row says it here: paused, not stopped, and what it waits on. While the sync is on and the
    paper deployment isn't running, Resume would only be paused again at the sync's next pass, so
    `resume_block` says what to do first and the page offers Resume disabled. `paper_books` is
    _paper_books of the deployments the page read (None: unknown, so nothing is claimed about paper).
    Display only: the sync, the resume route and the stored reason are unchanged."""
    from app.trading import demo_twins

    if d["mode"] != "demo" or d["status"] != "paused" or (d.get("status_reason") or "").strip() != demo_twins.PAUSE_REASON:
        return None
    paper = None if paper_books is None else paper_books.get(d["strategy_id"], "none")
    if paper is None or not demo_twins.DEMO_TWINS:
        return "Its paper deployment wasn’t running, so this demo copy paused with it", None
    if paper == "running":
        return "Its paper deployment wasn’t running when this paused. It is now, so you can resume it", None
    if paper == "paused":
        return ("Waiting on its paper deployment, which is paused. Resume that one and this copy resumes with it",
                "Resume its paper deployment first: this demo copy resumes with it.")
    return ("Waiting to run on paper again: a demo copy runs only while its paper deployment does",
            "Deploy it on paper first: this demo copy resumes with its paper deployment.")


def row_status_why(d: Dict, demo_key_back: bool = False, paper_books: Optional[Dict[int, str]] = None) -> Optional[str]:
    """A deployment row's "why" as Deploy and its strategy page show it (_demo_copy_pause, else _status_why), or None
    while it runs. The chat's context reads it too (agent._copilot_context), so "why did it pause?" is answered in the
    row's own words. Display only."""
    copy_pause = _demo_copy_pause(d, paper_books)
    return copy_pause[0] if copy_pause else _status_why(d, demo_key_back)


# The engine's loss guard pause (engine.loss_guard): "Lost $12.40, more than the $10.00 guard (...)".
_LOSS_GUARD = re.compile(r"^Lost \$[\d,]+\.\d\d, more than the \$([\d,]+\.\d\d) guard")


def _status_why(d: Dict, demo_key_back: bool = False) -> Optional[str]:
    """Why a deployment isn't running, in plain words, for its row and its strategy page. The stored
    reason read "By you", "Kill switch" or an engine line ("force-closed: position down 33.0% >= stop
    30.0%") after the stake, and "By you" didn't say whether you paused it or removed it. A reason
    that is already a sentence is kept as written, with what Resume does where the reason alone
    misleads: a demo key that is connected now (`demo_key_back`, from _demo_key_back), or a loss guard
    its lifetime realized P&L is still past (the guard sums every closed trade after each decision, so
    a resumed deployment pauses again at its next check). Display only."""
    raw = (d.get("status_reason") or "").strip()
    if d.get("status") == "running" or not raw:
        return None
    if raw == "By you" and d.get("status") == "stopped":
        return "You removed it"
    if d.get("status") == "paused" and demo_key_back and _DEMO_KEY_PAUSE in raw:
        return "Your Kalshi demo key wasn’t connected when it paused. It is now, so you can resume it"
    if raw not in ("By you", "Approved agent proposal", "Kill switch") and not re.search(r"% >= (?:stop|limit) ", raw):
        why = customer_text.clean(customer_text.tournament_status(raw),
                                  "Stopped automatically" if d.get("status") == "stopped" else "Paused automatically")
        guard = _LOSS_GUARD.match(raw)
        if (guard and d.get("status") == "paused" and d.get("realized_pnl") is not None
                and float(d["realized_pnl"]) < -float(guard.group(1).replace(",", ""))):
            why = why.rstrip(".") + ". Its loss is still past that guard, so Resume would pause it again at its next check"
        return why
    clause = _pause_clause(raw)
    return clause[:1].upper() + clause[1:]

def _plain_pause_reason(why: str) -> str:
    """The engine's pause reason in plain words, for the phone panel."""
    from app.trading import demo_twins

    why = (why or "").strip()
    if why == demo_twins.PAUSE_REASON:  # stored as "... the demo copy stopped too", but it is paused (_demo_copy_pause)
        return "its paper deployment wasn’t running, so its demo copy paused with it"
    m = re.search(r"position down ([\d.]+)% >= stop ([\d.]+)%", why)
    if m:
        return "its safety stop sold a position that had lost %.0f%% of its cost" % float(m.group(1))
    m = re.search(r"drawdown ([\d.]+)% >= limit ([\d.]+)%", why)
    if m:
        return "it lost %.0f%% of its money, past its %.0f%% limit" % (float(m.group(1)), float(m.group(2)))
    if why.lower().startswith("lost $"):
        return "it hit its loss guard"
    return (customer_text.curly(customer_text.clean(why, "")) or "").rstrip(".")[:120] or "it was paused"


def _pause_clause(raw: Optional[str]) -> str:
    """Why a deployment is paused, as the end of a sentence."""
    raw = (raw or "").strip()
    return {"By you": "you paused it", "Approved agent proposal": "you approved the agent’s proposal to pause it",
            "Kill switch": "you pressed Kill"}.get(raw) or _plain_pause_reason(raw)
