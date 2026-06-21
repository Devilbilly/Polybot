import os
import tempfile
import unittest

from polybot.paper import PaperTrader, PaperReport, paper_trade
from tests.helpers import make_market, make_reversal_market

CFG = {
    "strategies": [{"id": "m", "name": "fav_convergence", "weight": 1.0,
                    "params": {"buy_p": 0.70, "sell_p": 0.93, "time_cutoff": 0.50,
                               "stop_p": 0.50, "max_buy": 1, "bullet_pct": 0.02}}],
    "engine": {"fee": 0.001, "slippage": 0.002, "cap_fills": True},
    "risk": {"kill_switch_dd": 0.25, "round_loss_limit": 0.08, "min_capital": 50.0},
}
NOOP = {"strategies": [{"id": "n", "name": "noop", "weight": 1.0}], "engine": {}, "risk": {}}


class TestPaperTrader(unittest.TestCase):
    def test_winning_markets_compound(self):
        markets = [make_market(fav="YES") for _ in range(10)]
        rep = PaperTrader(CFG, capital=1000.0).run(markets)
        self.assertIsInstance(rep, PaperReport)
        self.assertEqual(rep.rounds, 10)
        self.assertGreater(rep.final, 1000.0)        # favorites won -> profit
        self.assertEqual(len(rep.equity_curve), 11)  # start + each round

    def test_noop_is_flat(self):
        rep = PaperTrader(NOOP, capital=1000.0).run([make_market() for _ in range(5)])
        self.assertAlmostEqual(rep.final, 1000.0)
        self.assertEqual(rep.roi_pct, 0.0)
        self.assertEqual(rep.win_rate_pct, 0.0)

    def test_reversal_loses_but_survives(self):
        rep = PaperTrader(CFG, capital=1000.0).run([make_reversal_market() for _ in range(5)])
        self.assertLess(rep.final, 1000.0)           # losing markets
        self.assertGreater(rep.final, 0.0)           # stop prevents ruin

    def test_state_persists_across_markets(self):
        pt = PaperTrader(CFG, capital=1000.0)
        pt.run_market(make_market(fav="YES"))
        after_one = pt.pf.total_cash()
        pt.run_market(make_market(fav="YES"))
        self.assertNotEqual(after_one, pt.pf.total_cash())   # cash compounded

    def test_report_str(self):
        rep = PaperTrader(CFG).run([make_market(fav="YES")])
        self.assertIn("PAPER SESSION", str(rep))

    def test_run_market_empty_returns_none(self):
        a = make_market(n=60)
        a["ws_bid"][:] = 0.0; a["ws_ask"][:] = 0.0   # no valid ticks
        self.assertIsNone(PaperTrader(CFG).run_market(a))


class TestPaperPersistence(unittest.TestCase):
    def test_session_persisted_to_db(self):
        import os, tempfile
        from polybot.database import Database
        with tempfile.TemporaryDirectory() as d:
            db = Database(os.path.join(d, "t.db"))
            pt = PaperTrader(CFG, capital=1000.0, db=db, session_id="run42")
            for i, m in enumerate([make_market(fav="YES") for _ in range(6)]):
                pt.run_market(m, market_id=f"mk{i}", ts=i)
            summary = db.session_summary("run42")
            self.assertEqual(summary["rounds"], 6)
            # the persisted final cash matches the live portfolio cash
            self.assertAlmostEqual(summary["final_cash"], pt.pf.total_cash(), places=6)
            db.close()


class TestPaperTradeFn(unittest.TestCase):
    def test_from_market_list(self):
        rep = paper_trade([make_market(fav="YES") for _ in range(8)], CFG)
        self.assertGreater(rep.final, 1000.0)

    def test_from_csv_dir(self):
        from tests.test_database import write_csv
        with tempfile.TemporaryDirectory() as d:
            csvdir = os.path.join(d, "data"); os.makedirs(csvdir)
            for i in range(3):
                write_csv(os.path.join(csvdir, f"Token_{i}.csv"), final=0.95)
            rep = paper_trade(csvdir, CFG)
            self.assertEqual(rep.rounds, 3)


if __name__ == "__main__":
    unittest.main()
