"""Billing routes: credits, cards, top-ups, auto-reload, receipts and the trades CSV export. Moved
verbatim from app.server, which re-imports every name for its route table."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from app import repository as repo
from app.adapters.llm import models_catalog as catalog
from app.adapters.stripe import billing
from app.services import customer_text
from app.services.trade_why import trade_why
from app.trading.markets import contract_customer_name, customer_name
from app.services import reader_day
from app.web.http import ApiError, Ctx, _num
from app.web.views import credit_cents, credit_exact, credit_split, credit_usd, session_title


def _stripe_failed(ctx: Ctx, e: "billing.StripeError", status: int, doing: str, fallback: str) -> ApiError:
    """A failed Stripe call, as the user reads it. Stripe's own text (which can name ids, parameters and
    hosts) goes to the server log; the user gets a declined card in plain words or ``fallback``."""
    print("[billing] %s failed for user %s: %s%s" % (doing, ctx.uid, e, " [%s/%s]" % (e.kind, e.code) if e.kind else ""),
          flush=True)
    return ApiError(status, billing.customer_message(e, fallback))


def _customer_id(ctx: Ctx) -> str:
    cust = repo.get_stripe_customer(ctx.cur, ctx.uid)
    existing = cust["stripe_customer_id"] if cust else None
    cid = billing.get_or_create_customer(ctx.user, existing)
    if cid != existing:  # new or re-created (stale id) -> persist, drop stale default card
        repo.save_stripe_customer(ctx.cur, ctx.uid, cid)
    return cid


RUNWAY_MIN_DAYS = 1.0  # an account younger than this has no spending pace to project from yet


def pace_days(created_at) -> float:
    """How many days the last-7-day spend covers: the account's age, at most 7 (7 when the age is
    unknown). A new account spent its whole "week" in its first minutes, and dividing that by 7 put
    "over 3 months at this week's pace" beside a first session that had only just begun."""
    if not isinstance(created_at, datetime):
        return 7.0
    return round(max(0.0, min(7.0, (repo.now_utc() - created_at).total_seconds() / 86400)), 2)


def runway_days(balance: float, spent_7d: float, days: float, floor: bool = False,
                stop_at: float = 0.0) -> Optional[float]:
    """Days until the balance falls to `stop_at` at the spend of the last `days` days (pace_days).
    The pages pass the research floor (pause_at), where research pauses, or the auto-reload trigger,
    where the card is next charged (reload_days): at $2.40 and $1.50 a day research pauses within a
    day, and "about 2 days, then research pauses" was a day late. None with no spend or nothing above
    `stop_at`, and for an account under RUNWAY_MIN_DAYS old, whose pace is not known yet; `floor`
    reads that account's spend as one day's instead (the low-credit warning: never later)."""
    balance = balance - stop_at
    if spent_7d <= 0 or balance <= 0:
        return None
    if days < RUNWAY_MIN_DAYS:
        if not floor:
            return None
        days = RUNWAY_MIN_DAYS
    return round(balance / (spent_7d / days), 1)


def pause_at() -> float:
    """The balance research pauses under (billing.MIN_RUN_BALANCE), which every runway counts down to.
    With payments off nothing gates research (billing.is_ready), so the runway is to $0 as before."""
    return float(billing.MIN_RUN_BALANCE) if billing.configured() else 0.0


def reload_trigger(cust: Optional[Dict]) -> Optional[Decimal]:
    """The balance auto-reload charges the card at, as billing.auto_reload reads it (the saved trigger,
    never under the minimum); None when auto-reload is off or there is no card to charge."""
    if not (cust and cust.get("auto_reload") and cust.get("default_pm_id")):
        return None
    return max(billing.AUTORELOAD_THRESHOLD, Decimal(str(cust.get("reload_threshold") or billing.AUTORELOAD_THRESHOLD)))


def reload_days(cust: Optional[Dict], balance: float, spent_7d: float, days: float) -> Optional[float]:
    """Days until auto-reload next charges the card, at the runway's pace: until the balance falls to
    its trigger. None when auto-reload is off or has no card, or the balance is at the trigger already
    (a reload is due, or failing, and the failure notice says which)."""
    trigger = reload_trigger(cust)
    return None if trigger is None else runway_days(balance, spent_7d, days, stop_at=float(trigger))


def _credit_runway(cur, uid: int, reads: Optional[billing.Reads] = None, balance: Optional[float] = None,
                   spent_7d: Optional[float] = None, created_at=None, floor: bool = False):
    """(balance, last-7-day usage spend, days until research pauses at that pace: the balance falls
    under pause_at()), so this is how long autopilot keeps working; None when there is no recent usage. A caller
    that already summed the ledger passes its `balance` and its week's usage (`spent_7d`). The
    pace is per day of the account's age (`created_at`) for an account under a week old."""
    if balance is None:
        balance = (reads or billing.Reads(cur, uid)).balance()
    if spent_7d is None:
        spent_7d = repo.credit_spent_total(cur, uid, repo.now_utc() - timedelta(days=7))
    return balance, spent_7d, runway_days(balance, spent_7d, pace_days(created_at), floor, pause_at())


CREDIT_RUNWAY_WARN_DAYS = 3


