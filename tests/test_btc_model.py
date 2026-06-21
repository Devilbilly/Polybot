import unittest
from polybot.btc_model import prob_up


class TestProbUp(unittest.TestCase):
    def test_above_strike_is_likely(self):
        self.assertGreater(prob_up(100500, 100000, 60, 0.0006), 0.5)

    def test_below_strike_is_unlikely(self):
        self.assertLess(prob_up(99500, 100000, 60, 0.0006), 0.5)

    def test_at_strike_is_half(self):
        self.assertAlmostEqual(prob_up(100000, 100000, 60, 0.0006), 0.5, places=6)

    def test_sharpens_as_time_runs_out(self):
        far = prob_up(100200, 100000, 250, 0.0006)
        near = prob_up(100200, 100000, 5, 0.0006)
        self.assertGreater(near, far)         # less time to reverse -> more certain

    def test_settlement_is_binary(self):
        self.assertEqual(prob_up(100001, 100000, 0, 0.0006), 1.0)
        self.assertEqual(prob_up(99999, 100000, 0, 0.0006), 0.0)

    def test_monotonic_in_spot(self):
        ps = [prob_up(s, 100000, 60, 0.0006) for s in (99800, 100000, 100200, 100500)]
        self.assertTrue(all(a < b for a, b in zip(ps, ps[1:])))

    def test_invalid_inputs(self):
        self.assertEqual(prob_up(0, 100000, 60, 0.0006), 0.5)
        self.assertEqual(prob_up(100000, 0, 60, 0.0006), 0.5)
        self.assertEqual(prob_up(100200, 100000, 60, 0.0), 1.0)   # zero vol -> certain


if __name__ == "__main__":
    unittest.main()
