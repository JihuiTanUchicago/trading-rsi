"""One verdict per strategy (services.verdicts), said in the same words by the server (the chat's context) and by
core.js (every page's stamp): the two word tables agree, and a strategy stored "passed" whose own validation didn't
pass gets a verdict of its own and is tinted on its validation's record. Its page stamped PASSED above a glance
saying it didn't pass validation, with a p-value of 0.18 against a bar of 0.0125 tinted as passed (walk 2026-11-02).
Display only: gate_explain's tints are unchanged; the page picks the status they're read with. The DB answers for
every verdict state are in test_verdict_everywhere_db.py."""
import re
from pathlib import Path

import pytest

from app.services import gate_explain, verdicts

CORE = (Path(__file__).resolve().parents[1] / "app" / "static" / "core.js").read_text()
VAL = {"n_trades": 48, "mean_net_return": 0.012, "p_value": 0.18, "dsr": 0.52, "placebo_p": 0.33, "fdr_alpha_i": 0.0125}


def _table(name):
    body = re.search(r"const %s = \{(.*?)\n  \};" % name, CORE, re.S).group(1)
    return {k.strip("'"): v for k, v in re.findall(r"('[\w-]+'|\w+): \[((?:'[^']*'(?:, )?)+)\]", body)}


def _words(name, at):
    return {k: re.findall(r"'([^']*)'", v)[at] for k, v in _table(name).items()}


def test_the_servers_words_are_the_pages_stamps():
    mark, verdict = _words("MARK", 1), _words("VERDICTS", 1)
    assert set(verdict) == {verdicts.MISSED_FINAL, verdicts.HELD_BACK, verdicts.INCONCLUSIVE, verdicts.MISSED_VALIDATION}
    for key, w in {**verdict, **{k: v for k, v in mark.items() if k in verdicts.WORDS}}.items():
        assert verdicts.WORDS[key] == w, key
    assert set(verdicts.WORDS) <= set(mark) | set(verdict)
    # A verdict's word wins over its stored status's; a status alone says its own.
    assert verdicts.word("passed", "inconclusive") == "Validation inconclusive"
    assert verdicts.word("failed", "held_back") == "Held back"
    assert verdicts.word("failed") == "Void" and verdicts.word("pending") == "Not judged"


def _exp(cls, **o):
    return dict({"min_sample": 40, "validation": dict(VAL, classification=cls), "holdout": None, "paper": None, "promoted": False}, **o)


@pytest.mark.parametrize("status,exp,want", [
    ("passed", _exp("inconclusive"), verdicts.INCONCLUSIVE),
    ("passed", _exp("rejected"), verdicts.MISSED_VALIDATION),
    ("passed", _exp("statistically_supported"), None),
    ("passed", _exp("inconclusive", paper={"classification": "deployment_candidate"}), None),  # passed on paper
    ("passed", _exp("inconclusive", paper={"classification": "inconclusive"}), verdicts.INCONCLUSIVE),
    ("passed", _exp("inconclusive", promoted=True), None),
    ("passed", _exp("inconclusive", holdout={"classification": "forward_validated"}), None),
    ("failed", _exp("inconclusive"), None),  # Void says it already
    ("rejected", _exp("rejected"), None),
    ("passed", None, None),  # no gatekeeper record: nothing to read
    ("passed", {"validation": None}, None),
    ("passed", {"validation": "not a row", "paper": "nor this"}, None),  # a record it can't read never fails the page
])
def test_a_pass_whose_validation_didnt_pass_has_a_verdict_of_its_own(status, exp, want):
    assert verdicts.validation_miss(status, exp) == want
    if want:  # the same rule the page's later_verdict gives, ahead of the promoted/perpetual early exit
        assert verdicts.later_verdict({"id": 1, "status": status}, exp) == want


def test_its_validation_figures_are_tinted_on_its_validations_record():
    weak = _exp("inconclusive")
    # gate_explain reads every validation figure of a "passed" strategy as passed: unchanged.
    assert set(gate_explain.strategy_tints("passed", weak)["validation"].values()) == {"pass"}
    # The page reads them with the status tint_status gives: a validation miss's figures as a Void one's.
    shown = gate_explain.strategy_tints(verdicts.tint_status("passed", weak), weak)["validation"]
    assert shown == gate_explain.strategy_tints("failed", weak)["validation"]
    assert shown["p_value"] == "fail"
    # A true pass, a void and a rejected strategy keep the tints they had.
    good = _exp("statistically_supported")
    for status, exp in (("passed", good), ("failed", weak), ("rejected", _exp("rejected"))):
        assert verdicts.tint_status(status, exp) == status
    assert verdicts.tint_status("passed", None) == "passed"
    assert verdicts.tint_status("failed", {"validation": "not a row"}) == "failed"
