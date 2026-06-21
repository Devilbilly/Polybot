import unittest
from polybot.core import (Tick, Position, Order, ExecutionEngine, RiskGovernor,
                          Portfolio, RoundResult)
from polybot.strategies import FavConvergence, NoOp


def tick(ap1=0.80, as1=1000.0, bp1=0.79, bs1=1000.0, ws_bid=None, ws_ask=None, tp=0.6):
    return Tick(ts="t", time_progress=tp, ws_bid=ws_bid if ws_bid is not None else bp1,
                ws_ask=ws_ask if ws_ask is not None else ap1,
                bid_p=(bp1, 0, 0), bid_s=(bs1, 0, 0), ask_p=(ap1, 0, 0), ask_s=(as1, 0, 0))


class TestTick(unittest.TestCase):
    def test_l1_accessors(self):
        t = tick(ap1=0.8, as1=50, bp1=0.7, bs1=40)
        self.assertEqual((t.ap1, t.as1, t.bp1, t.bs1), (0.8, 50, 0.7, 40))

    def test_no_side_prices(self):
        t = tick(ap1=0.8, bp1=0.7)
        self.assertAlmostEqual(t.no_ask, 1 - 0.7)   # buy NO consumes YES bid
        self.assertAlmostEqual(t.no_bid, 1 - 0.8)


class TestPosition(unittest.TestCase):
    def test_reset_and_equity(self):
        p = Position(cash=100.0, inv_yes=10, inv_no=5)
        eq = p.equity(tick(ws_bid=0.6, ws_ask=0.65))
        self.assertAlmostEqual(eq, 100 + 10 * 0.6 + 5 * (1 - 0.65))
        p.reset_market()
        self.assertEqual((p.inv_yes, p.inv_no, p.n_entries), (0, 0, 0))
        self.assertEqual(p.start_cash, 100.0)


class TestExecutionEngine(unittest.TestCase):
    def setUp(self):
        self.eng = ExecutionEngine(fee=0.001, slippage=0.002)

    def test_buy_yes_math(self):
        p = Position(cash=1000.0)
        ok = self.eng.execute(Order("YES", "BUY", 20.0), tick(ap1=0.80, as1=1000), p)
        self.assertTrue(ok)
        exec_p = 0.802
        self.assertAlmostEqual(p.inv_yes, 20.0 / (exec_p * 1.001), places=4)
        self.assertAlmostEqual(p.cash, 1000.0 - 20.0, places=3)
        self.assertEqual(p.n_entries, 1)

    def test_buy_no_uses_one_minus_bid(self):
        p = Position(cash=1000.0)
        # NO ask = 1 - bp1 = 1 - 0.25 = 0.75
        ok = self.eng.execute(Order("NO", "BUY", 20.0), tick(bp1=0.25, bs1=1000), p)
        self.assertTrue(ok)
        self.assertGreater(p.inv_no, 0)
        self.assertEqual(p.yes_bought, 0.0)

    def test_buy_capped_by_book_size(self):
        p = Position(cash=1000.0)
        self.eng.execute(Order("YES", "BUY", 500.0), tick(ap1=0.80, as1=5.0), p)
        self.assertAlmostEqual(p.inv_yes, 5.0)   # capped at resting size

    def test_buy_rejected_below_one_token(self):
        p = Position(cash=1000.0)
        ok = self.eng.execute(Order("YES", "BUY", 0.10), tick(ap1=0.80), p)  # 0.10/0.8 < 1
        self.assertFalse(ok)
        self.assertEqual(p.inv_yes, 0.0)

    def test_buy_rejected_invalid_price(self):
        p = Position(cash=1000.0)
        self.assertFalse(self.eng.execute(Order("YES", "BUY", 50), tick(ap1=0.0, as1=10), p))
        self.assertFalse(self.eng.execute(Order("YES", "BUY", 50), tick(ap1=1.0, as1=10), p))

    def test_sell_yes(self):
        p = Position(cash=0.0, inv_yes=100.0)
        ok = self.eng.execute(Order("YES", "SELL"), tick(bp1=0.90, bs1=1000), p)
        self.assertTrue(ok)
        self.assertAlmostEqual(p.inv_yes, 0.0)
        self.assertAlmostEqual(p.cash, 100.0 * (0.90 - 0.002) * 0.999, places=4)

    def test_sell_no(self):
        p = Position(cash=0.0, inv_no=100.0)
        ok = self.eng.execute(Order("NO", "SELL"), tick(ap1=0.10), p)  # no_bid = 1-0.10=0.90
        self.assertTrue(ok)
        self.assertAlmostEqual(p.inv_no, 0.0)

    def test_sell_capped_by_size(self):
        p = Position(cash=0.0, inv_yes=100.0)
        self.eng.execute(Order("YES", "SELL"), tick(bp1=0.9, bs1=30.0), p)
        self.assertAlmostEqual(p.inv_yes, 70.0)

    def test_sell_rejected_no_inventory(self):
        p = Position(cash=0.0)
        self.assertFalse(self.eng.execute(Order("YES", "SELL"), tick(), p))
        self.assertFalse(self.eng.execute(Order("NO", "SELL"), tick(), p))

    def test_no_cap_mode(self):
        eng = ExecutionEngine(cap_fills=False)
        p = Position(cash=1000.0)
        eng.execute(Order("YES", "BUY", 500.0), tick(ap1=0.80, as1=5.0), p)
        self.assertGreater(p.inv_yes, 100)   # ignores tiny book size

    def _deep_tick(self):
        return Tick(ts="t", time_progress=0.6, ws_bid=0.79, ws_ask=0.80,
                    bid_p=(0.79, 0, 0), bid_s=(0, 0, 0),
                    ask_p=(0.80, 0.82, 0.84), ask_s=(10, 10, 10))

    def test_walk_book_consumes_deeper_levels(self):
        eng = ExecutionEngine(fee=0.0, slippage=0.0, walk_book=True)
        p = Position(cash=10000.0)
        eng.execute(Order("YES", "BUY", 100.0), self._deep_tick(), p)  # > L1 size
        self.assertAlmostEqual(p.inv_yes, 30.0)        # walked L1+L2+L3 (10 each)

    def test_no_walk_book_l1_only(self):
        eng = ExecutionEngine(fee=0.0, slippage=0.0, walk_book=False)
        p = Position(cash=10000.0)
        eng.execute(Order("YES", "BUY", 100.0), self._deep_tick(), p)
        self.assertAlmostEqual(p.inv_yes, 10.0)        # only L1 consumed

    def test_walk_book_worse_average_price(self):
        eng = ExecutionEngine(fee=0.0, slippage=0.0, walk_book=True)
        p = Position(cash=10000.0)
        eng.execute(Order("YES", "BUY", 100.0), self._deep_tick(), p)
        avg = (10000.0 - p.cash) / p.inv_yes
        self.assertGreater(avg, 0.80)                  # paid worse than L1 price


