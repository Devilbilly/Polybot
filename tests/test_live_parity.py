"""
Live/backtest PARITY: the live trader (live.py) reuses core.Portfolio so backtest results are
supposed to carry over to live "by construction". This test PROVES the two un-obvious seams are
actually faithful, using offline data (the live async loop itself needs network and can't run
here):

  1. TICK ASSEMBLY  — live_tick() (live loop) must build the SAME Tick as ticks_from_arrays()
     (backtest) for identical market data, so the strategies see identical input -> identical
     decisions -> identical fills. Verified by running one real market through both paths and
     asserting identical final portfolio cash + per-strategy state.

  2. SETTLEMENT PROXY — the live PAPER winner proxy (winner_from_recent, median-of-5) must match
     the backtest's determine_winner on cleanly-resolved markets, else live paper P&L isn't
     comparable to the validated backtest (the whole point of paper-trading to testify).
"""
import json
import unittest
import numpy as np

from polybot import backtester as bt
from polybot.core import Portfolio, Tick, Position
from polybot.live import live_tick, build_portfolio, window_open_strike
from polybot.recorder import winner_from_recent, winner_from_last
from polybot.database import determine_winner
from polybot.btc_model import prob_up
from polybot.strategies import get_strategy
from tests.helpers import make_market


CFG = {
    "strategies": [
        {"id": "fav", "name": "scale_in_favorite", "weight": 0.6,
         "params": {"buy_p": 0.60, "sell_p": 0.93, "time_cutoff": 0.4, "stop_p": 0.5,
                    "max_buy": 2, "add_gap": 0.04, "bullet_pct": 0.03, "lookback": 50, "min_rise": 0.02}},
        {"id": "conv", "name": "fav_convergence", "weight": 0.4,
         "params": {"buy_p": 0.85, "sell_p": 0.97, "time_cutoff": 0.7, "stop_p": 0.5, "bullet_pct": 0.03}},
    ],
    "engine": {"fee": 0.001, "slippage": 0.002, "cap_fills": True},
    "risk": {"kill_switch_dd": 0.25, "soft_dd": 0.15, "round_loss_limit": 0.08, "min_capital": 50.0},
}


def _run_backtest_path(market, cfg, capital):
    pf = build_portfolio(cfg, capital)
    pf.new_market()
    for tick in bt.ticks_from_arrays(market):
        pf.process_tick(tick)
    return pf


def _run_live_path(market, cfg, capital):
    """Replay the SAME market rows through live_tick() exactly as the live loop would, using the
    recorded window so time_progress is reconstructed from rem (live uses WINDOW_SEC=300)."""
    pf = build_portfolio(cfg, capital)
    pf.new_market()
    a = market
    n = len(a["ws_bid"])
    for i in range(n):
        wb = a["ws_bid"][i]; wa = a["ws_ask"][i]
        if wb <= 0 or wa <= 0:            # same guard the live loop applies before process_tick
            continue
        rem = a["rem"][i]
        book = {f"{side}_{f}{lvl}": a[f"{side}_{f}{lvl}"][i]
                for side in ("bid", "ask") for f in ("p", "s") for lvl in (1, 2, 3)}
        pf.process_tick(live_tick(rem, wb, wa, book))
    return pf


class TestTickAssemblyParity(unittest.TestCase):
    def test_live_tick_matches_backtest_tick_on_synth_market(self):
        # deterministic synthetic favorite market (window 300 -> live's hardcoded WINDOW_SEC)
        m = make_market(n=300, fav="YES")
        m["window"] = 300.0
        a = _run_backtest_path(m, CFG, 1000.0)
        b = _run_live_path(m, CFG, 1000.0)
        self.assertAlmostEqual(a.total_cash(), b.total_cash(), places=6,
                               msg="live_tick path diverged from backtest path")
        for sa, sb in zip(a.accounts, b.accounts):
            self.assertAlmostEqual(sa.cash, sb.cash, places=6)
            self.assertAlmostEqual(sa.inv_yes, sb.inv_yes, places=6)
            self.assertAlmostEqual(sa.inv_no, sb.inv_no, places=6)

    def test_parity_on_real_markets_if_available(self):
        try:
            markets = bt.load_markets("market_data")
        except Exception:
            markets = []
        if not markets:
            self.skipTest("no real market_data available")
        for m in markets[:25]:
            m = dict(m); m.setdefault("window", 300.0)
            a = _run_backtest_path(m, CFG, 1000.0)
            b = _run_live_path(m, CFG, 1000.0)
            self.assertAlmostEqual(a.total_cash(), b.total_cash(), places=5,
                                   msg="live vs backtest cash diverged on a real market")


