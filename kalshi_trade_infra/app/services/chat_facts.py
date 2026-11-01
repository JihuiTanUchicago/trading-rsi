"""Account facts the chat hands the model (research.agent._copilot_context), each as the page that shows it says it.

A chat reply about money or status is read as the pages are, so each figure here is the page's own: counted the
way it counts (strategies, not deployments), rounded the way it rounds (trading_core.domain.money.cents for trading
money, trading_core.domain.credits for credits), over the window it names. Audit 2026-11-01 (a heavy account with
paper, demo, a deleted strategy, research and a billing ledger): asked "what's running?" the context had "6 on paper"
beside Home's "7 strategies running" (demo left out), "how much did autopilot cost me?" had only a 3-day token
estimate beside Billing's week, "what is my balance?" had paper cash beside Home's paper portfolio, and "why did this
trade lose?" had no trade at all.

Trading money (paper, demo, live) and credits (real money paid to Tau for the AI's work) are said apart, each book on
its own line or clause, and never added. Every function is best-effort for the context: the caller drops a block
whose read fails. Display only: no gate, order, limit or billing figure is computed here.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable, Dict, Iterable, List, Optional

from app import repository as repo
from app.services import trade_why
from trading_core.domain.credits import credit_exact, credit_split, credit_usd
from trading_core.domain.money import cents, exact

BOOKS = ("paper", "demo", "live")
PERP_MARKETS = frozenset({"btc_perp"})  # research only: the engine places no order for one in any book (core.js isPerpetual)
DELETED_STRATEGY = "Deleted strategy"  # the dashboard's and Deploy's name for it (routes.strategies.DELETED_STRATEGY)
RECENT_TRADES = 20  # the dashboard lists the newest 12; a few more reach a trade from yesterday


def usd(v) -> str:
    """Trading money as the pages write it: money.cents, the sign before the dollar ("-$3.38")."""
    c = cents(v)
    return ("-$%.2f" % -c) if c < 0 else "$%.2f" % c


def _credits(v: float) -> str:
    return "$%.2f" % v


def strategy_count(deps: Iterable[Dict]) -> int:
    """How many strategies these deployments are (core.js stratCount, chat._strat_count): one on paper with a demo
    copy is one. A row with no strategy id counts as its own."""
    return len({("s", d["strategy_id"]) if d.get("strategy_id") is not None else ("row", i) for i, d in enumerate(deps)})


def running_lines(deps: List[Dict], live_open: bool) -> List[str]:
    """What is running, as Home's header and the autopilot card count it. `deps` is every deployment that isn't
    stopped (repo.list_deployments). Home's "N strategies running" counts strategies placing orders across the books
    (a live one while live trading is closed places none, and a Bitcoin perpetual one is research only); the card
    says each book's strategies apart. Paused ones are counted apart (Home's "1 paused, needs you")."""
    def trading(d):
        return (d.get("status") == "running" and d.get("market") not in PERP_MARKETS
                and (d.get("mode") != "live" or live_open))
    on = [d for d in deps if trading(d)]
    per = {m: strategy_count([d for d in deps if d.get("mode") == m and d.get("status") == "running"
                              and d.get("market") not in PERP_MARKETS]) for m in BOOKS}
    paused = [d for d in deps if d.get("status") == "paused"]
    n = strategy_count(on)
    line = ("STRATEGIES RUNNING (Home's and the autopilot card's count: strategies, not deployments; one on paper with a "
            "demo copy is ONE strategy): %d running across the books; by book: %d on paper, %d on demo, %d live"
            % (n, per["paper"], per["demo"], per["live"]))
    if per["live"] and not live_open:
        line += " (the live ones wait: live trading isn’t open yet, so they place no orders and aren’t in the %d)" % n
    line += ". Paused: %s." % ("none" if not paused else "%d (%s)" % (len(paused), ", ".join(
        "“%s” on %s" % (d.get("title") or "Untitled strategy", d.get("mode")) for d in paused[:5])))
    line += " Say these counts as they are; never add a book's deployments to another's to make one figure."
    return [line]


def research_now(counts: Dict[str, int]) -> str:
    """Research sessions as the Research page and Home count them (repo.active_run_counts, silent ones left out):
    running, queued to start and paused, apart. "RESEARCH RUNNING RIGHT NOW: 2" counted a session still queued for a
    worker beside Home's "1 researching · 1 queued"."""
    run, queued, paused = (int(counts.get(k) or 0) for k in ("research_running", "research_queued", "research_paused"))
    return "%d running, %d queued to start, %d paused (the Research page's count)" % (run, queued, paused)


