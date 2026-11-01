"""The bell's line when a research session the customer started finishes.

A session runs for minutes or, by default, until it is stopped, and nothing told the customer it had
ended: they went back to Research to check. One notice says how it ended, how many of its ideas
passed, and opens the session.

Once per session. It is written where the run is marked finished, and only by the call whose UPDATE
took the run out of 'running' (agent_run_repository.finish_run returns True, and the reaper hands
back the runs it stopped); two workers racing to finish one run, or a finish run twice, get one
True between them. The insert itself adds nothing when the user already has this kind of notice
about that session (repository.add_notification_once), so a second say never makes a second line.

Left out:
    autopilot  sessions the autopilot started (brief runs). It starts them all day, and the bell already
               reports what they produce that the customer acts on: "Autopilot put … on paper" when one
               passes and goes to paper, then paused, passed and live-ready notices. A line per
               autopilot session would bury those.
    cancelled  a session the customer stopped knows it ended: they pressed Stop.

The words never carry the run's error text (it can be a provider's), an id or a model name: only how
it ended, in the Research page's own terms (services.run_stop), and its counts.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from app import repository as repo
from app.services.run_stop import stop_reason
from app.trading.markets import MARKETS

_LOG = logging.getLogger(__name__)

KIND = "research_done"  # app_notifications.kind is VARCHAR(16)

# How it ended, by services.run_stop's reason; {s} is the session ("Your research session on …").
ENDED = {
    "limit": "{s} finished.",
    "complete": "{s} finished: it ran out of new ideas to try.",
    "credits": "{s} stopped because your credits ran out.",
    "interrupted": "{s} was interrupted and stopped.",
    "no_data": "{s} stopped: that market has nothing to research yet.",
    "error": "{s} stopped on an error.",
}


def link(run_id: int) -> str:
    return "/research/%d" % int(run_id)


def _count(n: int) -> str:
    return "{:,}".format(n)


def passed_words(tried: int, passed: int) -> str:
    """How many of its ideas passed, in words."""
    tried = max(int(tried or 0), int(passed or 0))
    passed = int(passed or 0)
    if not tried:
        return "It didn’t test any ideas."
    if not passed:
        return "The one idea it tested didn’t pass." if tried == 1 else "None of the %s ideas it tested passed." % _count(tried)
    return "%s of %s idea%s passed." % (_count(passed), _count(tried), "" if tried == 1 else "s")


def body(run: Dict) -> Optional[str]:
    """The notice for a finished run row (finished_run's columns), or None when it gets none: not research,
    started by the autopilot, still at work, or cancelled. Its count is the row's, the one the Research list and
    the session page show (a run that ended on an error stores the ideas that passed before it)."""
    if run.get("kind") != "research" or run.get("by_autopilot"):
        return None
    why = stop_reason(run)
    if why not in ENDED:
        return None
    m = MARKETS.get(run.get("market") or "")
    subject = "Your research session on %s" % m.customer_label if m else "Your research session"
    return ENDED[why].format(s=subject) + " " + passed_words(int(run.get("hypotheses_tried") or 0),
                                                             int(run.get("strategies_passed") or 0))


def ring(cur, run_id: int) -> bool:
    """Write the notice for a run this caller just finished. True when a line was added. Never raises: the
    run is already recorded as finished, and a bell hiccup must not undo or fail that."""
    try:
        run = repo.finished_run(cur, run_id)
        text = body(run) if run else None
        if not text:
            return False
        return repo.add_notification_once(cur, run["user_id"], KIND, text, link(run_id)) is not None
    except Exception:  # best-effort, like the run's other after-finish steps
        _LOG.exception("research-finished notice failed for run %s", run_id)
        return False