def _credits_running_low(cur, uid: int, reads: Optional[billing.Reads] = None, spent_7d: Optional[float] = None,
                         created_at=None):
    """Days of credit left when autopilot will pause within CREDIT_RUNWAY_WARN_DAYS and auto-reload
    won't top it up; None otherwise. A readout for the panel, never a gate. `spent_7d` and
    `created_at` as _credit_runway; a first-day account's spend counts as one day's, so the warning
    comes no later than at the old 7-day pace."""
    reads = reads or billing.Reads(cur, uid)
    cust = reads.customer()
    if cust and cust.get("auto_reload") and cust.get("default_pm_id"):
        return None
    runway = _credit_runway(cur, uid, reads, spent_7d=spent_7d, created_at=created_at, floor=True)[2]
    return runway if runway is not None and runway < CREDIT_RUNWAY_WARN_DAYS else None


def _reload_failed(cur, uid: int) -> Optional[Dict]:
    """The last auto-reload attempt, when it failed: {"at", "text"} from the bell notice
    billing.reload_failed_notice wrote (the credits it tried to add and the reason, as the user reads
    it), unless an auto-reload has succeeded since. A saved card clears the notice. None otherwise;
    a readout, so a failed read is None, never a broken page."""
    try:
        note = repo.latest_notification(cur, uid, billing.RELOAD_FAILED_KIND)
        if not note:
            return None
        ok_at = repo.last_credit_at(cur, uid, "autoreload")
        if ok_at is not None and ok_at > note["created_at"]:  # a tie keeps the failure: hiding one is worse
            return None
        # Stored straight ("couldn't"), as the email says it; the pages write an apostrophe curly.
        return {"at": note["created_at"], "notice": note["id"], "text": _reload_failed_words(note["body"])}
    except Exception as e:  # noqa: BLE001
        print("[billing] auto-reload failure read failed for user %s: %r" % (uid, e), flush=True)
        return None


def _reload_failed_words(body: str) -> str:
    """A failure notice's words as Billing shows them (billing.reload_failed_text, its apostrophes curly and its
    dollars grouped): the notice's own words, never written anew."""
    return customer_text.grouped_dollars(customer_text.curly(billing.reload_failed_text(body)))


def _reload_notice(cur, uid: int, notice_id: int, shown: Optional[Dict]) -> Optional[Dict]:
    """Billing opened from one failed auto-reload's bell notice (?notice=, bell.shown_link) that isn't the failure its
    banner shows (`shown`, _reload_failed): {at, text, ok_at, next_at}, when it failed and what it said, and when an
    auto-reload first went through after it or the next failure was told. Every one of those notices opened the
    newest failure's banner, so an older one ("couldn't add $25.00") landed on another's words ("$1,000.00").
    None for that same failure, or for a notice that isn't the user's failed auto-reload. A readout: a failed read is None."""
    if notice_id <= 0 or (shown and shown.get("notice") == notice_id):
        return None
    try:
        row = repo.reload_failure(cur, uid, notice_id, billing.RELOAD_FAILED_KIND)
    except Exception as e:  # noqa: BLE001
        print("[billing] auto-reload notice read failed for user %s: %r" % (uid, e), flush=True)
        return None
    if not row:
        return None
    return {"at": row["created_at"], "text": _reload_failed_words(row["body"]), "ok_at": row.get("ok_at"),
            "next_at": row.get("next_at")}


def reload_failed_now(cur, uid: int, reads: billing.Reads) -> Optional[str]:
    """The failed auto-reload's text (Billing's banner) when it can be why credits are short, else None:
    read only while auto-reload is on with a card and the balance is at or under its trigger (a reload
    above the trigger never fires, so it can't be failing), since /api/me and the autopilot poll read it.
    The autopilot card, the dashboard and the research form then name the card as the step."""
    trigger = reload_trigger(reads.customer())
    if trigger is None or Decimal(str(reads.balance())) > trigger:
        return None
    failed = _reload_failed(cur, uid)
    return failed["text"] if failed else None


def asks_stripe(ctx: Ctx) -> bool:
    """Whether billing_state would ask Stripe for the saved cards (an account with a Stripe customer,
    payments on): a read that waits on Stripe, up to its timeout."""
    return bool(billing.configured() and repo.get_stripe_customer(ctx.cur, ctx.uid))


def billing_state(ctx: Ctx):
    """Billing's page: billing_figures, plus the saved cards (asked of Stripe for an account with a
    Stripe customer) and the statement."""
    since = repo.now_utc() - timedelta(days=STATEMENT_DAYS)
    state, cust, totals, balance = _figures(ctx, since)
    cards: List[Dict] = []
    # A card list Stripe didn't answer is not "no card on file": the page said that (and "Add a card first."
    # under Buy credits) to a customer whose card was saved and charged by auto-reload. cards_unread tells the
    # page the list didn't load, so it says so and offers Try again.
    cards_unread = False
    if cust and billing.configured():
        try:
            cards = billing.list_cards(cust["stripe_customer_id"])
        except billing.StripeError:
            cards, cards_unread = [], True
    # Opened from a failed auto-reload's bell notice: that failure, when it isn't the one the banner shows. Only
    # then is the key sent, so a plain read keeps the figures poll's shape.
    if state["configured"] and cust:
        try:
            notice_id = int(str(ctx.query.get("notice") or "0"))
        except ValueError:
            notice_id = 0
        was = _reload_notice(ctx.cur, ctx.uid, notice_id, state["reload_failed"])
        if was:
            state["reload_notice"] = was
    return dict(state, cards=cards, cards_unread=cards_unread, **_statement(ctx.cur, ctx.uid, since, _statement_tz(ctx, since), totals, balance, ctx.user.get("created_at")))


