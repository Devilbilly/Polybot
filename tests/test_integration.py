"""
End-to-end integration: exercises the whole pipeline together and asserts the modules
agree at every seam. Catches cross-module breakage that per-module unit tests miss.

  synth markets -> DB write -> DB read -> parallel backtest
                                       -> stateful paper trade (persisted)
                                       -> monitor metrics/alerts
  + report sections run on the same data.
"""
import os
import math
import random
import tempfile
import unittest
import numpy as np

from polybot import database as D
from polybot import backtester as bt
from polybot.paper import PaperTrader
from polybot import monitor as M
from polybot import report as R
from polybot.synth import market_from_path

FAV_CFG = {
    "strategies": [{"id": "fav", "name": "fav_convergence", "weight": 1.0,
                    "params": {"buy_p": 0.70, "sell_p": 0.93, "time_cutoff": 0.50, "stop_p": 0.50, "max_buy": 1}}],
    "engine": {"fee": 0.001, "slippage": 0.002, "cap_fills": True},
    "risk": {"kill_switch_dd": 0.25, "round_loss_limit": 0.50, "min_capital": 50.0},
}


def _gbm(rng, n=300, vol=0.0006, strike=100000.0):
    s = [strike]
    for _ in range(n - 1):
        s.append(s[-1] * math.exp(vol * rng.gauss(0, 1)))
    return np.array(s)


def _insert(db, mid, m):
    n = len(m["ws_bid"])
    for seq in range(n):
        db.insert_tick(mid, seq, {c: float(m[c][seq]) for c in D.TICK_COLS if c in m})
    db.upsert_market(mid, token_id=mid, winner=m["winner"], n_ticks=n)
    db.conn.commit()


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        rng = random.Random(11)
        # favorite-biased markets so the edge is exploitable end-to-end
        self.markets = [market_from_path(_gbm(rng), vol=0.0006, lag=0, fav_bias=0.18) for _ in range(40)]

    def test_db_roundtrip_matches_inmemory_backtest(self):
        with tempfile.TemporaryDirectory() as d:
            db = D.Database(os.path.join(d, "e2e.db"))
            for i, m in enumerate(self.markets):
                _insert(db, f"mk{i}", m)
            loaded = [db.load_market(mid) for mid in db.market_ids()]
            loaded = [x for x in loaded if x is not None]
            self.assertEqual(len(loaded), len(self.markets))
            # backtest from DB-loaded data must equal backtest from in-memory data
            roi_db = bt.run_parallel(loaded, FAV_CFG).roi_pct
            roi_mem = bt.run_parallel(self.markets, FAV_CFG).roi_pct
            self.assertAlmostEqual(roi_db, roi_mem, places=2)
            db.close()

    def test_paper_persist_monitor_consistent(self):
        with tempfile.TemporaryDirectory() as d:
            db = D.Database(os.path.join(d, "e2e.db"))
            pt = PaperTrader(FAV_CFG, capital=1000.0, db=db, session_id="e2e")
            for i, m in enumerate(self.markets):
                pt.run_market(m, market_id=f"mk{i}", ts=i)
            # persistence summary == live portfolio == monitor metrics (all three agree)
            summ = db.session_summary("e2e")
            metr = M.session_metrics(db, "e2e")
            self.assertAlmostEqual(summ["final_cash"], pt.pf.total_cash(), places=6)
            self.assertAlmostEqual(metr["final"], pt.pf.total_cash(), places=6)
            self.assertEqual(metr["rounds"], len(self.markets))
            # equity curve endpoints sane; alerts is a list
            curve = M.equity_curve(db, "e2e")
            self.assertEqual(curve[0], 1000.0)
            self.assertAlmostEqual(curve[-1], pt.pf.total_cash(), places=6)
            self.assertIsInstance(M.session_alerts(db, "e2e"), list)
            db.close()

    def test_report_sections_run(self):
        fav = R.favorite_validation(self.markets, FAV_CFG, folds=3)
        self.assertEqual(len(fav["cv_folds_roi"]), 3)
        self.assertIn("capacity", fav)
        spot = R.spot_validation(count=100)
        self.assertTrue(spot["control_passes"])            # lag=0 -> no spurious edge

    def test_parallel_equals_sequential_on_pipeline_data(self):
        self.assertAlmostEqual(bt.run_parallel(self.markets, FAV_CFG).roi_pct,
                               bt.run_sequential(self.markets, FAV_CFG).roi_pct, places=6)


if __name__ == "__main__":
    unittest.main()