class TestSettlementProxyParity(unittest.TestCase):
    def test_median_proxy_matches_backtest_on_clean_markets(self):
        try:
            markets = bt.load_markets("market_data")
        except Exception:
            markets = []
        if not markets:
            self.skipTest("no real market_data available")
        agree = total = 0
        for m in markets[:200]:
            wb = m["ws_bid"]
            bt_win = determine_winner(wb)           # backtest proxy (may be None = unresolved)
            if bt_win is None:
                continue                            # skip ambiguous; live must settle but bt excludes
            live_win = winner_from_recent(list(wb))
            total += 1
            agree += (bt_win == live_win)
        self.assertGreater(total, 20, "need a reasonable sample of resolved markets")
        # on cleanly-resolved markets the median proxies must agree essentially always
        self.assertGreaterEqual(agree / total, 0.99,
                                f"settlement proxies disagree on {total-agree}/{total} clean markets")

    def test_recent_proxy_is_robust_to_a_last_tick_blip(self):
        # median-of-5 must NOT flip on a single noisy last tick, unlike the old single-bid proxy
        bids = [0.93, 0.94, 0.95, 0.96, 0.02]      # clean YES favorite + one blip
        self.assertEqual(winner_from_recent(bids), "YES")
        self.assertEqual(winner_from_last(bids[-1]), "NO")   # old proxy would mis-settle on the blip

    def test_recent_proxy_handles_empty_and_zero(self):
        self.assertIsNone(winner_from_recent([]))
        self.assertIsNone(winner_from_recent([0.0, 0.0]))
        self.assertEqual(winner_from_recent([0.0, 0.0, 0.7, 0.8, 0.9]), "YES")


class TestWindowOpenStrike(unittest.TestCase):
    def test_fetches_close_at_window_open_time(self):
        calls = {}
        def fake_fetch(interval="1s", total=1, end_time=None):
            calls["interval"] = interval; calls["total"] = total; calls["end_time"] = end_time
            return [(end_time, 100123.5)]
        strike = window_open_strike(end_ts=1_700_000_300, fetch_klines_fn=fake_fetch, window=300)
        self.assertEqual(strike, 100123.5)
        # must request the OPEN time = (end_ts - window) in MILLISECONDS
        self.assertEqual(calls["end_time"], (1_700_000_300 - 300) * 1000)
        self.assertEqual(calls["interval"], "1s")

    def test_returns_zero_on_empty_or_error(self):
        self.assertEqual(window_open_strike(1_700_000_300, lambda **k: [], window=300), 0.0)
        def boom(**k):
            raise RuntimeError("network down")
        self.assertEqual(window_open_strike(1_700_000_300, boom, window=300), 0.0)  # -> caller falls back


class TestSpotDegradationGuards(unittest.TestCase):
    """A Binance hiccup (spot=0 / strike=0) must NOT crash or mis-signal the live spot path."""
    def test_prob_up_neutral_on_bad_spot_or_strike(self):
        self.assertEqual(prob_up(0.0, 100000, 60, 0.0006), 0.5)
        self.assertEqual(prob_up(100000, 0.0, 60, 0.0006), 0.5)
        self.assertEqual(prob_up(-5, -5, 60, 0.0006), 0.5)

    def test_spot_confirmation_degrades_to_allow_when_spot_missing(self):
        # spot_confirmed_favorite with confirm>0 but NO spot feed must still trade the favorite
        # (graceful degradation), not silently halt on a Binance outage.
        s = get_strategy("spot_confirmed_favorite",
                         {"buy_p": 0.60, "sell_p": 0.97, "time_cutoff": 0.0, "stop_p": 0.5,
                          "max_buy": 1, "bullet_pct": 0.02, "vol": 0.0006, "window": 300, "confirm": 0.55})
        t = Tick(ts="t", time_progress=0.6, ws_bid=0.84, ws_ask=0.85,
                 bid_p=(0.84, 0, 0), bid_s=(1e6, 0, 0), ask_p=(0.85, 0, 0), ask_s=(1e6, 0, 0))  # spot/strike=0
        orders = s.decide(t, Position(cash=1000))
        self.assertTrue(any(o.kind == "BUY" for o in orders),
                        "confirmation sleeve must trade the favorite when spot is unavailable")


if __name__ == "__main__":
    unittest.main()