def credits_lines(cur, uid: int, cap_usd: float = 0.0, cap_spent: Optional[float] = None) -> List[str]:
    """Credits as Billing shows them: the last 24 hours' spend, the last 7 days' spend and its split by what spent it
    (credit_split, so the parts add up to the week as shown, and a part under half a cent says "under $0.01"), and the
    autopilot's research cap with what the cap counts against it (`cap_spent`: research_spend, research sessions
    started in the last 24 hours priced as the cap prices them, the autopilot card's figure). Credits are real money
    paid to Tau, never trading P&L."""
    now = repo.now_utc()
    spent_7d = repo.credit_spent_total(cur, uid, now - timedelta(days=7))
    by = repo.usage_by_source(cur, uid, now - timedelta(days=7))
    split = credit_split(by, spent_7d)
    spent_24h = credit_usd(repo.credit_spent_total(cur, uid, now - timedelta(days=1)))

    def part(k):
        return "under $0.01" if credit_exact(by.get(k) or 0) > 0 and not split.get(k) else _credits(split.get(k) or 0.0)
    out = ["Credits spent (Billing's figures; credits are real money the user paid Tau for the AI's work, NOT trading "
           "P&L: never add them to, or take them from, any book's P&L): last 24 hours %s; last 7 days %s, of which "
           "your research sessions (the user's own) %s, autopilot research %s, autopilot check-ins %s, chat %s, other agent "
           "calls %s (Billing's split). "
           "What the autopilot cost is its research plus its check-ins. Billing shows how long the credits left last "
           "at this pace; don't work that out yourself."
           % (_credits(spent_24h), _credits(credit_usd(spent_7d)), part("research"), part("autopilot_research"),
              part("autopilot_review"), part("chat"), part("other"))]
    if cap_usd > 0 and cap_spent is not None:
        reached = cap_spent >= cap_usd
        out.append("Autopilot research cap (the autopilot card's): $%.2f per 24 hours. Research sessions started in the "
                   "last 24 hours, the user's own included, cost $%.2f by the cap's count (priced from their AI usage, "
                   "so not the same figure as the 24-hour credits spent above), so %s. Only the user changes the cap; "
                   "it never limits research the user starts."
                   % (cap_usd, cents(cap_spent), "it has REACHED the cap and starts no new research until enough of "
                      "them are over 24 hours old" if reached else "it can still start research"))
    return out


def paper_portfolio_line(cash: float, open_notional) -> str:
    """The paper book's headline as Home and Funds show it: cash plus the open positions at cost, summed exactly and
    rounded once (views._paper_equity). The context said only "paper cash $2000.00" beside Home's $2,030.08."""
    return ("Paper portfolio %s (Home's and Funds' headline: available cash %s plus open positions at cost %s; "
            "practice money, not real)." % (usd(exact(cash) + exact(open_notional)), usd(cash), usd(open_notional)))


def orphan_lines(open_positions: List[Dict]) -> List[str]:
    """Open positions whose strategy was deleted (repo.user_positions: title None), per book: Home and Deploy list them
    as "Deleted strategy", and nothing sells or settles them. The Deployments list leaves them out, so "8 open" in a
    book read one more than its deployments hold, with nothing to say why."""
    n = {m: sum(1 for p in open_positions if p.get("title") is None and p.get("mode") == m) for m in BOOKS}
    if not any(n.values()):
        return []
    return ["Open positions of a DELETED strategy (Home and Deploy list each as “Deleted strategy”; they are in the "
            "open counts above but not in the Deployments list): %s. Tau no longer sells or settles them; they stay "
            "open until their markets settle." % "; ".join("%s %d" % (m, k) for m, k in n.items() if k)]


def _when(at) -> str:
    return at.strftime("%b %-d %H:%M UTC") if isinstance(at, datetime) else ""


def recent_trades_lines(closed: List[Dict], deps: List[Dict], market_name: Callable[[Optional[str]], str]) -> List[str]:
    """The dashboard's recent closed trades (repo.user_positions closed, newest settled first), each with its book,
    P&L as the page rounds it and the page's own reason line (services.trade_why: why it entered and why it exited,
    a cancelled market's fair-price payout included), so "why did this trade lose?" is answered from the page's
    words. A deleted strategy's trade is named as the pages name it. Times are UTC, as stored."""
    if not closed:
        return []
    by_dep = {d["id"]: d for d in deps}
    out = ["Recent closed trades, newest first (the dashboard lists the newest 12, Deploy and each strategy's page "
           "all of them; each P&L is that one trade's, after fees, in its own book; never add books together):"]
    for p in closed[:RECENT_TRADES]:
        d = by_dep.get(p.get("deployment_id")) or {}
        title = p.get("title")
        name = DELETED_STRATEGY if title is None else (str(title).strip() or "Untitled strategy")
        why = trade_why.trade_why(p, d.get("spec")) or ""
        where = market_name(d.get("market")) if d.get("market") else ""
        out.append("- %s · %s · “%s”%s · %s %s · %s%s" % (
            _when(p.get("closed_at")), p.get("mode"), name, (" · " + where) if where else "",
            str(p.get("side") or "").upper(), int(p.get("qty") or 0), usd(p.get("pnl")), (" · " + why) if why else ""))
    return out


def open_notional(row: Optional[Dict]) -> Decimal:
    return exact((row or {}).get("notional") or 0)
