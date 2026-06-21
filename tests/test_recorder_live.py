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

    def test_winner_from_recent_median(self):
        self.assertEqual(R.winner_from_recent([0.9, 0.92, 0.95, 0.96, 0.94]), "YES")
        self.assertEqual(R.winner_from_recent([0.1, 0.08, 0.05]), "NO")
        self.assertEqual(R.winner_from_recent([0.95, 0.96, 0.97, 0.98, 0.02]), "YES")  # blip ignored
        self.assertIsNone(R.winner_from_recent([]))

    def test_extract_token_clobtokenids_as_json_string(self):
        # Polymarket commonly returns clobTokenIds as a JSON-encoded STRING
        ev = [{"markets": [{"clobTokenIds": '["TOKEN_YES", "TOKEN_NO"]'}]}]
        self.assertEqual(R.extract_token(ev), "TOKEN_YES")

    def test_extract_token_clobtokenids_as_list(self):
        ev = [{"markets": [{"clobTokenIds": ["TKA", "TKB"]}]}]
        self.assertEqual(R.extract_token(ev), "TKA")

    def test_extract_token_skips_market_without_ids_then_finds_next(self):
        ev = [{"markets": [{"foo": 1}, {"clobTokenIds": ["TK2"]}]}]
        self.assertEqual(R.extract_token(ev), "TK2")

    def test_extract_token_handles_missing_malformed_payloads(self):
        self.assertIsNone(R.extract_token(None))
        self.assertIsNone(R.extract_token([]))
        self.assertIsNone(R.extract_token([{"markets": []}]))
        self.assertIsNone(R.extract_token([{"markets": [{"clobTokenIds": ""}]}]))   # empty string
        self.assertIsNone(R.extract_token([{"markets": [{"clobTokenIds": "not-json"}]}]))
        self.assertIsNone(R.extract_token([{"no_markets_key": 1}]))
        self.assertIsNone(R.extract_token([42]))                                     # ev[0] not a dict

    def test_extract_token_is_shared_by_live(self):
        # live.py must import the SAME helper (no drift between the two loops)
        self.assertIs(LV.extract_token, R.extract_token)


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
