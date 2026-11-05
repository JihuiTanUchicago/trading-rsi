"""The proposer reads the 2026-11-05 hand-built Bitcoin results, and the searched-out line never tells the
user a market is "exhausted" (Todd: "I don't get why you say you exhaust")."""
from app.research import agent


def test_btc_profiles_carry_the_measured_tick_facts():
    for key in ("btc15m", "btc15m_tick", "btc_daily"):
        text = agent._market_profile(key)
        assert "MEASURED ON BITCOIN TICKS" in text and "0.2-1.5c" in text
    assert "MEASURED ON BITCOIN TICKS" not in agent._market_profile("eth15m")


def test_tick_profile_no_longer_recommends_taker_lead_lag():
    assert "is REAL here but worth well under the taker cost" in agent._market_profile("btc15m_tick")


def test_searched_out_line_tells_reviews_not_to_say_exhausted(monkeypatch):
    monkeypatch.setattr(agent, "_autopilot_research_keys", lambda cur, uid: ["btc15m", "btc_daily"])
    monkeypatch.setattr(agent, "closed_markets", lambda cur, uid, keys: {k: [] for k in keys})
    line = agent._exhausted_line(None, 1)
    assert line.startswith("Searched-out markets") and 'never "exhausted"' in line
    monkeypatch.setattr(agent, "closed_markets", lambda cur, uid, keys: {"btc15m": []})
    line = agent._exhausted_line(None, 1)
    assert "do NOT propose research on these: btc15m" in line and "searched out by the taker loop" in line
