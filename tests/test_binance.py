import math
import unittest
import numpy as np
from polybot import binance as B


class TestWindows(unittest.TestCase):
    def test_slices_full_windows(self):
        w = B.closes_to_windows(list(range(900)), 300)
        self.assertEqual(len(w), 3)
        self.assertEqual(len(w[0]), 300)
        self.assertEqual(w[1][0], 300.0)

    def test_drops_partial_tail(self):
        w = B.closes_to_windows(list(range(700)), 300)
        self.assertEqual(len(w), 2)        # 700//300 = 2; trailing 100 dropped

    def test_empty(self):
        self.assertEqual(B.closes_to_windows([], 300), [])


class TestVol(unittest.TestCase):
    def test_nonnegative(self):
        path = [100 * math.exp(0.0005 * i) for i in range(100)]
        self.assertGreaterEqual(B.estimate_vol(path), 0.0)

    def test_constant_is_zero(self):
        self.assertEqual(B.estimate_vol([100.0] * 50), 0.0)

    def test_too_short_is_zero(self):
        self.assertEqual(B.estimate_vol([100.0]), 0.0)

    def test_matches_known_stdev(self):
        # alternating +r/-r log steps -> std of returns == r
        r = 0.001
        path = [100.0]
        for i in range(200):
            path.append(path[-1] * math.exp(r if i % 2 == 0 else -r))
        self.assertAlmostEqual(B.estimate_vol(path), r, places=4)


if __name__ == "__main__":
    unittest.main()
