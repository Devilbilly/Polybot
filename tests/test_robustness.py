import unittest
from polybot import robustness as R
from polybot.robustness import MonteCarloReport
from tests.helpers import make_market, make_reversal_market

CFG = {
    "strategies": [{"id": "m", "name": "fav_convergence", "weight": 1.0,
                    "params": {"buy_p": 0.70, "sell_p": 0.93, "time_cutoff": 0.50,
                               "stop_p": 0.50, "max_buy": 1, "bullet_pct": 0.02}}],
    "engine": {"fee": 0.001, "slippage": 0.002, "cap_fills": True},
    "risk": {"kill_switch_dd": 0.25, "round_loss_limit": 0.50, "min_capital": 50.0},
}


class TestMonteCarlo(unittest.TestCase):
    def setUp(self):
        self.markets = [make_market(fav="YES") for _ in range(8)] + \
                       [make_market(fav="NO") for _ in range(8)] + \
                       [make_reversal_market() for _ in range(4)]

    def test_report_fields_and_ranges(self):
        rep = R.monte_carlo_ordering(self.markets, CFG, n_runs=50, seed=0)
        self.assertIsInstance(rep, MonteCarloReport)
        self.assertEqual(rep.runs, 50)
        self.assertTrue(0.0 <= rep.kill_rate <= 1.0)
        self.assertTrue(0.0 <= rep.positive_rate <= 1.0)
        self.assertGreaterEqual(rep.dd_max, rep.dd_mean)      # max >= mean
        self.assertLessEqual(rep.roi_min, rep.roi_mean + 1e-6)
        self.assertIn("MONTE-CARLO", str(rep))

    def test_order_invariant_without_kill(self):
        # winning-only markets never trip the kill switch -> wealth is order-invariant
        win = [make_market(fav="YES") for _ in range(12)]
        rep = R.monte_carlo_ordering(win, CFG, n_runs=100, seed=3)
        self.assertEqual(rep.kill_rate, 0.0)
        self.assertAlmostEqual(rep.roi_min, rep.roi_mean, places=4)   # all orderings equal
        self.assertEqual(rep.positive_rate, 1.0)

    def test_deterministic_seed(self):
        a = R.monte_carlo_ordering(self.markets, CFG, n_runs=30, seed=7)
        b = R.monte_carlo_ordering(self.markets, CFG, n_runs=30, seed=7)
        self.assertEqual(a.dd_p95, b.dd_p95)
        self.assertEqual(a.roi_mean, b.roi_mean)


class TestDiversification(unittest.TestCase):
    def _markets(self):
        from polybot.synth import market_from_path
        import random, math
        rng = random.Random(2)
        def gbm(n=300, vol=0.0006, strike=100000.0):
            s = [strike]
            for _ in range(n - 1):
                s.append(s[-1] * math.exp(vol * rng.gauss(0, 1)))
            return s
        return [market_from_path(gbm(), vol=0.0006, lag=5, fav_bias=0.15) for _ in range(400)]

    def _cfgs(self):
        eng = {"fee": 0.001, "slippage": 0.002, "cap_fills": True}
        risk = {"kill_switch_dd": 0.25, "round_loss_limit": 0.50, "min_capital": 50.0}
        fav = {"strategies": [{"id": "fav", "name": "fav_convergence", "weight": 1.0,
                "params": {"buy_p": 0.70, "sell_p": 0.93, "time_cutoff": 0.50, "stop_p": 0.50, "max_buy": 1}}],
               "engine": eng, "risk": risk}
        spot = {"strategies": [{"id": "spot", "name": "btc_spot_divergence", "weight": 1.0,
                 "params": {"vol": 0.0006, "edge": 0.04, "window": 300, "time_cutoff": 0.0, "max_buy": 1}}],
                "engine": eng, "risk": risk}
        return [fav, spot]

    def test_two_edges_are_decorrelated_and_combine_better(self):
        rep = R.diversification_report(self._markets(), self._cfgs())
        self.assertEqual(rep["corr"].shape, (2, 2))
        self.assertLess(abs(rep["corr"][0, 1]), 0.5)                  # weakly correlated
        self.assertGreaterEqual(rep["combined_sharpe"], max(rep["sharpe"]) - 1e-6)  # diversification gain

    def test_per_strategy_fractions_shapes(self):
        markets = self._markets()
        fr = R.per_strategy_fractions(markets, self._cfgs())
        self.assertEqual(len(fr), 2)
        self.assertEqual(len(fr[0]), len(markets))


class TestSequentialStability(unittest.TestCase):
    def test_segments_reported(self):
        markets = [make_market(fav="YES") for _ in range(60)]
        segs = R.sequential_stability(markets, CFG, k=6)
        self.assertEqual(len(segs), 6)
        for s in segs:
            self.assertIn("roi_pct", s)
            self.assertIn("win_rate_pct", s)
            self.assertEqual(s["markets"], 10)

    def test_all_winning_markets_all_positive(self):
        markets = [make_market(fav="YES") for _ in range(48)]
        segs = R.sequential_stability(markets, CFG, k=4)
        self.assertTrue(all(s["roi_pct"] > 0 for s in segs))


class TestCapacity(unittest.TestCase):
    def test_thin_books_degrade_roi_with_capital(self):
        thin = [make_market(fav="YES", depth=30.0) for _ in range(15)]
        rows = R.capacity_curve(thin, CFG, capitals=(1_000, 1_000_000))
        self.assertGreater(rows[0]["roi_pct"], rows[1]["roi_pct"])   # capacity-limited

    def test_deep_books_roi_capital_independent(self):
        deep = [make_market(fav="YES", depth=1e8) for _ in range(15)]
        rows = R.capacity_curve(deep, CFG, capitals=(1_000, 100_000))
        self.assertAlmostEqual(rows[0]["roi_pct"], rows[1]["roi_pct"], delta=1.0)


class TestDepthByPrice(unittest.TestCase):
    def _market_with_depth_rising_in_price(self, n=300):
        import numpy as np
        rem = np.linspace(300.0, 0.0, n)
        ask = np.linspace(0.62, 0.95, n)          # price rises across the late window
        bid = ask - 0.01
        sizes = ask * 1000                         # token size scaled so $depth rises with price
        a = {"rem": rem, "ws_bid": bid, "ws_ask": ask,
             "bid_p1": bid, "bid_s1": sizes, "ask_p1": ask, "ask_s1": sizes}
        for c in ["bid_p2", "bid_s2", "ask_p2", "ask_s2", "bid_p3", "bid_s3", "ask_p3", "ask_s3"]:
            a[c] = np.zeros(n)
        a["winner"] = "YES"
        return a

    def test_depth_grows_with_price(self):
        markets = [self._market_with_depth_rising_in_price() for _ in range(5)]
        d = R.depth_by_price(markets)
        keys = sorted(d)
        self.assertGreater(len(keys), 3)
        self.assertGreater(d[keys[-1]], d[keys[0]])     # high-price bucket deeper than low

    def test_empty_safe(self):
        self.assertEqual(R.depth_by_price([]), {})


class TestCostSensitivity(unittest.TestCase):
    def test_higher_cost_not_better(self):
        markets = [make_market(fav="YES") for _ in range(12)]
        rows = R.cost_sensitivity(markets, CFG)
        self.assertEqual(len(rows), 3)
        rois = [r["roi_pct"] for r in rows]
        self.assertGreaterEqual(rois[0] + 1e-6, rois[-1])   # cheapest >= most expensive
        for r in rows:
            self.assertIn("max_dd_pct", r)


if __name__ == "__main__":
    unittest.main()