class TestRiskGovernor(unittest.TestCase):
    def test_normal_allows(self):
        r = RiskGovernor(1000.0)
        r.new_market(1000.0)
        self.assertTrue(r.allow_entries(1000.0, 1000.0))

    def test_depletion_halt(self):
        r = RiskGovernor(1000.0, min_capital=50.0)
        self.assertFalse(r.allow_entries(40.0, 40.0))
        self.assertTrue(r.halted)

    def test_kill_switch_sticky(self):
        r = RiskGovernor(1000.0, kill_switch_dd=0.25)
        r.allow_entries(1000.0, 1000.0)            # peak=1000
        self.assertFalse(r.allow_entries(700.0, 700.0))   # -30% > 25%
        self.assertTrue(r.killed)
        self.assertFalse(r.allow_entries(999.0, 999.0))   # stays killed

    def test_round_loss_limit(self):
        r = RiskGovernor(1000.0, round_loss_limit=0.05)
        r.new_market(1000.0)
        self.assertTrue(r.allow_entries(970.0, 970.0))    # -3% ok
        self.assertFalse(r.allow_entries(940.0, 940.0))   # -6% > 5% blocks


class TestPortfolio(unittest.TestCase):
    def _pf(self):
        return Portfolio([FavConvergence("a", {}), NoOp("b", {})], [0.5, 0.5], total_capital=1000.0)

    def test_settle_pays_winner(self):
        pf = self._pf()
        pf.new_market()
        pf.accounts[0].inv_yes = 100.0
        pf.accounts[0].cash = 0.0
        pf.accounts[0].start_cash = 50.0
        res = pf.settle(winner_yes=True)
        self.assertIsInstance(res, RoundResult)
        self.assertEqual(res.winner, "YES")
        # winnings persist (cash compounds); reset_market clears POSITIONS, not cash
        self.assertAlmostEqual(pf.accounts[0].cash, 100.0)
        self.assertAlmostEqual(pf.accounts[0].inv_yes, 0.0)
        self.assertAlmostEqual(res.per_strategy["a"]["pnl"], 100.0 - 50.0)

    def test_settle_no_winner_pays_no_side(self):
        pf = self._pf()
        pf.new_market()
        pf.accounts[0].inv_no = 80.0; pf.accounts[0].cash = 0.0; pf.accounts[0].start_cash = 0.0
        res = pf.settle(winner_yes=False)
        self.assertEqual(res.winner, "NO")
        self.assertAlmostEqual(res.per_strategy["a"]["pnl"], 80.0)

    def test_new_market_resets(self):
        pf = self._pf()
        pf.accounts[0].inv_yes = 5
        pf.new_market()
        self.assertEqual(pf.accounts[0].inv_yes, 0)

    def test_process_tick_blocked_entries_when_killed(self):
        pf = self._pf()
        pf.new_market()
        pf.risk.killed = True
        pf.process_tick(tick(ap1=0.80, tp=0.9))   # FAV would buy, but entries blocked
        self.assertEqual(pf.accounts[0].inv_yes, 0.0)


if __name__ == "__main__":
    unittest.main()
