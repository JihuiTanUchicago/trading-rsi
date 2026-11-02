"""One verdict per strategy, said in the same words wherever a customer sees it.

A strategy's stored status is written once (agent._status_for, at validation, or for a probation strategy at its
validation miss) and no later check changes it. What the gatekeeper's record says since, or what its status leaves
out, rides beside it as `verdict` on every answer that shows the strategy: the Strategies list and its chips, the
strategy page, the research session rows and headline, Home's new-results strip, the autopilot panel's week line, a
Community share card and the chat's context. The page's own bundle words each key with core.js's VERDICTS (its
stamp, its tone and its lowercase phrase), and WORDS here says the same for the chat. Display only: this reads the
gatekeeper's recorded rows and judges nothing again; no gate, threshold or stored status changes here.

The keys:
- MISSED_FINAL: a validation pass whose one-time locked holdout then failed.
- HELD_BACK: a holdout (or, on probation, a paper) pass the diversification check after it held back.
- INCONCLUSIVE / MISSED_VALIDATION: a strategy stored "passed" whose own validation was inconclusive (or rejected)
  and that no later judgment cleared. Its page's glance said "In its paper forward test ... it didn't pass
  validation" under a PASSED stamp, the list and Community stamped it Passed, and its validation figures were
  tinted as passed (walk 2026-11-01). It is the same evidence a Void (or Rejected) strategy has, so it is toned as
  one.

Moved here from app.web.routes.strategies (which re-imports every name) so the chat's context (research.agent)
words each strategy as its page stamps it without reaching into the web layer.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from app.research import gatekeeper
from app.research import research_repository as research_repo
from app.services import gate_explain
from app.trading import spec as specmod

MISSED_FINAL, HELD_BACK = "missed_final", "held_back"
INCONCLUSIVE, MISSED_VALIDATION = "inconclusive", "missed_validation"

# A validation classification that passed (gate_explain's _PASSED): stat. supported or better.
VALIDATION_PASSED = (gatekeeper.Classification.STAT_SUPPORTED.value, gatekeeper.Classification.FORWARD_VALIDATED.value,
                     gatekeeper.Classification.DEPLOYMENT_CANDIDATE.value)

# Each stored status's and verdict's stamp, as core.js's MARK and VERDICTS say it (tests/test_verdict_everywhere.py
# holds the two tables to each other).
WORDS = {
    "passed": "Passed", "failed": "Void", "rejected": "Rejected", "queued": "Testing", "pending": "Not judged",
    "backtest-only": "Backtest only",
    MISSED_FINAL: "Missed final test", HELD_BACK: "Held back",
    INCONCLUSIVE: "Validation inconclusive", MISSED_VALIDATION: "Missed validation",
}


def word(status: Optional[str], verdict: Optional[str] = None) -> str:
    """The stamp a strategy's page and list show: its verdict's when it has one, else its stored status's."""
    return WORDS.get(verdict or "") or WORDS.get(status or "") or str(status or "")


def exp_num(row: Dict, key: str) -> Optional[float]:
    v = row.get(key)
    return None if v is None else float(v)


def exp_bool(row: Dict, key: str) -> Optional[bool]:
    v = row.get(key)
    return None if v is None else bool(v)


def exp_int(row: Dict, key: str) -> Optional[int]:
    v = row.get(key)
    return None if v is None else int(v)


def phase_block(row: Optional[Dict], holdout: bool = False) -> Optional[Dict]:
    """Shape one app_experiment_results row (validation or holdout) into the §D block."""
    if not row:
        return None
    out = {
        "classification": row.get("classification"),
        "n_trades": exp_int(row, "n_trades"),
        "mean_net_return": exp_num(row, "mean_net_return"),
        "ci_lo": exp_num(row, "ci_lo"),
        "ci_hi": exp_num(row, "ci_hi"),
        "p_value": exp_num(row, "p_value"),
        "psr": exp_num(row, "psr"),
        "dsr": exp_num(row, "dsr"),
        "sharpe": exp_num(row, "sharpe"),
        "net_pnl": exp_num(row, "net_pnl"),
        "fdr_alpha_i": exp_num(row, "fdr_alpha_i"),
        "fdr_reject": exp_bool(row, "fdr_reject"),
        "bh_reject": exp_bool(row, "bh_reject"),
        "placebo_p": exp_num(row, "placebo_p"),
        "n_trials_audited": exp_int(row, "n_trials_audited"),
        "equity_curve": row.get("equity_curve"),
    }
    if holdout:
        out["consumed"] = bool(row.get("holdout_consumed"))
    return out


def later_fields(results: List[Dict]) -> Dict:
    """The experiment block's verdicts, from its result rows (oldest first): its validation, the one-time holdout, a
    probation strategy's one paper judgment (with when it was judged), and whether it was promoted all the way to a
    deployment candidate (agent._is_deployment_candidate's own test). A strategy page reads them from all its rows, a
    list from research_repo.later_results' rows, so both judge alike."""
    latest = lambda phase: next((r for r in reversed(results) if r.get("phase") == phase), None)
    paper = latest("paper")
    return {
        "validation": phase_block(latest("validation")),
        "holdout": phase_block(latest("holdout"), holdout=True),
        "paper": dict(phase_block(paper), verdict_at=paper.get("verdict_at")) if paper else None,
        # A holdout (or paper) pass that isn't was held back by the diversification check after it.
        "promoted": any(r.get("classification") == gatekeeper.Classification.DEPLOYMENT_CANDIDATE.value
                        for r in results),
    }


