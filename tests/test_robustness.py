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