def billing_figures(ctx: Ctx):
    """GET /api/billing/figures: Billing's figures (the balance, the spend and its pace, how long credits
    last, the next auto-reload and the saved auto-reload) for the open page's poll. The database only:
    never the Stripe card list, which waits on Stripe. With ?statement=1 (the page saw the balance move)
    the statement comes too, so the sum under it still ends at the balance the figures show."""
    since = repo.now_utc() - timedelta(days=STATEMENT_DAYS)
    state, _, totals, balance = _figures(ctx, since)
    if ctx.query.get("statement") == "1":
        state.update(_statement(ctx.cur, ctx.uid, since, _statement_tz(ctx, since), totals, balance, ctx.user.get("created_at")))
    return state


def _figures(ctx: Ctx, since):
    """(the page's figures, the Stripe customer row, and credit_totals since `since` and the balance they
    sum to, which the statement explains)."""
    cur = ctx.cur
    cust = repo.get_stripe_customer(cur, ctx.uid)
    default_pm, auto_reload, reload_failed = None, True, None
    reload_amount, reload_threshold = float(billing.AUTORELOAD_AMOUNT), float(billing.AUTORELOAD_THRESHOLD)
    if cust and billing.configured():
        reload_failed = _reload_failed(cur, ctx.uid)
        default_pm = cust.get("default_pm_id")
        auto_reload = bool(cust.get("auto_reload"))
        if cust.get("reload_amount") is not None:
            reload_amount = float(cust["reload_amount"])
        if cust.get("reload_threshold") is not None:
            reload_threshold = float(cust["reload_threshold"])
    # One grouped read gives the balance, the all-time spend and how the balance came to be (the
    # kinds add up to it, from the same snapshot, so the page's sum always closes).
    totals = repo.credit_totals(cur, ctx.uid, since)
    balance = float(sum((Decimal(str(t["all"])) for t in totals.values()), Decimal(0)))
    created_at = ctx.user.get("created_at")
    balance, spent_7d, runway = _credit_runway(cur, ctx.uid, balance=balance, created_at=created_at)
    # When auto-reload next charges the card at that pace; none while its last attempt stands failed.
    by_7d = repo.usage_by_source(cur, ctx.uid, repo.now_utc() - timedelta(days=7))
    split_7d = credit_split(by_7d, spent_7d)
    reload_in = None if reload_failed or not billing.configured() else reload_days(cust, balance, spent_7d,
                                                                                  pace_days(created_at))
    return {
        "configured": billing.configured(),
        "publishable_key": billing.STRIPE_PUBLISHABLE_KEY,
        "balance": credit_exact(balance),
        "spent": credit_usd(-(totals.get("usage") or {}).get("all", 0.0)),
        "spent_7d": credit_usd(spent_7d),
        # Days until research pauses (the balance under min_run_balance) at the week's pace, and until
        # auto-reload next charges the card (None while it is off, failing or due).
        "runway_days": runway,
        "reload_days": reload_in,
        # The days the week's spend covers (the account's age, at most 7): under 1 the page says it is
        # too soon to project, under 7 it states the pace per day so far instead of a week's.
        "pace_days": pace_days(created_at),
        "free_bonus": float(billing.FREE_BONUS),
        "topup_fee_pct": float(billing.TOPUP_FEE) * 100,
        "autoreload_threshold": reload_threshold,
        "autoreload_amount": reload_amount,
        # What one auto-reload charges the card (credits plus the top-up fee), so the card wall can
        # say it before the card is saved: auto-reload is on by default.
        "autoreload_charge": billing.topup_charge_cents(Decimal(str(reload_amount))) / 100,
        "min_reload_amount": float(billing.AUTORELOAD_AMOUNT),
        "min_reload_threshold": float(billing.AUTORELOAD_THRESHOLD),
        "min_topup": float(billing.MIN_TOPUP), "max_topup": float(billing.MAX_TOPUP),
        "default_pm": default_pm, "auto_reload": auto_reload,
        # The last auto-reload failed (what it tried to add and why, in the user's words): Billing
        # shows it by the auto-reload switch with a way to update the card. None when it didn't.
        "reload_failed": reload_failed,
        # Read-only spend pace: the last day, and the week split by what spent it, in cents that add up to
        # spent_7d as shown (credit_split). A part that spent something but shows $0.00 is named in
        # spent_7d_under_cent, so the page says "under $0.01" instead of dropping it. The research floor
        # tells the page when new research can't start.
        "spent_24h": credit_usd(repo.credit_spent_total(cur, ctx.uid, repo.now_utc() - timedelta(days=1))),
        "spent_7d_by": split_7d,
        "spent_7d_under_cent": [k for k, v in by_7d.items() if credit_exact(v) > 0 and not split_7d.get(k)],
        "min_run_balance": float(billing.MIN_RUN_BALANCE),
        # Whether the card was ever charged for credits (a top-up or an auto-reload row in the same grouped
        # read): until then the balance is free credits, and the page says so under it.
        "card_charged": any((totals.get(k) or {}).get("n", 0) > 0 for k in _CARD_KINDS),
    }, cust, totals, balance


