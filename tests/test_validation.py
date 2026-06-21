import unittest
from polybot.core import Strategy, Order, Position, Portfolio, Tick, LookaheadError
from polybot import validation as V
from polybot.strategies import get_strategy, NoOp
from tests.helpers import make_market


class _FutureCheat(Strategy):
    """Deliberately non-causal: peeks 5 ticks AHEAD in the price series. Must be CAUGHT."""
    def __init__(self, name, params, market):
        super().__init__(name, params)
        self._fut = market["ws_bid"]
        self._i = -1

    def decide(self, tick, pos):
        self._i += 1
        j = self._i + 5
        if j < len(self._fut) and self._fut[j] >= 0.84:   # future knowledge
            return [Order("YES", "BUY", 10.0)]
        return []


class TestCausality(unittest.TestCase):
    def setUp(self):
        self.market = make_market(n=200, fav="YES")

    def test_real_strategies_are_causal(self):
        results = V.check_all_strategies_causal()      # non-vacuous both-edges gate market
        self.assertTrue(all(results.values()), results)
        self.assertIn("scale_in_favorite", results)
        self.assertIn("btc_spot_divergence", results)

    def test_gate_market_is_non_vacuous_for_spot(self):
        # the registry-wide causal gate must make btc_spot_divergence actually TRADE,
        # else a future-peeking version would pass with a trivially-empty decision log.
        from polybot.strategies import get_strategy
        mk = V._gate_market()
        log = V._decision_log(
            lambda mv: get_strategy("btc_spot_divergence",
                                    {"vol": 0.0006, "edge": 0.04, "window": 300, "time_cutoff": 0.0, "max_buy": 1}),
            mk)
        self.assertTrue(any(orders for orders in log), "spot strategy must trade on the gate market")

    def test_cheating_strategy_is_caught(self):
        with self.assertRaises(LookaheadError):
            V.assert_causal(lambda mv: _FutureCheat("cheat", {}, mv), self.market)

    def test_noop_causal(self):
        self.assertTrue(V.assert_causal(lambda mv: NoOp("n", {}), self.market))

    def test_spot_strategy_causal_while_trading(self):
        # the spot strategy actively trades on synthetic spot data and must still be causal
        import random
        from polybot.synth import synth_market
        m = synth_market(random.Random(0), n=200, lag=5)
        params = {"vol": 0.0006, "edge": 0.04, "window": 300, "time_cutoff": 0.0,
                  "max_buy": 1, "bullet_pct": 0.02}
        self.assertTrue(V.assert_causal(lambda mv: get_strategy("btc_spot_divergence", params), m))


class TestRuntimeGuard(unittest.TestCase):
    def _pf(self):
        return Portfolio([get_strategy("noop", {})], [1.0], total_capital=1000.0)

    def _tick(self, tp):
        return Tick(ts="t", time_progress=tp, ws_bid=0.8, ws_ask=0.81,
                    bid_p=(0.8, 0, 0), bid_s=(10, 0, 0), ask_p=(0.81, 0, 0), ask_s=(10, 0, 0))

    def test_backward_time_raises(self):
        pf = self._pf(); pf.new_market()
        pf.process_tick(self._tick(0.5))
        with self.assertRaises(LookaheadError):
            pf.process_tick(self._tick(0.3))    # time went backward

    def test_forward_time_ok(self):
        pf = self._pf(); pf.new_market()
        pf.process_tick(self._tick(0.2))
        pf.process_tick(self._tick(0.2))        # equal is allowed
        pf.process_tick(self._tick(0.6))        # forward ok

    def test_new_market_resets_clock(self):
        pf = self._pf(); pf.new_market()
        pf.process_tick(self._tick(0.9))
        pf.new_market()                          # resets
        pf.process_tick(self._tick(0.1))         # fine — new market

    def test_can_disable_guard(self):
        pf = Portfolio([get_strategy("noop", {})], [1.0], strict_time=False)
        pf.new_market()
        pf.process_tick(self._tick(0.9))
        pf.process_tick(self._tick(0.1))         # no raise when disabled


if __name__ == "__main__":
    unittest.main()
