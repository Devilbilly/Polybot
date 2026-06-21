import unittest
import random
import numpy as np
from polybot.synth import synth_market, synth_dataset, market_from_path
from polybot.btc_model import prob_up


class TestSynthMarket(unittest.TestCase):
    def test_structure_and_fields(self):
        m = synth_market(random.Random(0), n=300)
        for k in ("rem", "ws_bid", "ws_ask", "spot", "strike", "winner"):
            self.assertIn(k, m)
        self.assertEqual(len(m["spot"]), 300)
        self.assertIn(m["winner"], ("YES", "NO"))

    def test_winner_matches_path(self):
        m = synth_market(random.Random(5), n=300)
        expected = "YES" if m["spot"][-1] > m["strike"][0] else "NO"
        self.assertEqual(m["winner"], expected)

    def test_lag0_market_equals_model(self):
        # with lag=0 and no noise, the market mid should equal the true model probability
        m = synth_market(random.Random(7), n=300, lag=0, noise=0.0, spread=0.0)
        i = 150
        mid = (m["ws_bid"][i] + m["ws_ask"][i]) / 2
        model = prob_up(m["spot"][i], m["strike"][0], m["rem"][i], 0.0006)
        self.assertAlmostEqual(mid, model, places=6)

    def test_deterministic(self):
        a = synth_market(random.Random(3), n=100)
        b = synth_market(random.Random(3), n=100)
        self.assertTrue(np.allclose(a["spot"], b["spot"]))

    def test_dataset(self):
        ds = synth_dataset(seed=1, count=10, n=120)
        self.assertEqual(len(ds), 10)


class TestMarketFromPath(unittest.TestCase):
    def test_builds_from_real_path(self):
        path = np.array([100000.0, 100100.0, 100200.0, 100300.0] * 75)  # 300 pts, rising
        m = market_from_path(path, vol=0.0006, lag=2)
        self.assertEqual(len(m["spot"]), 300)
        self.assertEqual(m["strike"][0], 100000.0)        # default strike = first price
        self.assertEqual(m["winner"], "YES")              # ends above strike

    def test_strike_override_and_loser(self):
        path = np.array([100000.0] * 150 + [99900.0] * 150)
        m = market_from_path(path, strike=100050.0, lag=0)
        self.assertEqual(m["strike"][0], 100050.0)
        self.assertEqual(m["winner"], "NO")               # ends below the override strike


if __name__ == "__main__":
    unittest.main()