STATEMENT_DAYS = 30     # Billing lists the last 30 days; the receipt has every row
STATEMENT_LINES = 300   # at most this many lines; a day the cap cuts short is left out whole
_CARD_KINDS = ("topup", "autoreload")  # credits bought with the card, each with a Stripe reference


def _tz_minutes(ctx: Ctx) -> int:
    """The viewer's offset from UTC in minutes, as the browser's getTimezoneOffset() gives it (240
    for UTC-4), so the statement's days are the viewer's days. 0 (UTC) when absent or not sane."""
    try:
        tz = int(str(ctx.query.get("tz") or "0"))
    except ValueError:
        return 0
    return tz if -840 <= tz <= 840 else 0


TZ_NAME_COOKIE = reader_day.TZ_NAME_COOKIE  # the browser's time zone name (Intl), URL-encoded, as boot.js writes it


def _statement_tz(ctx: Ctx, since: datetime) -> Union[int, List[Tuple[Optional[datetime], int]]]:
    """The viewer's day for each statement row: the browser's offset now (?tz=) when its offset held over the whole
    window, else [(from, minutes)] — the offset in force from each naive UTC instant on, oldest first, the first
    from None — read off the time zone the browser names (the tau_tzn cookie), when that zone is at ?tz= now. With one
    offset for every row, the days before a clock change were cut an hour off the viewer's midnight: a session's
    calls on two of the viewer's days (Oct 31 evening and 00:04 CDT Nov 1) were one line, headed by its last call's
    day, so "Today" summed yesterday's calls (walk 2026-11-01). Display only: amounts are the ledger's own. The
    zone and its offsets are app.services.reader_day's, as every page that counts the reader's day reads them."""
    tz = _tz_minutes(ctx)
    zone = reader_day.named_zone(reader_day._cookies(ctx).get(TZ_NAME_COOKIE), tz)
    if zone is None:
        return tz
    segs = reader_day.offsets(zone, since)
    return segs if isinstance(segs, list) else tz


_cents = credit_cents  # every credits figure rounds one way (app.web.views.credit_cents)


def _statement(cur, uid: int, since, tz, totals: Dict, balance: float, created_at=None) -> Dict:
    """Billing's history and the sum that explains the balance. `ledger`: the last STATEMENT_DAYS
    as lines, newest first. A usage line is one session's model calls on one of the viewer's days (`tz`, as
    _statement_tz gives it) with one model (`calls`, `first_at`, and `session` when it billed a research session the user
    can open); a card purchase says what the card was charged and its Stripe reference.
    `ledger_cut`: the line cap left older days out. `math`: the balance STATEMENT_DAYS ago, plus
    credits bought by card, plus other credits, minus agent usage, is the balance now, in cents that
    add up (the starting balance takes the rounding). Only what was charged or debited; never a rate.
    `joined`: the account (made at `created_at`) is younger than the statement, so the sum starts when it
    joined: a new subscriber read "Balance 30 days ago $0.00" for an account a day old."""
    rows = repo.credit_statement(cur, uid, since, tz, STATEMENT_LINES)
    cut = len(rows) >= STATEMENT_LINES
    if cut:  # the oldest day listed may be missing lines: drop it, so every day shown is whole
        oldest = rows[-1]["day"]
        rows = [r for r in rows if r["day"] != oldest]
    lines = []
    for r in rows:
        line = dict({"kind": r["kind"], "amount": float(r["amount"]), "memo": _ledger_memo(r),
                     "created_at": r["last_at"], "first_at": r["first_at"], "day": str(r["day"])}, **_usage_item(r))
        if r["kind"] == "usage":
            line["calls"] = int(r["n"])
            if r.get("run_kind") == "research" and r.get("run_id"):
                line["session"] = {"id": int(r["run_id"]), "prompt": (r.get("run_prompt") or "")[:120] or None,
                                   "title": session_title(r.get("run_prompt"), r.get("run_market")),
                                   "market": r.get("run_market"), "direction": r.get("run_direction")}
        elif r["kind"] in _CARD_KINDS:
            line["card_charge"] = billing.topup_charge_cents(Decimal(str(r["amount"]))) / 100
            line["stripe_ref"] = r.get("stripe_ref")
        lines.append(line)

    def since_usd(kinds) -> Decimal:
        return sum((Decimal(str(v["since"])) for k, v in totals.items() if kinds(k)), Decimal(0))

    bought = _cents(since_usd(lambda k: k in _CARD_KINDS))
    usage = _cents(-since_usd(lambda k: k == "usage"))
    other = _cents(since_usd(lambda k: k != "usage" and k not in _CARD_KINDS))
    end = _cents(balance)
    purchases = sum(int(v["n_since"]) for k, v in totals.items() if k in _CARD_KINDS)
    start = end - bought - other + usage
    # Nothing can come before an account's first day; the start may still carry a cent of the parts' rounding.
    joined = isinstance(created_at, datetime) and created_at >= since and abs(start) <= billing.CENT
    return {"ledger": lines, "ledger_cut": cut, "math": {
        "days": STATEMENT_DAYS, "start": float(start), "bought": float(bought), "joined": joined,
        "purchases": purchases, "other": float(other), "usage": float(usage), "end": float(end)}}


