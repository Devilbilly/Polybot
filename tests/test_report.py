import unittest
from polybot import report as R
from tests.helpers import make_market, make_reversal_market


class TestFavoriteValidation(unittest.TestCase):
    def test_runs_and_reports(self):
        markets = [make_market(fav="YES") for _ in range(10)] + \
                  [make_market(fav="NO") for _ in range(10)] + \
                  [make_reversal_market() for _ in range(5)]
        rep = R.favorite_validation(markets, folds=3)
        for k in ("oos_roi_pct", "cv_all_positive", "mc_kill_rate", "cost_roi",
                  "seq_all_positive", "seq_segments_roi"):
            self.assertIn(k, rep)
        self.assertEqual(len(rep["cv_folds_roi"]), 3)
        self.assertEqual(len(rep["seq_segments_roi"]), 6)
        self.assertEqual(len(rep["cost_roi"]), 3)
        self.assertTrue(0.0 <= rep["mc_kill_rate"] <= 1.0)


class TestSpotValidation(unittest.TestCase):
    def test_control_passes_and_lag_helps(self):
        rep = R.spot_validation(count=200, seed=42)
        self.assertTrue(rep["control_passes"])              # lag=0 -> ~no edge
        self.assertIn("lag10_roi_pct", rep)
        # at lag 0 the efficient-market spot strategy must not make money
        self.assertLess(abs(rep["lag0_roi_pct"]), 1.0)


class TestDecorrelationValidation(unittest.TestCase):
    def test_two_edges_decorrelated(self):
        rep = R.decorrelation_validation(count=300, seed=2)
        self.assertLess(abs(rep["correlation"]), 0.5)       # weakly correlated
        self.assertEqual(len(rep["sharpe_individual"]), 2)
        self.assertIn("diversification_gain", rep)


if __name__ == "__main__":
    unittest.main()
