import unittest
from polybot.core import Tick, Position, ExecutionEngine
from polybot import strategies as S


def tick(ap1=0.80, bp1=0.79, as1=1000, bs1=1000, ws_bid=None, ws_ask=None, tp=0.6):
    return Tick(ts="t", time_progress=tp,
                ws_bid=ws_bid if ws_bid is not None else bp1,
                ws_ask=ws_ask if ws_ask is not None else ap1,
                bid_p=(bp1, 0, 0), bid_s=(bs1, 0, 0), ask_p=(ap1, 0, 0), ask_s=(as1, 0, 0))


class TestRegistry(unittest.TestCase):
    def test_get_and_available(self):
        self.assertIn("fav_convergence", S.available())
        self.assertIsInstance(S.get_strategy("noop", {}), S.NoOp)

    def test_unknown_raises(self):
        with self.assertRaises(KeyError):
            S.get_strategy("does_not_exist", {})


class TestFavConvergence(unittest.TestCase):
    def setUp(self):
        self.s = S.FavConvergence("fav", {"buy_p": 0.70, "sell_p": 0.93,
                                          "time_cutoff": 0.50, "stop_p": 0.50,
                                          "max_buy": 1, "bullet_pct": 0.02})

    def test_buys_yes_favorite_in_band_late(self):
        orders = self.s.decide(tick(ap1=0.80, tp=0.6), Position(cash=1000))
        self.assertEqual(len(orders), 1)
        self.assertEqual((orders[0].side, orders[0].kind), ("YES", "BUY"))
        self.assertAlmostEqual(orders[0].usd, 20.0)

    def test_buys_no_favorite(self):
        # NO ask = 1 - bp1 = 1-0.20 = 0.80 in band; YES ask 0.21 NOT in band
        orders = self.s.decide(tick(ap1=0.21, bp1=0.20, tp=0.6), Position(cash=1000))
        self.assertEqual(orders[0].side, "NO")

    def test_no_entry_when_early(self):
        self.assertEqual(self.s.decide(tick(ap1=0.80, tp=0.3), Position(cash=1000)), [])

    def test_no_entry_out_of_band(self):
        self.assertEqual(self.s.decide(tick(ap1=0.55, bp1=0.54, tp=0.6), Position(cash=1000)), [])
        self.assertEqual(self.s.decide(tick(ap1=0.99, bp1=0.98, tp=0.6), Position(cash=1000)), [])

    def test_single_entry_cap(self):
        p = Position(cash=1000, n_entries=1)   # already entered
        self.assertEqual([o for o in self.s.decide(tick(ap1=0.80, tp=0.6), p) if o.kind == "BUY"], [])

    def test_reversal_stop_sells_yes(self):
        p = Position(cash=0, inv_yes=100)
        orders = self.s.decide(tick(ws_bid=0.40, ap1=0.80, tp=0.8), p)   # ws_bid<0.5 stop
        self.assertTrue(any(o.side == "YES" and o.kind == "SELL" for o in orders))

    def test_reversal_stop_sells_no(self):
        p = Position(cash=0, inv_no=100)
        # NO sellable = 1 - ws_ask; ws_ask=0.60 -> 0.40 < 0.50 stop
        orders = self.s.decide(tick(ws_ask=0.60, bp1=0.79, tp=0.8), p)
        self.assertTrue(any(o.side == "NO" and o.kind == "SELL" for o in orders))

    def test_no_stop_above_threshold(self):
        p = Position(cash=0, inv_yes=100)
        orders = self.s.decide(tick(ws_bid=0.80, ap1=0.85, tp=0.8), p)
        self.assertFalse(any(o.kind == "SELL" for o in orders))


class TestFavHoldAndNoOp(unittest.TestCase):
    def test_fav_hold_has_no_stop(self):
        s = S.FavHold("h", {"stop_p": 0.50})
        self.assertEqual(s.stop, 0.0)
        # already entered (n_entries=1) so no new buy; crashing price must NOT trigger a SELL
        p = Position(cash=0, inv_yes=100, n_entries=1)
        self.assertEqual(s.decide(tick(ws_bid=0.10, tp=0.9), p), [])   # never stops

    def test_noop_never_trades(self):
        self.assertEqual(S.NoOp("n", {}).decide(tick(tp=0.9), Position(cash=1000)), [])


if __name__ == "__main__":
    unittest.main()
