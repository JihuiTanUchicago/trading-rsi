"""The alerter emails once when Kalshi first funds a Bitcoin series with liquidity rewards."""
import data_pipeline.live_alerter as la

POOL = {"markets": 4, "usd": 400.0, "usd_per_market_hour": 50.0, "target_sizes": [1000.0], "start": "2026-11-07T16:00:00Z"}


def test_a_new_pool_is_announced_with_its_per_market_hour_size():
    lines = la.btc_lip_alerts({"KXBTCD": POOL}, {})
    assert len(lines) == 1
    assert "KXBTCD" in lines[0] and "$50.00 per market-hour" in lines[0] and "target size 1000" in lines[0]


def test_a_standing_pool_is_quiet():
    assert la.btc_lip_alerts({"KXBTCD": POOL}, {"KXBTCD": POOL}) == []


def test_a_pool_that_ends_is_noted_once():
    assert la.btc_lip_alerts({}, {"KXBTCD": POOL}) == ["Liquidity rewards on KXBTCD are no longer listed."]


def test_nothing_posted_is_quiet():
    assert la.btc_lip_alerts({}, {}) == []
