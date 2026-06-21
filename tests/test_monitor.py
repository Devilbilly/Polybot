import os
import tempfile
import unittest
from polybot.database import Database
from polybot.core import RoundResult
from polybot import monitor as M


def _round(n, pnl, cash, strat="a"):
    return RoundResult(n, "YES" if pnl >= 0 else "NO", pnl, cash, {strat: {"pnl": pnl, "cash": cash}})


class TestMonitor(unittest.TestCase):
    def _db_with_session(self, pnls, sid="s", start=1000.0):
        d = tempfile.mkdtemp()
        db = Database(os.path.join(d, "t.db"))
        cash = start
        for i, p in enumerate(pnls, 1):
            cash += p
            db.log_round(sid, _round(i, p, cash))
        return db

    def test_equity_curve(self):
        db = self._db_with_session([50.0, -20.0, 30.0])
        self.assertEqual(M.equity_curve(db, "s"), [1000.0, 1050.0, 1030.0, 1060.0])
        db.close()

    def test_metrics(self):
        db = self._db_with_session([100.0, -50.0, 25.0])   # 1000 -> 1100 -> 1050 -> 1075
        m = M.session_metrics(db, "s")
        self.assertEqual(m["rounds"], 3)
        self.assertAlmostEqual(m["final"], 1075.0)
        self.assertAlmostEqual(m["roi_pct"], 7.5)
        # peak 1100, trough 1050 -> max dd ~4.5%
        self.assertAlmostEqual(m["max_dd_pct"], (1100 - 1050) / 1100 * 100, places=3)
        self.assertAlmostEqual(m["win_rate_pct"], 2 / 3 * 100, places=3)
        self.assertEqual(m["per_strategy_pnl"]["a"], 75.0)
        db.close()

    def test_per_strategy_roi_unconfounded_by_weight(self):
        # two sleeves: A starts $400, B starts $100; BOTH gain 10%/round -> equal ROI%, unequal $.
        import os, tempfile
        from polybot.database import Database
        from polybot.core import RoundResult
        with tempfile.TemporaryDirectory() as d:
            db = Database(os.path.join(d, "t.db"))
            ca, cb = 400.0, 100.0
            for i in range(1, 4):
                pa, pb = ca * 0.1, cb * 0.1
                ca += pa; cb += pb
                db.log_round("s", RoundResult(i, "YES", pa + pb, ca + cb,
                             {"A": {"pnl": pa, "cash": ca}, "B": {"pnl": pb, "cash": cb}}))
            m = M.session_metrics(db, "s")
            self.assertGreater(m["per_strategy_pnl"]["A"], m["per_strategy_pnl"]["B"])      # $ confounded
            self.assertAlmostEqual(m["per_strategy_roi"]["A"], m["per_strategy_roi"]["B"], places=2)  # ROI% equal
            self.assertAlmostEqual(m["per_strategy_roi"]["A"], 33.1, places=1)
            db.close()

    def test_missing_session(self):
        db = self._db_with_session([10.0])
        self.assertIsNone(M.session_metrics(db, "nope"))
        self.assertEqual(M.equity_curve(db, "nope"), [])
        db.close()

    def test_longest_losing_streak(self):
        db = self._db_with_session([10.0, -5.0, -5.0, -5.0, 10.0, -5.0])
        self.assertEqual(M.session_metrics(db, "s")["longest_losing_streak"], 3)
        db.close()

    def test_alerts_fire(self):
        # a big drawdown should trip the kill-switch + drawdown + underwater alerts
        db = self._db_with_session([100.0, -500.0])   # 1000 -> 1100 -> 600  (-45% from peak)
        alerts = M.session_alerts(db, "s")
        self.assertTrue(any("KILL-SWITCH" in a for a in alerts))
        self.assertTrue(any("UNDERWATER" in a for a in alerts))
        db.close()

    def test_no_alerts_when_healthy(self):
        db = self._db_with_session([10.0, 10.0, 10.0])
        self.assertEqual(M.session_alerts(db, "s"), [])
        db.close()


if __name__ == "__main__":
    unittest.main()