_USAGE_MEMO = "Agent usage · "  # billing.charge_usage's memo: this, then the invokable modelId
_RUN_ITEM = {("research", 0): "Research session", ("research", 1): "Autopilot research",
             ("review", 0): "Autopilot check-in", ("review", 1): "Autopilot check-in", ("chat", 0): "Chat"}


def _usage_item(r: Dict) -> Dict[str, str]:
    """What a usage line was for, in customer words: {"item": "Autopilot research", "model":
    "Claude Opus 4.6"}. The memo names the invokable modelId, which is ours, not the customer's, so
    it never reaches the page or the receipt as is. {} for a row that is not usage."""
    if r.get("kind") != "usage":
        return {}
    memo = r.get("memo") or ""
    model = memo[len(_USAGE_MEMO):] if memo.startswith(_USAGE_MEMO) else ""
    item = _RUN_ITEM.get((r.get("run_kind"), int(r.get("run_brief") or 0)), "Agent usage")
    return {"item": item, "model": catalog.label_for_model_id(model) if model else ""}


# An admin grant's stored memo names the staff member who made it ("Admin grant by <their email>");
# the admin tool asks for no reason meant for the customer, so the customer reads this instead. The
# row keeps its memo: it is the record of who granted it.
ADMIN_GRANT_MEMO = "Credits added by Tau support"
ADMIN_DEBIT_MEMO = "Credits adjusted by Tau support"


def _ledger_memo(r: Dict) -> str:
    """A ledger row's memo as the customer reads it (Billing and the receipt): a usage line says what
    it was for and names the model, never its id; an admin grant says Tau support added (or took
    back) credits, never who; any other memo is as written, less anything internal
    (services.customer_text)."""
    use = _usage_item(r)
    if use.get("model"):
        return "%s · %s" % (use["item"], use["model"])
    if use:
        # A usage row whose memo isn't charge_usage's own (an older or hand-written one) said whatever it held in the
        # receipt's Memo column: a job name, an experiment id ("exp-4471 · job:hypo_gen_v2", walk 2026-10-31). It
        # says what the line was for, and the model's family when the memo names a model by its id.
        found = customer_text.MODEL_ID.search(r.get("memo") or "")
        family = catalog.label_for_model_id(found.group(0)) if found else ""
        return "%s · %s" % (use["item"], family) if family and family != "AI model" else use["item"]
    if r.get("kind") == "admin_grant":
        amount = r.get("amount", r.get("amount_usd"))
        return ADMIN_DEBIT_MEMO if amount is not None and Decimal(str(amount)) < 0 else ADMIN_GRANT_MEMO
    return customer_text.clean(r.get("memo") or "") or ""


_PLAIN_NUMBER = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)$")


def _csv_safe(value: Any) -> str:
    """One CSV cell that a spreadsheet will show as text, never run as a formula. Strategy titles
    are written by people and models; a cell that starts with = + - @ (or a tab or CR) and isn't a
    plain number gets a leading apostrophe. Negative P&L stays a number."""
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in "=+-@\t\r" and not _PLAIN_NUMBER.match(s):
        return "'" + s
    return s


def _csv_rows(buf) -> Callable[[List[Any]], Any]:
    """A csv writer for a user download whose every cell goes through _csv_safe: the one guard both
    downloads (credits receipt, trades) share."""
    import csv
    w = csv.writer(buf)
    return lambda row: w.writerow([_csv_safe(c) for c in row])


