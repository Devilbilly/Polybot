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

    def test_ws_best_bid_ask_price_changes_schema(self):
        # the REAL 2026 schema: per-asset quotes nested in a price_changes array
        msg = {"event_type": "price_change", "market": "0xabc", "price_changes": [
            {"asset_id": "NO_TOK", "best_bid": "0.02", "best_ask": "0.03"},
            {"asset_id": "YES_TOK", "best_bid": "0.97", "best_ask": "0.98"}]}
        self.assertEqual(R.ws_best_bid_ask(msg, "YES_TOK"), (0.97, 0.98))
        self.assertEqual(R.ws_best_bid_ask(msg, "NO_TOK"), (0.02, 0.03))

    def test_ws_best_bid_ask_absent_token_returns_zero(self):
        msg = {"price_changes": [{"asset_id": "OTHER", "best_bid": "0.5", "best_ask": "0.51"}]}
        self.assertEqual(R.ws_best_bid_ask(msg, "MINE"), (0.0, 0.0))
        self.assertEqual(R.ws_best_bid_ask({}, "MINE"), (0.0, 0.0))
        self.assertEqual(R.ws_best_bid_ask([], "MINE"), (0.0, 0.0))

    def test_best_bid_ask_prefers_rest_book(self):
        # the REST book (bid_p1/ask_p1) is authoritative; the WS msg is stale -> must use the book
        book = {"bid_p1": 0.74, "ask_p1": 0.76, "ask_s1": 100}
        stale_msg = {"price_changes": [{"asset_id": "T", "best_bid": "0.50", "best_ask": "0.51"}]}
        self.assertEqual(R.best_bid_ask(book, stale_msg, "T"), (0.74, 0.76))

    def test_best_bid_ask_falls_back_to_ws_when_book_empty(self):
        empty_book = {"bid_p1": 0.0, "ask_p1": 0.0}
        msg = {"price_changes": [{"asset_id": "T", "best_bid": "0.90", "best_ask": "0.91"}]}
        self.assertEqual(R.best_bid_ask(empty_book, msg, "T"), (0.90, 0.91))
        self.assertEqual(R.best_bid_ask({}, None, None), (0.0, 0.0))

    def test_carry_book_keeps_last_when_fresh_empty(self):
        good = {"bid_p1": 0.74, "ask_p1": 0.76}
        # a fresh empty fetch (rate-limited -> {}) must NOT clobber the last good book
        self.assertEqual(R.carry_book({}, good), good)
        self.assertEqual(R.carry_book({"bid_p1": 0.0, "ask_p1": 0.0}, good), good)
        # a fresh good fetch replaces it
        newer = {"bid_p1": 0.80, "ask_p1": 0.82}
        self.assertEqual(R.carry_book(newer, good), newer)
        # one-sided fresh book still counts as real (not empty)
        self.assertEqual(R.carry_book({"bid_p1": 0.9, "ask_p1": 0.0}, good), {"bid_p1": 0.9, "ask_p1": 0.0})
        # no prior book + empty fresh -> empty dict (best_bid_ask then uses WS for that one tick)
        self.assertEqual(R.carry_book({}, {}), {})

    def test_ws_best_bid_ask_book_snapshot(self):
        # initial 'book' snapshot (list-wrapped): best bid = highest, best ask = lowest
        snap = [{"asset_id": "T", "bids": [{"price": "0.70", "size": "1"}, {"price": "0.72", "size": "2"}],
                 "asks": [{"price": "0.75", "size": "1"}, {"price": "0.74", "size": "2"}]}]
        self.assertEqual(R.ws_best_bid_ask(snap, "T"), (0.72, 0.74))

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


class TestMarketDiscovery(unittest.TestCase):
    """Discovery parsing is folded INTO the live bot (not a side script); these pin the pure
    helpers so a changed-slug fallback is a localised fix, not a silent overnight hang."""

    def test_listing_rows_handles_list_and_wrapped(self):
        self.assertEqual(LV._listing_rows([{"a": 1}, "x", {"b": 2}]), [{"a": 1}, {"b": 2}])
        self.assertEqual(LV._listing_rows({"data": [{"a": 1}]}), [{"a": 1}])
        self.assertEqual(LV._listing_rows({"events": [{"e": 1}]}), [{"e": 1}])
        self.assertEqual(LV._listing_rows({"nope": 1}), [])
        self.assertEqual(LV._listing_rows(None), [])

    def test_is_btc_5m_matches_btc_updown_only(self):
        self.assertTrue(LV._is_btc_5m({"slug": "btc-updown-5m-1700000000", "title": "BTC Up or Down"}))
        self.assertTrue(LV._is_btc_5m({"title": "Bitcoin Up/Down 5 min"}))
        self.assertFalse(LV._is_btc_5m({"slug": "eth-updown-5m-1", "title": "ETH up or down"}))
        self.assertFalse(LV._is_btc_5m({"title": "Bitcoin above $100k by 2027"}))   # btc but not 5m up/down

    def test_token_from_row_event_and_flat_shapes(self):
        # event shape (nested markets, like extract_token)
        ev_row = {"markets": [{"clobTokenIds": '["TKA","TKB"]'}]}
        self.assertEqual(LV._token_from_row(ev_row), "TKA")
        # flat market shape (clobTokenIds on the row itself, list and json-string)
        self.assertEqual(LV._token_from_row({"clobTokenIds": ["TK1", "TK2"]}), "TK1")
        self.assertEqual(LV._token_from_row({"clobTokenIds": '["TK9"]'}), "TK9")
        self.assertIsNone(LV._token_from_row({"foo": 1}))

    def test_market_end_ts_from_slug_timestamp(self):
        # historical format: unix ts in the slug -> end = ts + 300
        self.assertEqual(LV.market_end_ts("btc-updown-5m-1700000000", {}), 1700000000 + 300)

    def test_market_end_ts_from_iso_enddate(self):
        ts = LV.market_end_ts("some-named-slug", {"endDate": "2026-06-22T00:05:00Z"})
        self.assertIsNotNone(ts)
        from datetime import datetime, timezone
        self.assertEqual(ts, int(datetime(2026, 6, 22, 0, 5, tzinfo=timezone.utc).timestamp()))

    def test_market_end_ts_none_when_unavailable(self):
        self.assertIsNone(LV.market_end_ts("named-slug-no-ts", {"title": "x"}))

    def test_is_updown_market_matches_any_crypto(self):
        self.assertTrue(LV._is_updown_market({"slug": "eth-updown-5m-1700000000", "title": "ETH Up or Down"}))
        self.assertTrue(LV._is_updown_market({"slug": "sol-updown-5m-1", "title": "Solana up or down"}))
        self.assertTrue(LV._is_updown_market({"title": "XRP up/down 5 min"}))
        self.assertTrue(LV._is_updown_market({"slug": "btc-updown-5m-1", "title": "Bitcoin Up or Down"}))

    def test_is_updown_market_rejects_non_updown(self):
        self.assertFalse(LV._is_updown_market({"slug": "fed-rate-hike", "title": "Will the Fed hike?"}))
        self.assertFalse(LV._is_updown_market({"title": "Ethereum above $5000 by 2027"}))  # crypto but not up/down
        self.assertFalse(LV._is_updown_market({"title": "Election up or down ballot"}))     # up/down but not crypto


if __name__ == "__main__":
    unittest.main()