def _phase_cls(exp: Optional[Dict], phase: str) -> Optional[str]:
    """One phase block's classification, or None for a block that isn't one: a record it can't read never fails the
    page (test_gate_explain's bad record)."""
    block = exp.get(phase) if isinstance(exp, dict) else None
    return block.get("classification") if isinstance(block, dict) else None


def validation_miss(status: Optional[str], exp: Optional[Dict]) -> Optional[str]:
    """INCONCLUSIVE or MISSED_VALIDATION for a strategy stored "passed" whose own validation didn't pass and that no
    later judgment cleared (no holdout row, no paper pass, never promoted), else None. core.js's validationMiss
    reads the same fields of the page's experiment block."""
    exp = exp if isinstance(exp, dict) else {}
    cls = _phase_cls(exp, "validation")
    if status != "passed" or not cls or cls in VALIDATION_PASSED:
        return None
    if exp.get("holdout") or exp.get("promoted"):
        return None
    if _phase_cls(exp, "paper") in VALIDATION_PASSED:
        return None
    return INCONCLUSIVE if cls == gatekeeper.Classification.INCONCLUSIVE.value else MISSED_VALIDATION


def tint_status(status: Optional[str], exp: Optional[Dict]) -> Optional[str]:
    """The status the research paper's figures are tinted by (gate_explain.strategy_tints, unchanged): the stored one,
    except when the strategy's validation itself didn't pass. strategy_tints reads every validation figure of a
    "passed" strategy as passed, so a p-value of 0.18 against a bar of 0.0125 read as cleared under a glance saying it
    didn't pass validation. Without a status its figures are judged on the validation's own classification, as a Void
    strategy's with the same record are. Judged on the record, not the verdict: one that passed on paper after an
    inconclusive validation ("Passed on paper. It fell short on its first test on unseen data") shows that shortfall
    too. The holdout's tints never read the status."""
    cls = _phase_cls(exp, "validation")
    if status == "passed" and cls and cls not in VALIDATION_PASSED:
        return None
    return status


def _lines(fn, *args) -> List[Dict]:
    """gate_explain's lines, or none: a record it can't read never fails the page (views._gate_lines)."""
    try:
        return fn(*args)
    except (TypeError, ValueError, KeyError, AttributeError):
        return []


def later_verdict(s: Dict, exp: Optional[Dict]) -> Optional[str]:
    """MISSED_FINAL, HELD_BACK, INCONCLUSIVE, MISSED_VALIDATION or None for one strategy, from its experiment block (a
    page's _experiment_block, or later_verdicts' slim one built from the same rows by later_fields). Display only: it
    reads the gatekeeper's record and judges nothing again."""
    if not exp:
        return None
    if s.get("status") == "passed" and _lines(gate_explain.for_strategy, "passed", None, exp):
        return MISSED_FINAL  # for_strategy reads a failed holdout first, as the page's why_failed does
    miss = validation_miss(s.get("status"), exp)
    if miss:
        return miss
    if exp.get("promoted") or specmod.has_perpetual_market(s["spec"], s.get("market")):  # research only: never promoted
        return None
    fv = gatekeeper.Classification.FORWARD_VALIDATED.value
    if s.get("status") == "passed" and (exp.get("holdout") or {}).get("classification") == fv:
        return HELD_BACK
    if s.get("status") in ("failed", "rejected") and (exp.get("paper") or {}).get("classification") == fv:
        return HELD_BACK
    return None


def later_verdicts(cur, uid: int, strategies: List[Dict],
                   recorded: Optional[set] = None) -> Dict[int, Tuple[str, Dict]]:
    """{strategy id: (its later_verdict, the slim experiment block it was judged on)} for the strategies a list shows,
    those with one only. At most two reads for the whole list, never one per row: only for a passed strategy stored
    before app_strategies.experiment_id existed, the experiment experiments_for_strategy falls back to (by
    idx_exp_user_market's user_id), then their experiments' validation, holdout, paper and forward rows
    (research_repo.later_results, by primary key and idx_res_exp).
    `recorded`, when given, is filled with the ids of the strategies whose experiment those same reads found: the
    gatekeeper record a strategy page resolves (_experiment_block). A passed strategy left out of it has none, so its
    list row says its backtest figure is later-period, as its page does. Display only."""
    eids = {s["id"]: int(s["experiment_id"]) for s in strategies if s.get("experiment_id")}
    legacy = [s["id"] for s in strategies if s.get("status") == "passed" and s["id"] not in eids]
    if legacy:
        eids.update(research_repo.newest_experiment_ids(cur, uid, legacy))
    later = research_repo.later_results(cur, list(eids.values()))
    out: Dict[int, Tuple[str, Dict]] = {}
    for s in strategies:
        rec = later.get(eids.get(s["id"]))
        if rec is None:
            continue
        if recorded is not None:
            recorded.add(s["id"])
        try:
            exp = dict(later_fields(rec["results"]), min_sample=exp_int(rec, "min_sample"))
        except (TypeError, ValueError):  # a record it can't read never fails the list
            continue
        v = later_verdict(s, exp)
        if v:
            out[s["id"]] = (v, exp)
    return out
