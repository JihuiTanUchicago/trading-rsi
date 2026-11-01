"""Explore's intro names only the kinds of market a customer can find on it.

Walk 2026-11-01: the intro said "across crypto, politics, sports, weather and more", but politics (with economics,
financials and entertainment) was pruned on 2026-10-24 (data_pipeline.common.TRADED_CATEGORIES: sports, weather and
crypto), and Explore's list is the snapshot collector's feed, which reads only the series in
data_pipeline/snapshot_series.txt: crypto and sports today, no weather. The intro names a kind of market exactly when
that file holds one of its series, so adding weather series to the file asks for "weather" in the intro."""
import re
from pathlib import Path

from app.trading.markets import MARKETS

ROOT = Path(__file__).resolve().parents[1]
PRUNED = ("politic", "election", "econom", "financ", "entertainment", "commodit", "and more")


def _ledes():
    src = (ROOT / "app/static/page-explore.js").read_text()
    tau = re.findall(r"boardTitle\('Explore', '([^']*)'\)", src)
    meridian = re.findall(r'<h1 class="page">Explore</h1>\$\{pageLede\(\'([^\']*)\'\)\}', src)
    assert len(tau) == 1 and len(meridian) == 1, (tau, meridian)
    return tau[0], meridian[0]


def _snapshot_series():
    lines = (ROOT / "data_pipeline/snapshot_series.txt").read_text().splitlines()
    return {ln.strip().upper() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")}


def _series_of(*categories):
    return {m.series.upper() for m in MARKETS.values() if m.series and m.category in categories}


def test_both_themes_say_the_same_explore_intro():
    tau, meridian = _ledes()
    assert tau == meridian


def test_explore_intro_names_no_pruned_category():
    lede = _ledes()[0].lower()
    for word in PRUNED:
        assert word not in lede, word


def test_explore_intro_names_a_kind_of_market_exactly_when_the_feed_reads_its_series():
    lede = _ledes()[0].lower()
    feed = _snapshot_series()
    weather = any(s in _series_of("Weather", "Climate and Weather") or re.match(r"KX(HIGH|LOWT?|RAIN|SNOW)", s) for s in feed)
    kinds = {"crypto": bool(feed & _series_of("Crypto")), "sports": bool(feed & _series_of("Sports")), "weather": weather}
    for word, held in kinds.items():
        assert (word in lede) == held, "%s: in the intro %s, in the feed %s" % (word, word in lede, held)
