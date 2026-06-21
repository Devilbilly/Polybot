import os
import tempfile
import unittest
import numpy as np
import pandas as pd

from polybot import database as D


def write_csv(path, n=60, final=0.95, with_l2=True, btc_corrupt=False):
    rem = np.linspace(300, 0, n)
    bid = np.full(n, 0.50); ask = np.full(n, 0.51)
    bid[-10:] = final - 0.01; ask[-10:] = final
    if btc_corrupt:
        bid = np.full(n, 100000.0)   # corrupted log: prices not probabilities
    cols = {"WS_Bid": bid, "WS_Ask": ask, "Bid_P1": bid, "Bid_S1": np.full(n, 500.0),
            "Ask_P1": ask, "Ask_S1": np.full(n, 500.0), "Rem_Float": rem}
    if with_l2:
        for c in ["Bid_P2", "Bid_S2", "Ask_P2", "Ask_S2", "Bid_P3", "Bid_S3", "Ask_P3", "Ask_S3"]:
            cols[c] = np.zeros(n)
    pd.DataFrame(cols).to_csv(path, index=False)


class TestDetermineWinner(unittest.TestCase):
    def test_yes(self):
        self.assertEqual(D.determine_winner(np.array([0.9] * 10)), "YES")

    def test_no(self):
        self.assertEqual(D.determine_winner(np.array([0.1] * 10)), "NO")

    def test_unresolved_midrange(self):
        self.assertIsNone(D.determine_winner(np.array([0.5] * 10)))

    def test_too_short(self):
        self.assertIsNone(D.determine_winner(np.array([0.9, 0.9])))


class TestCsvToArrays(unittest.TestCase):
    def test_valid(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Token_1.csv"); write_csv(p, final=0.95)
            m = D.csv_to_arrays(p)
            self.assertIsNotNone(m)
            self.assertEqual(m["winner"], "YES")
            self.assertEqual(len(m["ws_bid"]), 60)

    def test_short_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Token_1.csv"); write_csv(p, n=10)
            self.assertIsNone(D.csv_to_arrays(p))

    def test_corrupted_btc_prices_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Token_1.csv"); write_csv(p, btc_corrupt=True)
            self.assertIsNone(D.csv_to_arrays(p))   # the 0318-folder failure mode

    def test_missing_l2_filled_zeros(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Token_1.csv"); write_csv(p, with_l2=False)
            m = D.csv_to_arrays(p)
            self.assertTrue(np.all(m["bid_p2"] == 0.0))

    def test_unresolved_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Token_1.csv"); write_csv(p, final=0.50)
            self.assertIsNone(D.csv_to_arrays(p))


class TestDatabase(unittest.TestCase):
    def test_roundtrip_and_import(self):
        with tempfile.TemporaryDirectory() as d:
            csvdir = os.path.join(d, "data"); os.makedirs(csvdir)
            write_csv(os.path.join(csvdir, "Token_42.csv"), final=0.9)
            write_csv(os.path.join(csvdir, "Token_43.csv"), final=0.1)
            db = D.Database(os.path.join(d, "t.db"))
            n = db.import_csv_dir(csvdir)
            self.assertEqual(n, 2)
            self.assertEqual(set(db.market_ids()), {"42", "43"})
            m = db.load_market("42")
            self.assertEqual(m["winner"], "YES")
            self.assertEqual(len(m["ws_bid"]), 60)
            db.set_winner("42", "NO")
            self.assertEqual(db.load_market("42")["winner"], "NO")
            db.close()

    def test_session_log_and_summary(self):
        from polybot.core import RoundResult
        with tempfile.TemporaryDirectory() as d:
            db = D.Database(os.path.join(d, "t.db"))
            db.log_round("s1", RoundResult(1, "YES", 50.0, 1050.0, {"a": {"pnl": 50.0, "cash": 1050.0}}), market_id="m1", ts=100)
            db.log_round("s1", RoundResult(2, "NO", -20.0, 1030.0, {"a": {"pnl": -20.0, "cash": 1030.0}}), market_id="m2", ts=200)
            db.log_round("s1", RoundResult(3, "YES", 0.0, 1030.0, {"a": {"pnl": 0.0, "cash": 1030.0}}), market_id="m3", ts=300)
            s = db.session_summary("s1")
            self.assertEqual(s["rounds"], 3)
            self.assertAlmostEqual(s["final_cash"], 1030.0)
            self.assertAlmostEqual(s["total_pnl"], 30.0)
            self.assertAlmostEqual(s["win_rate_pct"], 50.0)   # 1 win / 2 traded (the 0 doesn't count)
            self.assertIsNone(db.session_summary("missing"))
            # per-strategy rows persisted too
            n = db.conn.execute("SELECT COUNT(*) FROM session_strategy WHERE session_id='s1'").fetchone()[0]
            self.assertEqual(n, 3)
            db.close()

    def test_upsert_updates(self):
        with tempfile.TemporaryDirectory() as d:
            db = D.Database(os.path.join(d, "t.db"))
            db.upsert_market("x", slug="s1", n_ticks=5)
            db.upsert_market("x", winner="YES", n_ticks=9)
            row = db.conn.execute("SELECT slug,winner,n_ticks FROM markets WHERE market_id='x'").fetchone()
            self.assertEqual(row, ("s1", "YES", 9))
            db.close()


if __name__ == "__main__":
    unittest.main()
