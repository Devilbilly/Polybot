import os
import tempfile
import unittest
import numpy as np

from polybot import backtester as bt
from polybot import database as D
from tests.helpers import make_market, make_reversal_market

CFG = {
    "strategies": [
        {"id": "m", "name": "fav_convergence", "weight": 0.6,
         "params": {"buy_p": 0.70, "sell_p": 0.93, "time_cutoff": 0.50, "stop_p": 0.50, "max_buy": 1}},
        {"id": "l", "name": "fav_convergence", "weight": 0.4,
         "params": {"buy_p": 0.85, "sell_p": 0.96, "time_cutoff": 0.70, "stop_p": 0.50, "max_buy": 1}},
    ],
    "engine": {"fee": 0.001, "slippage": 0.002, "cap_fills": True},
    "risk": {"kill_switch_dd": 0.25, "round_loss_limit": 0.08, "min_capital": 50.0},
}


class TestTicksFromArrays(unittest.TestCase):
    def test_time_progress_from_rem(self):
        a = make_market(n=100)
        ticks = list(bt.ticks_from_arrays(a))
        self.assertAlmostEqual(ticks[0].time_progress, 1 - 300 / 300, places=2)
        self.assertGreater(ticks[-1].time_progress, 0.9)

    def test_fallback_when_no_rem(self):
        a = make_market(n=100); a["rem"] = np.full(100, -1.0)
        ticks = list(bt.ticks_from_arrays(a))
        self.assertAlmostEqual(ticks[len(ticks) // 2].time_progress, 0.5, delta=0.1)

    def test_skips_zero_prices(self):
        a = make_market(n=100); a["ws_bid"][:10] = 0.0
        self.assertEqual(len(list(bt.ticks_from_arrays(a))), 90)


class TestEntryId(unittest.TestCase):
    def test_explicit_and_default(self):
        self.assertEqual(bt._entry_id({"id": "x", "name": "f"}, 3), "x")
        self.assertEqual(bt._entry_id({"name": "f"}, 3), "f#3")


class TestSimulateMarket(unittest.TestCase):
    def test_winning_favorite_positive(self):
        a = make_market(fav="YES", fav_price=0.85)
        fr = bt.simulate_market(a, CFG)
        self.assertGreater(fr["m"], 0)        # bought YES favorite, YES won

    def test_reversal_caps_loss(self):
        a = make_reversal_market()
        fr = bt.simulate_market(a, CFG)
        # bought YES at ~0.85, stopped near 0.20 -> a loss, but a PARTIAL one (not -100%)
        self.assertLess(fr["m"], 0)
        self.assertGreater(fr["m"], -1.0)

    def test_noop_zero(self):
        cfg = {"strategies": [{"id": "n", "name": "noop", "weight": 1.0}],
               "engine": {}, "risk": {}}
        self.assertEqual(bt.simulate_market(make_market(), cfg)["n"], 0.0)


class TestCompound(unittest.TestCase):
    def test_known_fractions(self):
        cfg = {"strategies": [{"id": "a", "name": "noop", "weight": 1.0}], "risk": {}}
        res = bt._compound([{"a": 0.10}, {"a": 0.10}], cfg, capital=100.0)
        self.assertAlmostEqual(res.final, 100 * 1.1 * 1.1, places=6)
        self.assertAlmostEqual(res.roi_pct, 21.0, places=4)
        self.assertEqual(res.win_rate_pct, 100.0)

    def test_kill_switch_stops_trading(self):
        cfg = {"strategies": [{"id": "a", "name": "noop", "weight": 1.0}],
               "risk": {"kill_switch_dd": 0.25}}
        # -30% first market trips kill switch; the +100% that follows must NOT be applied
        res = bt._compound([{"a": -0.30}, {"a": 1.00}], cfg, capital=100.0)
        self.assertAlmostEqual(res.final, 70.0, places=6)


class TestParallelMatchesSequential(unittest.TestCase):
    def test_parity(self):
        markets = [make_market(fav="YES") for _ in range(6)] + \
                  [make_market(fav="NO") for _ in range(6)] + \
                  [make_reversal_market() for _ in range(4)]
        rp = bt.run_parallel(markets, CFG, n_jobs=2)
        rs = bt.run_sequential(markets, CFG)
        self.assertAlmostEqual(rp.roi_pct, rs.roi_pct, places=6)
        self.assertAlmostEqual(rp.final, rs.final, places=6)


class TestLoadMarkets(unittest.TestCase):
    def test_from_csv_dir_and_db(self):
        from tests.test_database import write_csv
        with tempfile.TemporaryDirectory() as d:
            csvdir = os.path.join(d, "data"); os.makedirs(csvdir)
            write_csv(os.path.join(csvdir, "Token_1.csv"), final=0.9)
            self.assertEqual(len(bt.load_markets(csvdir)), 1)
            dbp = os.path.join(d, "t.db")
            db = D.Database(dbp); db.import_csv_dir(csvdir); db.close()
            self.assertEqual(len(bt.load_markets(dbp)), 1)


class TestResultStr(unittest.TestCase):
    def test_str(self):
        r = bt.BacktestResult(10, 50.0, 1500.0, 8.0, 1.2, 70.0, {"a": 50.0})
        self.assertIn("ROI=+50.0%", str(r))


if __name__ == "__main__":
    unittest.main()
