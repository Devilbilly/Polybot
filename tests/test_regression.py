"""
Regression tests on REAL historical data. These lock in the validated favorite-convergence
behavior end-to-end through the shared engine, so a future refactor can't silently break
the edge (the original bot's failure mode). Skips cleanly if market_data/ is absent.
"""
import os
import unittest
from polybot import backtester as bt

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "market_data")
HAVE_DATA = os.path.isdir(DATA_DIR) and len(os.listdir(DATA_DIR)) > 100

FAV_CFG = {
    "strategies": [
        {"id": "m", "name": "fav_convergence", "weight": 0.6,
         "params": {"buy_p": 0.70, "sell_p": 0.93, "time_cutoff": 0.50, "stop_p": 0.50, "max_buy": 1}},
        {"id": "l", "name": "fav_convergence", "weight": 0.4,
         "params": {"buy_p": 0.85, "sell_p": 0.96, "time_cutoff": 0.70, "stop_p": 0.50, "max_buy": 1}},
    ],
    "engine": {"fee": 0.001, "slippage": 0.002, "cap_fills": True},
    "risk": {"kill_switch_dd": 0.25, "round_loss_limit": 0.08, "min_capital": 50.0},
}


@unittest.skipUnless(HAVE_DATA, "market_data/ not present")
class TestRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.markets = bt.load_markets(DATA_DIR)

    def test_loaded_enough_clean_markets(self):
        self.assertGreater(len(self.markets), 1000)

    def test_fav_is_strongly_profitable(self):
        r = bt.run_parallel(self.markets, FAV_CFG)
        self.assertGreater(r.roi_pct, 50.0, f"FAV regressed: {r}")
        self.assertLess(r.max_dd_pct, 25.0, f"drawdown breached kill-switch: {r}")
        self.assertTrue(55.0 < r.win_rate_pct < 85.0, f"win-rate out of range: {r}")

    def test_deterministic(self):
        r1 = bt.run_parallel(self.markets, FAV_CFG, n_jobs=2)
        r2 = bt.run_parallel(self.markets, FAV_CFG, n_jobs=2)
        self.assertAlmostEqual(r1.roi_pct, r2.roi_pct, places=6)

    def test_parallel_equals_sequential(self):
        sub = self.markets[:400]
        self.assertAlmostEqual(bt.run_parallel(sub, FAV_CFG).roi_pct,
                               bt.run_sequential(sub, FAV_CFG).roi_pct, places=6)

    def test_noop_is_flat(self):
        cfg = {"strategies": [{"id": "n", "name": "noop", "weight": 1.0}], "engine": {}, "risk": {}}
        self.assertAlmostEqual(bt.run_parallel(self.markets, cfg).roi_pct, 0.0, places=6)

    def test_reversal_stop_reduces_drawdown(self):
        """The stop must lower drawdown vs hold-to-settlement (its whole point)."""
        hold = {**FAV_CFG, "strategies": [
            {"id": "m", "name": "fav_hold", "weight": 0.6,
             "params": {"buy_p": 0.70, "sell_p": 0.93, "time_cutoff": 0.50, "max_buy": 1}},
            {"id": "l", "name": "fav_hold", "weight": 0.4,
             "params": {"buy_p": 0.85, "sell_p": 0.96, "time_cutoff": 0.70, "max_buy": 1}},
        ]}
        with_stop = bt.run_parallel(self.markets, FAV_CFG)
        no_stop = bt.run_parallel(self.markets, hold)
        self.assertLess(with_stop.max_dd_pct, no_stop.max_dd_pct,
                        f"stop should cut DD: stop={with_stop.max_dd_pct:.1f} hold={no_stop.max_dd_pct:.1f}")

    def test_deployed_config_profitable(self):
        """Lock in the ACTUAL polybot/portfolio.json so config edits can't silently break it."""
        import json
        from polybot.paper import paper_trade
        with open(os.path.join(os.path.dirname(__file__), "..", "polybot", "portfolio.json")) as f:
            cfg = json.load(f)
        rep = paper_trade(self.markets, cfg)
        self.assertGreater(rep.roi_pct, 50.0, f"deployed config regressed: {rep}")
        self.assertFalse(rep.killed)

    def test_paper_session_profitable(self):
        """The stateful paper trader (realistic fills) must be strongly profitable + survive."""
        from polybot.paper import paper_trade
        rep = paper_trade(self.markets, FAV_CFG)
        self.assertGreater(rep.roi_pct, 50.0, f"paper session regressed: {rep}")
        self.assertLess(rep.max_dd_pct, 25.0)
        self.assertFalse(rep.killed)

    def test_favorites_beat_longshots(self):
        """Core invariant of the edge: buying favorites must beat buying longshots."""
        longshot = {"strategies": [{"id": "ls", "name": "fav_convergence", "weight": 1.0,
                    "params": {"buy_p": 0.05, "sell_p": 0.35, "time_cutoff": 0.50, "stop_p": 0.0, "max_buy": 1}}],
                    "engine": FAV_CFG["engine"], "risk": FAV_CFG["risk"]}
        fav = bt.run_parallel(self.markets, FAV_CFG)
        ls = bt.run_parallel(self.markets, longshot)
        self.assertGreater(fav.roi_pct, ls.roi_pct,
                           f"favorites must beat longshots: fav={fav.roi_pct:.0f} ls={ls.roi_pct:.0f}")


if __name__ == "__main__":
    unittest.main()
