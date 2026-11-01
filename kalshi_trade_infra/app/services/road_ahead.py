"""What is left before live money for a paper strategy still in its paper forward test, as one figure every page reads.

A probation strategy (its experiment's validation not supported) can be cleared to trade only by its one judgment on its
own paper trades (gatekeeper.judge_paper_forward), made once its realistic record holds the experiment's min_sample trips
from gatekeeper.PAPER_MIN_EVENTS separate events. Only after a pass does the live check (agent.paper_evidence: 30
trades, 20 events, 48h, P&L, edge, demo orders) count. A regression walk (2026-11-01) found one such strategy reading
"Next rung ... 8 of 40" on its page, "8 of 30 ... about 25 days" in its Road to live a few lines lower, and "about 36
days" on Deploy: two gates and two paces, each unexplained. Deploy's row, the strategy page's ladder and Road to live,
the autopilot panel's "Furthest on paper" and the chat's facts now all read `forward_figure`, and say it in this order:
first its paper forward test (its N of M and when at its pace), then, if it passes, the live check.

Display only: the forward gate, the gatekeeper and the live gate decide alone, from their own reads; this only counts
toward them. The pace is rsi.paper_trade_rate's (the oldest running paper deployment's, as Deploy's live estimate)."""
from datetime import datetime
from typing import Dict, List, Optional

PAPER_MIN_EVENTS = 20  # gatekeeper.PAPER_MIN_EVENTS (the live gate's agent.MIN_PAPER_EVENTS too)
LIVE_MIN_TRADES = 30   # agent.MIN_REALISTIC_PAPER_TRADES


def days_words(days: float) -> str:
    """An estimate in days, as every page says it (roadDays in core.js): halves round up, as Math.round does."""
    return "under a day" if days < 1 else "about a day" if days < 1.5 else "about %d days" % int(days + 0.5)


def _oldest_running(deps: List[Dict], strategy_id: int) -> Optional[Dict]:
    running = [d for d in deps or [] if int(d["strategy_id"]) == int(strategy_id) and d.get("mode") == "paper"
               and d.get("status") == "running" and isinstance(d.get("created_at"), datetime)]
    return min(running, key=lambda d: d["created_at"]) if running else None


def live_eta(deps: List[Dict], strategy_id: int, trades: int, events: int, span_hours: Optional[float] = None) -> Optional[float]:
    """Days until its record could hold the live check's trades, events and hours (rsi.paper_eta_days, from its oldest
    running paper deployment, as Deploy's road line counts it). None while none runs, or with no pace."""
    from app.research import rsi
    dep = _oldest_running(deps, strategy_id)
    return rsi.paper_eta_days(dep, trades, events=events, span_hours=span_hours) if dep else None


def forward_eta(deps: List[Dict], strategy_id: int, trades: int, events: int, sample: int, now=None) -> Optional[float]:
    """Days until its record could hold the `sample` trips from PAPER_MIN_EVENTS separate events its one judgment counts,
    at the pace it trades (rsi.paper_trade_rate). None while no paper deployment of it runs, or with no pace."""
    from app.research import rsi
    dep = _oldest_running(deps, strategy_id)
    rate = rsi.paper_trade_rate(dep, trades, now) if dep else None
    if rate is None:
        return None
    days = max(0, int(sample) - int(trades)) / rate["per_day"]
    if trades >= 3 and rate["hours"] > 0 and events:
        days = max(days, max(0, PAPER_MIN_EVENTS - int(events)) / (events / rate["hours"] * 24.0))
    return round(days, 1)


def forward_figure(deps: List[Dict], strategy_id: int, trades: int, events: int, sample: int,
                   live_eta_days: Optional[float] = None, now=None) -> Dict:
    """The one figure for a strategy in its paper forward test (sample = its experiment's min_sample, the deployment
    reads' forward_sample): its realistic `trades` and separate `events` against what the judgment needs, whether its
    record is growing (`running`), when it could be in (`forward_eta_days`), and, when its min_sample is under the live
    check's trade count, what the live check then still needs (`then_trades_needed`, with `then_eta_days`, the live
    counts' own estimate `live_eta_days`, when that is later)."""
    running = _oldest_running(deps, strategy_id) is not None
    eta = forward_eta(deps, strategy_id, trades, events, sample, now) if running else None
    then_n = LIVE_MIN_TRADES if int(sample) < LIVE_MIN_TRADES else None
    then_eta = live_eta_days if then_n and live_eta_days is not None and (eta is None or live_eta_days > eta) else None
    return {"trades": int(trades), "events": int(events), "running": running, "forward_sample": int(sample),
            "forward_events_needed": PAPER_MIN_EVENTS, "forward_eta_days": eta,
            "then_trades_needed": then_n, "then_eta_days": then_eta}


def _plural(n: int, unit: str) -> str:
    return "%s %s" % ("{:,}".format(n), unit if n == 1 else unit + "s")


def forward_words(f: Dict) -> str:
    """The figure in words, after "its paper forward test: " (forwardBody in core.js says the same words)."""
    need, ev_need = int(f["forward_sample"]), int(f.get("forward_events_needed") or PAPER_MIN_EVENTS)
    if f.get("then_trades_needed"):
        then = "if it passes, the live check then needs %s%s and looks at their P&L, edge and demo orders" % (
            _plural(int(f["then_trades_needed"]), "trade"),
            " (%s at its pace)" % days_words(float(f["then_eta_days"])) if f.get("then_eta_days") is not None else "")
    else:
        then = "if it passes, the live check then looks at the same record’s P&L, edge and demo orders"
    parts = ["%s of %s trades" % ("{:,}".format(h), "{:,}".format(n)) if u == "t" else
             "%s of %s separate events" % ("{:,}".format(h), "{:,}".format(n))
             for h, n, u in ((int(f["trades"]), need, "t"), (int(f["events"]) if f.get("events") is not None else ev_need, ev_need, "e"))
             if h < n]
    if not parts:
        return ("its %s trades from %s+ separate events are in, so its one judgment on them is due. Only a pass can "
                "clear it to trade; %s." % ("{:,}".format(need), ev_need, then))
    when = ("paused, so not growing" if not f.get("running") else
            "%s at its pace" % days_words(float(f["forward_eta_days"])) if f.get("forward_eta_days") is not None else "")
    return "%s. One judgment on them decides whether it can be cleared to trade; %s." % (
        " · ".join(parts + ([when] if when else [])), then)