def _usd4(v) -> str:
    """A ledger amount to 4 places, rounded half up from its exact decimal: through a float, -4.666650
    printed -4.6666."""
    return str(Decimal(str(v)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


# Session: the research session a usage row billed, by its number among the user's own ("Session 3", as
# the Research list names it; a chat or check-in debit says what it was in its memo). Card charged: what the card
# paid for credits bought (the credits plus the purchase fee). Stripe reference: that payment's id.
RECEIPT_HEADER = ["Date (UTC)", "Type", "Amount (USD)", "Memo", "Session", "Card charged (USD)", "Stripe reference"]
# The Type column in the words Billing's statement uses (the bundles' KIND_LABEL), never the ledger's own kind
# key: the receipt said "usage", "topup", "autoreload", "bonus" and "admin_grant".
RECEIPT_TYPE = {"bonus": "Welcome credits", "topup": "Added credits", "autoreload": "Auto-reload",
                "usage": "Agent usage", "admin_grant": "Credits from Tau support"}


def receipt_type(kind: Optional[str]) -> str:
    """A ledger kind as the receipt's Type column says it; a kind this list doesn't know, in plain words."""
    return RECEIPT_TYPE.get(kind or "") or (kind or "").replace("_", " ").strip().capitalize()


RECEIPT_MAX_ROWS = 100000  # the receipt's ledger lines, newest first: every row a real account has


def billing_receipt(ctx: Ctx):
    """Full Credits & Usage history as a downloadable CSV receipt. Memos can carry run and strategy
    names, so every cell is formula-safe. The account is the session's own user row (its email), and the balance
    and total spent are summed from the same read of the ledger as the lines (repo.credit_history), so they always
    agree with them: one statement where four read the user, the two totals and the lines."""
    import io
    cur = ctx.cur
    history = repo.credit_history(cur, ctx.uid, RECEIPT_MAX_ROWS)  # full history, newest first
    ledger = history["rows"]
    balance = history["balance"] if history["balance"] is not None else repo.credit_balance(cur, ctx.uid)
    spent = history["spent"] if history["spent"] is not None else repo.credit_spent_total(cur, ctx.uid)
    buf = io.StringIO()
    row = _csv_rows(buf)
    row(["Tau - Credits & Usage receipt"])
    row(["Account", (ctx.user or {}).get("email", "")])
    row(["Generated (UTC)", repo.now_utc().replace(microsecond=0).isoformat()])
    row(["Current balance (USD)", _usd4(balance)])
    row(["Total spent (USD)", _usd4(spent)])
    row([])
    row(RECEIPT_HEADER)
    # A research debit names its session as the Research list does ("Session 3", the user's own count),
    # never the global run id; a check-in or chat debit says what it was in its memo.
    seq = repo.research_session_numbers(cur, ctx.uid) if any(
        r["kind"] == "usage" and r.get("run_id") and r.get("run_kind") == "research" for r in ledger) else {}
    for r in ledger:
        card = r["kind"] in _CARD_KINDS
        n = seq.get(int(r["run_id"])) if r["kind"] == "usage" and r.get("run_id") else None
        row([r["created_at"], receipt_type(r["kind"]), _usd4(r["amount_usd"]), _ledger_memo(r),
             "Session %d" % n if n else "",
             "%.2f" % (billing.topup_charge_cents(Decimal(str(r["amount_usd"]))) / 100) if card else "",
             (r.get("stripe_ref") or "") if card else ""])
    fname = "tau-credits-%s.csv" % repo.now_utc().strftime("%Y%m%d")
    return ("download", fname, "text/csv; charset=utf-8", buf.getvalue())


TRADE_MODES = ("paper", "demo", "live")
TRADES_CSV_MAX_ROWS = 50000  # about 7MB; a cap, so one request can't read an unbounded book
TRADES_CSV_HEADER = ["closed_at_utc", "opened_at_utc", "mode", "strategy", "strategy_title", "ticker", "side",
                     "contracts", "entry_price", "exit_price", "entry_fee", "exit_fee", "realized_pnl",
                     "close_reason", "market", "closed_at_local"]
# The file's last column was each trade's internal position id (walk 2026-11-01): no column carries an id now. A trade
# is told apart by its Kalshi contract (ticker) and its times. The ticker carries the raw series key
# (KXSOL15M-26NOV011815-00), so the market column names the market in words (markets.contract_customer_name), as the
# pages do. It comes last: every column a reader already reads keeps its header and its place.
# closed_at_local, after it, is when the trade closed in the reader's own zone with its offset ("2026-10-31T21:05:00-04:00",
# app.services.reader_day), so its first ten characters are the day Home's "Today" and its paper days count it on: a
# New York reader's 9pm trade read "2026-11-01" in closed_at_utc. "+00:00" while the reader's zone isn't known yet.


def _close_said(r: Dict) -> str:
    """A trade's close in words, as the strategy page's trade line says it (trade_why, without the strategy's rule):
    "Held to settlement: paid $1", "Held to settlement: Kalshi cancelled this market and paid 50¢ a contract", "Exited at
    60¢: take-profit (+20¢)". The column said the stored code ("take_profit", "edge_gone"), and a market Kalshi cancelled
    read "settled" like any win or loss. Words only: the prices and P&L columns are the engine's, as they were."""
    reason = r.get("close_reason") or ""
    if r.get("exit_price") in (None, ""):
        return reason.replace("_", " ")
    return trade_why({**r, "status": "closed", "order_ref": None}) or reason.replace("_", " ")


def _iso_utc(v: Any) -> str:
    if not isinstance(v, datetime):
        return "" if v is None else str(v)
    v = v.astimezone(timezone.utc).replace(tzinfo=None) if v.tzinfo else v
    return v.replace(microsecond=0).isoformat() + "Z"


def _dec(v: Any, places: int = 4) -> str:
    """An amount to `places`; text is already the file's (closed_trades_for_export reads it so)."""
    if isinstance(v, str):
        return v
    return "" if v is None else "%.*f" % (places, float(v))


def _made(v: Any, minute: bool = False) -> str:
    """When a strategy was made, as the file says it: "made Oct 25, 2026" (to the minute, in UTC, when
    the day alone doesn't tell two apart); '' when unknown."""
    if not isinstance(v, datetime):
        return ""
    v = v.astimezone(timezone.utc).replace(tzinfo=None) if v.tzinfo else v
    return "made %s %d, %d%s" % (v.strftime("%b"), v.day, v.year, v.strftime(" %H:%M UTC") if minute else "")


def _strategy_names(rows: List[Dict]) -> Dict[int, str]:
    """The trades file's strategy column: each strategy by its title, the name the customer gave it or
    saw it under, never its internal id (the file once said "No. 00042"). Two strategies in the file
    with one title are told apart in plain words: by market, then also the day each was made, then the
    minute, and, made in the same minute, by the order they were made ("2 of 2"). A trade whose
    strategy is gone has none ('')."""
    first: Dict[int, Dict] = {}
    for r in rows:
        sid = r.get("strategy_id")
        if sid is not None and (r.get("title") or "").strip() and sid not in first:
            first[sid] = r
    names = {sid: r["title"].strip() for sid, r in first.items()}
    same: Dict[str, List[int]] = {}
    for sid, name in names.items():
        same.setdefault(name.casefold(), []).append(sid)
    for sids in same.values():
        if len(sids) < 2:
            continue
        sids.sort(key=lambda s: (_iso_utc(first[s].get("strategy_created")), s))  # the order they were made
        market = {s: customer_name(first[s].get("strategy_market"), "") for s in sids}
        for how in ((), (False,), (True,)):
            tags = {s: ", ".join(p for p in [market[s]] + [_made(first[s].get("strategy_created"), m) for m in how] if p)
                    for s in sids}
            if len(set(tags.values())) == len(sids):
                break
        else:
            tags = {s: ", ".join(p for p in (tags[s], "%d of %d" % (k + 1, len(sids))) if p) for k, s in enumerate(sids)}
        for s in sids:
            names[s] = "%s (%s)" % (names[s], tags[s])
    return names


# A removed (stopped) deployment's trades, in the strategy column: the file filed them under the same name as the
# strategy's current deployment, so a reader couldn't tell the two books' runs apart (walk 2026-10-31). In words,
# within the column (the columns and their order stay as they are), never by an id.
REMOVED_DEPLOYMENT = " (removed deployment)"


def _strategy_cell(names: Dict[int, str], r: Dict) -> str:
    name = names.get(r.get("strategy_id"), "")
    return name + REMOVED_DEPLOYMENT if name and r.get("deployment_removed") else name


def trades_csv(ctx: Ctx):
    """One mode's closed trades as a CSV the user can check the P&L from: realized_pnl summed over the
    file is the mode's realized P&L on the dashboard (demo's is since the last reload, so it can
    differ by what came before). Never mixes modes. Newest TRADES_CSV_MAX_ROWS at most, and says so."""
    import io
    mode = (ctx.query.get("mode") or "").strip().lower()
    if mode not in TRADE_MODES:
        raise ApiError(400, "Pick a mode: paper, demo or live.")
    rows = repo.closed_trades_for_export(ctx.cur, ctx.uid, mode, TRADES_CSV_MAX_ROWS + 1)
    zone = reader_day.reader_zone(ctx)
    cut = len(rows) > TRADES_CSV_MAX_ROWS
    buf = io.StringIO()
    row = _csv_rows(buf)
    row(TRADES_CSV_HEADER)
    names = _strategy_names(rows[:TRADES_CSV_MAX_ROWS])
    # A close in words reads only the trade's close reason and its entry and exit prices (_close_said), and a book's
    # thousands of trades share a few dozen of those: each is worded once per file.
    said: Dict[Tuple, str] = {}
    market: Dict[Tuple, str] = {}  # each (series, strategy market) named once per file
    for r in reversed(rows[:TRADES_CSV_MAX_ROWS]):  # oldest first, the order a reader checks a book in
        key = (r.get("close_reason"), r.get("entry_price"), r.get("exit_price"))
        close = said.get(key)
        if close is None:
            close = said[key] = _close_said(r)
        mkey = (str(r["ticker"] or "").split("-", 1)[0], r.get("strategy_market"))
        name = market.get(mkey)
        if name is None:
            name = market[mkey] = contract_customer_name(r["ticker"], r.get("strategy_market"))
        row([
            _iso_utc(r["closed_at"]), _iso_utc(r["opened_at"]), r["mode"],
            _strategy_cell(names, r), r.get("title") or "", r["ticker"], r["side"], int(r["qty"]),
            _dec(r["entry_price"]), _dec(r["exit_price"]), _dec(r["entry_fee"]), _dec(r["exit_fee"]),
            _dec(r["pnl"]), close, name, reader_day.local_iso(r["closed_at"], zone)])
    if cut:
        row(["# Only the newest %d trades are in this file; older ones are not." % TRADES_CSV_MAX_ROWS])
    fname = "tau-trades-%s-%s.csv" % (mode, repo.now_utc().strftime("%Y%m%d"))
    return ("download", fname, "text/csv; charset=utf-8", buf.getvalue())


def billing_setup_intent(ctx: Ctx):
    if not billing.configured():
        raise ApiError(503, "Payments aren't enabled yet.")
    try:
        return billing.create_setup_intent(_customer_id(ctx))
    except billing.StripeError as e:
        raise _stripe_failed(ctx, e, 502, "setup intent", "The card form couldn't start just now. Reload the page "
                                                          "and try again in a minute.")


def billing_card_added(ctx: Ctx):
    pm = str(ctx.body.get("payment_method_id") or "")
    if not pm.startswith("pm_"):
        raise ApiError(400, "No card to save.")
    cust = repo.get_stripe_customer(ctx.cur, ctx.uid)
    try:
        cid = cust["stripe_customer_id"] if cust else _customer_id(ctx)
        billing.set_default_card(cid, pm)
    except billing.StripeError as e:
        raise _stripe_failed(ctx, e, 502, "save card", "Your card couldn't be saved just now. Reload the page "
                                                       "and add it again.")
    repo.set_default_pm(ctx.cur, ctx.uid, pm)
    # A new card answers a failed auto-reload: its notice (the bell line and Billing's banner) is no
    # longer so. Best-effort; the card is saved either way.
    try:
        repo.delete_notifications(ctx.cur, ctx.uid, billing.RELOAD_FAILED_KIND)
    except Exception as e:  # noqa: BLE001
        print("[billing] clearing the auto-reload failure notice failed for user %s: %r" % (ctx.uid, e), flush=True)
    return {"ok": True}


def _amount(ctx: Ctx, key: str, lo, hi, said: str) -> float:
    """A dollar amount from the body between lo and hi, else a 400 that says the range in the user's
    words ("Add between $5.00 and $1,000.00 in credits.", as the page writes dollars), never the
    field's name ("amount must be between 5 and 1000"). The bounds and the check are _num's."""
    try:
        return _num(ctx.body, key, float(lo), float(hi))
    except ApiError:
        raise ApiError(400, said % ("${:,.2f}".format(Decimal(str(lo))), "${:,.2f}".format(Decimal(str(hi))))) from None


def billing_topup(ctx: Ctx):
    if not billing.configured():
        raise ApiError(503, "Payments aren't enabled yet.")
    amount = _amount(ctx, "amount", billing.MIN_TOPUP, billing.MAX_TOPUP, "Add between %s and %s in credits.")
    cust = repo.get_stripe_customer(ctx.cur, ctx.uid)
    if not cust or not cust.get("default_pm_id"):
        raise ApiError(409, "Add a card first.")
    from decimal import Decimal as _D
    credits = _D(str(amount))
    idem = "topup-%s-%d" % (ctx.uid, int(repo.now_utc().timestamp()))
    try:
        pi = billing.charge(cust["stripe_customer_id"], cust["default_pm_id"],
                            billing.topup_charge_cents(credits), off_session=False, idem=idem,
                            metadata={"user_id": ctx.uid, "credits": str(credits), "purpose": "topup"})
    except billing.StripeError as e:
        # Not "you weren't charged": a dropped connection can fail here after the charge went through.
        raise _stripe_failed(ctx, e, 402, "top-up", "The top-up didn't complete. Reload this page in a minute "
                                                    "to check your balance before you try again.")
    if pi["status"] != "succeeded":
        print("[billing] top-up for user %s ended %s (%s)" % (ctx.uid, pi["status"], pi.get("id")), flush=True)
        raise ApiError(402, "The card didn't complete the charge. Reload this page in a minute to check your "
                            "balance before you try another card.")
    with repo.transaction(ctx.conn) as tx:
        repo.add_credit(tx, ctx.uid, "topup", credits, "Added $%s credits" % credits, stripe_ref=pi["id"])
    # What the card was charged, as charged: the confirmation says this, not a float sum of its own.
    return {"ok": True, "balance": credit_exact(repo.credit_balance(ctx.cur, ctx.uid)),
            "charged": billing.topup_charge_cents(credits) / 100}


def billing_autoreload(ctx: Ctx):
    on = ctx.body.get("on") is True
    if on and not (repo.get_stripe_customer(ctx.cur, ctx.uid) or {}).get("default_pm_id"):
        raise ApiError(409, "Add a card before turning on auto-reload.")
    from decimal import Decimal as _D
    amount = threshold = None
    if ctx.body.get("amount") is not None:
        amount = _D(str(_amount(ctx, "amount", billing.AUTORELOAD_AMOUNT, billing.MAX_TOPUP,
                                "Auto-reload adds between %s and %s at a time.")))
    if ctx.body.get("threshold") is not None:
        threshold = _D(str(_amount(ctx, "threshold", billing.AUTORELOAD_THRESHOLD, billing.MAX_TOPUP,
                                   "Auto-reload can start at a balance between %s and %s.")))
    repo.set_auto_reload(ctx.cur, ctx.uid, on, amount=amount, threshold=threshold)
    # What one reload of the saved amount charges the card, so the page's other copy of it (the
    # autopilot card) says the new figure without reading Billing again.
    return {"ok": True, "autoreload_charge": billing.topup_charge_cents(amount) / 100 if amount is not None else None}


def billing_remove_card(ctx: Ctx):
    pm = str(ctx.body.get("payment_method_id") or "")
    cust = repo.get_stripe_customer(ctx.cur, ctx.uid)
    if not cust:
        raise ApiError(404, "No card on file.")
    try:
        cards = billing.list_cards(cust["stripe_customer_id"])
    except billing.StripeError as e:
        raise _stripe_failed(ctx, e, 502, "list cards", "Your cards couldn't be loaded just now, so nothing was "
                                                        "removed. Try again in a minute.")
    if len(cards) <= 1:
        raise ApiError(409, "You can't remove your only card. Add another first.")
    if not any(c["id"] == pm for c in cards):
        raise ApiError(404, "That card isn't on file.")
    try:
        billing.detach_card(pm)
    except billing.StripeError as e:
        raise _stripe_failed(ctx, e, 502, "remove card", "That card couldn't be removed just now. Try again "
                                                         "in a minute.")
    if cust.get("default_pm_id") == pm:
        other = next(c["id"] for c in cards if c["id"] != pm)
        try:
            billing.set_default_card(cust["stripe_customer_id"], other)
        except billing.StripeError as e:
            # The card is gone already; Tau charges the card it has saved as the default, so point
            # that at the remaining card either way rather than leave it on the removed one.
            print("[billing] set default after remove failed for user %s: %s" % (ctx.uid, e), flush=True)
        repo.set_default_pm(ctx.cur, ctx.uid, other)
    return {"ok": True}
