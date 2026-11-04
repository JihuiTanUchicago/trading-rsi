"""Favorite-bid lead: queue-aware fills, settlement sides, the loss-rate judge and the verdict."""
from app.services import favorite_bid as fb
from data_pipeline.kalshi_fees import maker_fee_dollars

CT = 10_000


def book(rows):
    """rows: (sec, yes_bid, yes_ask, bid_size, ask_size)"""
    keys = ("sec", "yes_bid", "yes_ask", "yes_bid_size", "yes_ask_size")
    return {k: [r[i] for r in rows] for i, k in enumerate(keys)}


def fill(side, b, prints, price=0.99, horizon=420, qty=100):
    return fb.queue_fill(side, b["sec"], b["yes_bid"], b["yes_ask"], b["yes_bid_size"], b["yes_ask_size"],
                         CT, prints, price, horizon, qty, maker_fee_dollars)


def test_order_posts_only_inside_the_horizon_and_behind_the_displayed_queue():
    b = book([(CT - 500, 0.99, 1.0, 50, 0), (CT - 300, 0.99, 1.0, 200, 0)])
    r = fill("yes", b, [])
    assert r["posted_sec"] == CT - 300 and r["queue_ahead"] == 200 and r["filled"] == 0


def test_fills_only_after_prints_selling_the_favorite_exceed_the_queue_ahead():
    b = book([(CT - 300, 0.99, 1.0, 200, 0)])
    prints = [(CT - 299.5, 0.99, 500, "no"),     # before the order rests (it rests from the next second)
              (CT - 250, 0.99, 150, "no"),       # eats 150 of the 200 ahead
              (CT - 240, 0.99, 400, "yes"),      # a YES buy lifts the ask: never hits our bid
              (CT - 200, 0.98, 120, "no")]       # a sweep through 99c: 50 more ahead, then 70 to us
    r = fill("yes", b, prints)
    assert r["filled"] == 70 and r["fill_ts"] == CT - 200


def test_no_side_favorite_bids_at_one_minus_the_yes_ask():
    b = book([(CT - 100, 0.0, 0.01, 0, 30)])     # YES ask 1c = a NO bid at 99c with 30 ahead
    r = fill("no", b, [(CT - 50, 0.01, 1000, "yes")])
    assert r["queue_ahead"] == 30 and r["filled"] == 100


def test_contract_fills_settles_each_side_and_charges_the_maker_fee():
    b = book([(CT - 100, 0.99, 1.0, 0, 0)])
    out = fb.contract_fills("T", CT, 0.0, b, [(CT - 50, 0.99, 100, "no")], 0.99, 420, 100, maker_fee_dollars)
    assert len(out) == 1 and out[0].side == "yes" and out[0].win == 0.0
    assert abs(out[0].pnl - (100 * (0 - 0.99) - maker_fee_dollars(100, 0.99))) < 1e-9


def test_judge_breakeven_and_binomial():
    wins = [fb.Fill("T%d" % i, i, "yes", 0.99, 100, 1.0, 0.02, 60, 0) for i in range(500)]
    j = fb.judge(wins, 0.99)
    assert j["losses"] == 0 and abs(j["break_even_loss_rate"] - (0.01 - 0.0175 * 0.99 * 0.01)) < 1e-12
    assert abs(j["p_binom"] - (1 - j["break_even_loss_rate"]) ** 500) < 1e-9
    assert fb.verdict(j) .startswith("accruing")
    big = wins * 3
    assert fb.verdict(fb.judge(big, 0.99), min_fills=1000) == "PASS"
    losing = [fb.Fill("L%d" % i, 10_000 + i, "yes", 0.99, 100, 0.0, 0.02, 60, 0) for i in range(30)]
    assert fb.verdict(fb.judge(big + losing, 0.99), min_fills=1000) == "FAIL"


def test_binomial_cdf_matches_closed_form():
    assert abs(fb._binom_cdf(0, 10, 0.1) - 0.9 ** 10) < 1e-12
    assert abs(fb._binom_cdf(10, 10, 0.3) - 1.0) < 1e-12
