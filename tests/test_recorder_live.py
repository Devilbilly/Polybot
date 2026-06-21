import unittest
from polybot import recorder as R
from polybot import live as LV


class TestRecorderHelpers(unittest.TestCase):
    def test_predicted_slugs(self):
        slugs = R.predicted_slugs(1000_000_000 + 123)   # not a 300-multiple
        base = (1000_000_000 + 123) - ((1000_000_000 + 123) % 300)
        self.assertEqual(slugs, [f"btc-updown-5m-{base}", f"btc-updown-5m-{base+300}"])

    def test_parse_book_full(self):
        book = {"bids": [{"price": "0.70", "size": "10"}, {"price": "0.69", "size": "20"}],
                "asks": [{"price": "0.72", "size": "5"}, {"price": "0.73", "size": "8"}]}
        rec = R.parse_book(book)
        self.assertEqual(rec["bid_p1"], 0.70)   # highest bid first
        self.assertEqual(rec["bid_p2"], 0.69)
        self.assertEqual(rec["ask_p1"], 0.72)   # lowest ask first
        self.assertEqual(rec["ask_s2"], 8.0)

    def test_parse_book_empty(self):
        rec = R.parse_book({})
        self.assertEqual(rec["bid_p1"], 0.0)
        self.assertEqual(rec["ask_s1"], 0.0)

    def test_parse_book_one_side(self):
        rec = R.parse_book({"bids": [{"price": "0.6", "size": "3"}]})
        self.assertEqual(rec["bid_p1"], 0.6)
        self.assertEqual(rec["ask_p1"], 0.0)

    def test_build_tick_row(self):
        book = R.parse_book({"bids": [{"price": "0.6", "size": "3"}], "asks": []})
        row = R.build_tick_row(120.0, 0.6, 0.62, book)
        self.assertEqual(row["rem"], 120.0)
        self.assertEqual(row["ws_bid"], 0.6)
        self.assertEqual(row["bid_p1"], 0.6)

    def test_winner_from_last(self):
        self.assertEqual(R.winner_from_last(0.9), "YES")
        self.assertEqual(R.winner_from_last(0.1), "NO")
        self.assertIsNone(R.winner_from_last(0.0))


class TestLiveHelpers(unittest.TestCase):
    def test_build_portfolio_unique_ids(self):
        cfg = {"strategies": [
            {"name": "fav_convergence", "weight": 0.5},
            {"name": "fav_convergence", "weight": 0.5}],
            "engine": {}, "risk": {}}
        pf = LV.build_portfolio(cfg)
        names = [s.name for s in pf.strategies]
        self.assertEqual(len(set(names)), 2)   # no collision

    def test_live_tick_time_progress(self):
        t = LV.live_tick(rem=150.0, ws_bid=0.8, ws_ask=0.82,
                         book={"bid_p1": 0.8, "ask_p1": 0.82, "ask_s1": 100})
        self.assertAlmostEqual(t.time_progress, 0.5)
        self.assertEqual(t.ap1, 0.82)
        self.assertEqual(t.as1, 100)


if __name__ == "__main__":
    unittest.main()
