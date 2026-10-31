"""Every error a customer can read says what happened, in their words, and never the request's plumbing.
tests/test_customer_leaks.py keeps the Dev Logs guard's internals (tests/test_devlog.py INTERNAL) off the routes'
own error words. This adds the server's own replies (app/server.py's dispatcher, app/web/*.py) to that scan, and
the kinds a walk of every form and detail URL found there (2026-10-31): a request field's own name ("amount must be
a number"), a number in Python's notation ("between 1 and 1e+06"), the market code from a link ("Kalshi has no
market KXNOPE-26OCT28-X."), the request's plumbing ("Missing request header.", "Malformed JSON."), a row id ("Bad
entry id.") and a code word with nothing to do ("Not found", "Unknown model.")."""
import ast
import re
from pathlib import Path

import pytest

from app.web import http
from tests.test_customer_leaks import ALLOWED_WORDS  # the demo key form's example Key ID
from tests.test_devlog import INTERNAL

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "app" / "web"
# admin.py is the operator's page, behind is_admin.
SOURCES = [p for p in sorted((WEB / "routes").glob("*.py")) if p.name != "admin.py"] + \
    sorted(WEB.glob("*.py")) + [ROOT / "app" / "server.py"]

# Each kind keeps one of the walk's own examples, which the guard must still catch.
ERROR_LEAKS = {
    "a request field's own name": (r"^%s must\b|\b[a-z]+_[a-z_]+ must be\b|^(?:amount|stake|key) must\b",
                                   "amount must be between 1 and 1e+06"),
    "a number in code notation": (r"\d(?:\.\d+)?e[+-]?\d", "amount must be between 1 and 1e+06"),
    "the request's plumbing": (r"\bJSON\b|\brequest header\b|\bHTTP\b|\bpayload\b|\bendpoint\b|\bnull\b|\bundefined\b",
                               "Missing request header."),
    "a row id": (r"\bids?\b", "Bad entry id."),
    "a code word with nothing to do": (
        r"^(?:Not found|(?:Unknown|Unsupported|Bad|Malformed|Missing|Expected) [\w ]+|Request too large)\.?$",
        "Unknown model."),
}
# A route message that interpolates one of these names prints what the request or a row carried: a body key, the
# market code from a link, a row's number. ("Kalshi has no market %s." % ticker, "%s must be a number" % key.)
CARRIED = {"key", "ticker", "series", "field", "column", "table", "uid", "user_id", "rid", "sid", "pid", "did"}
CARRIED_EXAMPLE = '"Kalshi has no market %s." % ticker'


def _kinds(text):
    return [k for k, (pat, _) in ERROR_LEAKS.items() if re.search(pat, text)] + \
        [k for k, (pat, _) in INTERNAL.items() if re.search(pat, text, re.I if k.startswith("what Tau pays") else 0)]


def _consts(tree):
    """The module's NAME = "words" constants, so a message held in one (server.NOT_FOUND) is read too."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value.value
    return out


def _message_node(node):
    """The message of an ApiError(status, msg) or a _send_json(status, {"error": msg}) call, else None."""
    fn = node.func
    name = getattr(fn, "id", None) or getattr(fn, "attr", None)
    if name == "ApiError" and len(node.args) > 1:
        return node.args[1]
    if name == "_send_json" and len(node.args) > 1 and isinstance(node.args[1], ast.Dict):
        for k, v in zip(node.args[1].keys, node.args[1].values):
            if isinstance(k, ast.Constant) and k.value == "error":
                return v
    return None


def _errors():
    for path in SOURCES:
        tree = ast.parse(path.read_text())
        consts = _consts(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            msg = _message_node(node)
            if msg is None:
                continue
            words = [c.value for c in ast.walk(msg) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
            words += [consts[n.id] for n in ast.walk(msg) if isinstance(n, ast.Name) and n.id in consts]
            yield "%s:%d" % (path.name, node.lineno), ALLOWED_WORDS.sub("", " ".join(words)), msg


def _carried(msg):
    """The CARRIED names a message interpolates (a % b, an f-string)."""
    spots = [n.right for n in ast.walk(msg) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod)]
    spots += [v.value for n in ast.walk(msg) if isinstance(n, ast.JoinedStr) for v in n.values if isinstance(v, ast.FormattedValue)]
    return sorted({n.id for s in spots for n in ast.walk(s) if isinstance(n, ast.Name) and n.id in CARRIED})


def test_the_scan_reads_the_servers_own_replies_too():
    found = {where: words for where, words, _ in _errors()}
    assert len(found) > 100
    assert any(w.startswith("server.py") for w in found) and any(w.startswith("http.py") for w in found)
    assert any("Pick a model from the list" in w for w in found.values())


@pytest.mark.parametrize("kind", sorted(ERROR_LEAKS))
def test_the_guard_still_catches_what_the_walk_found(kind):
    pat, example = ERROR_LEAKS[kind]
    assert re.search(pat, example), kind


def test_the_carried_check_still_catches_what_the_walk_found():
    assert _carried(ast.parse(CARRIED_EXAMPLE, mode="eval").body) == ["ticker"]


@pytest.mark.parametrize("text", [
    "Add between $5.00 and $1,000.00 in credits.",
    "Paste the Key ID from the API keys page on kalshi.com: the short code shown next to the key.",
    "Images must be under 3 MB.",
    "Keep posts under 2,000 characters.",
    "That strategy isn’t yours or doesn’t exist.",
    "Pick a model from the list, or leave it on the default.",
    "Enter a stake between $1 and $500.",
    "You need at least $0.50 in credits to start a research session. Add credits on the Billing tab.",
    "Kalshi has no market at this link. It may have been taken down, or the link is incomplete. Find it again on Explore.",
    "Tau couldn’t find that. Reload the page and try again.",
])
def test_the_guard_leaves_customer_words_alone(text):
    assert _kinds(text) == []


def test_error_words_carry_no_plumbing_or_internal_detail():
    assert [(where, _kinds(words)) for where, words, _ in _errors() if _kinds(words)] == []


def test_no_error_prints_what_the_request_or_a_row_carried():
    assert [(where, _carried(msg)) for where, _, msg in _errors() if _carried(msg)] == []


# The number fields' own check (funds' paper and demo amounts, the strategy page's what-if dials): their words, in
# the units the page shows, never the body's key or 1e+06.
@pytest.mark.parametrize("key,lo,hi,said", [
    ("amount", 1, 1_000_000.0, "Enter an amount between $1 and $1,000,000."),
    ("fee_multiplier", 0.0, 3.0, "Enter a fee multiplier between 0× and 3×."),
    ("slippage_cents", 0.0, 5.0, "Enter a slippage between 0¢ and 5¢."),
    ("stake", 1.0, 500.0, "Enter a stake between $1 and $500."),
    ("other", 0.5, 2.5, "Enter a number between 0.5 and 2.5."),
])
@pytest.mark.parametrize("value", [None, "12", True, -1, 2_000_000, float("nan")])
def test_a_bad_number_says_what_to_enter_in_the_pages_units(key, lo, hi, said, value):
    with pytest.raises(http.ApiError) as e:
        http._num({key: value}, key, lo, hi)
    assert e.value.status == 400 and e.value.message == said
    assert _kinds(said) == []
