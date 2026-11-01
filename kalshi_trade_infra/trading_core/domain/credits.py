"""Credits as every page shows them: the ledger's six places, then half up to the cent.

Moved from app.web.views (which re-imports every name) so the chat's account facts (app.services.chat_facts) quote
Billing's figures with Billing's own rounding. Pure: no I/O. Display only, never applied to a stored or charged amount."""
from __future__ import annotations

from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Dict

from trading_core.domain.money import CENT

LEDGER_PLACE = Decimal("0.000001")  # app_credit_ledger.amount_usd is DECIMAL(14,6)


def credit_cents(v) -> Decimal:
    """A credits figure to the cent, rounded half up from its exact decimal: the one rounding every
    page that shows credits uses (Billing, the autopilot panel, the dashboard, a session's cost), so
    one $0.105 charge reads $0.11 everywhere. Python's round() works on the binary float and said
    $0.10. A float summed in Python carries noise past the ledger's six places (0.10499999999999998),
    so it is first put back on those places. Never applied to a stored amount."""
    return Decimal(str(v or 0)).quantize(LEDGER_PLACE, rounding=ROUND_HALF_UP).quantize(CENT, rounding=ROUND_HALF_UP)


def credit_usd(v) -> float:
    """credit_cents as the float a JSON answer carries (never -0.0)."""
    return float(credit_cents(v)) + 0.0


def credit_split(parts: Dict[str, float], total=None) -> Dict[str, float]:
    """A credits total's parts to the cent so the shown parts add up to the shown total (credit_cents of
    `total`, or of the parts' sum): each part takes its floor, and the cents left go one each to the
    parts with the largest remainders (the bigger part first on a tie). Every part stays within a cent of
    its exact amount, and a part with nothing in it stays $0. Rounded each on its own, Billing's week read
    $38.85 + $0.18 + $0.18 under $39.22. When the parts can't reach the total that way (a part below zero,
    or a total read apart from the parts that disagrees by more than they can carry) each part is
    credit_usd of its own. Display only: never applied to a stored or charged amount."""
    exact_ = {k: Decimal(str(v or 0)).quantize(LEDGER_PLACE, rounding=ROUND_HALF_UP) for k, v in parts.items()}
    want = int(credit_cents(sum(exact_.values(), Decimal(0)) if total is None else total) * 100)
    base = {k: int((v * 100).to_integral_value(rounding=ROUND_FLOOR)) for k, v in exact_.items()}
    rest = {k: v * 100 - base[k] for k, v in exact_.items()}
    left = want - sum(base.values())
    ups = [k for k in exact_ if rest[k] > 0]
    if any(v < 0 for v in exact_.values()) or not 0 <= left <= len(ups):
        return {k: credit_usd(v) for k, v in parts.items()}
    for k in sorted(ups, key=lambda k: (-rest[k], -exact_[k]))[:left]:
        base[k] += 1
    return {k: float(Decimal(c) / 100) + 0.0 for k, c in base.items()}


def credit_exact(v) -> float:
    """A balance as the ledger holds it (six places, no float noise), for a figure the page compares
    as well as shows: the page rounds it half up to the cent, as credit_cents does. Rounding it to
    four places first could round twice (0.10496 -> 0.105 -> $0.11 beside Billing's $0.10)."""
    return float(Decimal(str(v or 0)).quantize(LEDGER_PLACE, rounding=ROUND_HALF_UP)) + 0.0


def research_week_usd(by_source: Dict[str, float], research) -> float:
    """The week's research credits as the autopilot card shows them: Billing's two research parts ("your research
    sessions" plus "autopilot research"), each as credit_split rounds it so the parts add up, summed. `by_source` is
    the week's usage split (usage_by_source), `research` the week's research debits (credit_week's, the seeded
    example left out). When the two parts don't make up `research` the figure is `research` to the cent. Moved from
    app.web.routes.chat so the chat's account facts say the card's figure. Display only."""
    if not research:
        return 0.0  # no research debits this week: nothing to split
    by = by_source
    if abs(by["research"] + by["autopilot_research"] - research) > 1e-9:
        return credit_usd(research)
    split = credit_split(by)
    return credit_usd(credit_cents(split["research"]) + credit_cents(split["autopilot_research"]))
