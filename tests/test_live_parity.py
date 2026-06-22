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

import asyncio
from polybot import backtester as bt
from polybot.core import Portfolio, Tick, Position
from polybot.live import (live_tick, build_portfolio, window_open_strike, _trade_one_market)
from polybot.recorder import winner_from_recent, winner_from_last
from polybot.database import determine_winner
from polybot.btc_model import prob_up
from polybot.strategies import get_strategy
from tests.helpers import make_market


class _FakeDB:
    """Captures log_round + the tick/market recording so the loop is observable in tests."""
    def __init__(self):
        self.rounds = []
        self.ticks = []
        self.markets = []
    def log_round(self, session_id, res, market_id=None, ts=None):
        self.rounds.append((session_id, res, market_id, ts))
    def insert_tick(self, token, seq, row):
        self.ticks.append((token, seq, row))
    def upsert_market(self, market_id, **kw):
        self.markets.append((market_id, kw))


async def _aiter(items, raise_after=False):
    """Async generator yielding (msg, book, spot, rem) tuples; if raise_after, raises on the final
    __anext__ AFTER yielding all items — simulating a websocket drop once some ticks have traded."""
    for it in items:
        yield it
    if raise_after:
        raise ConnectionError("ws dropped")


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


def _msg(bid, ask, rem, spot=0.0, token="TKN"):
    # CURRENT Polymarket schema: per-asset quotes nested in a price_changes array.
    return ({"event_type": "price_change",
             "price_changes": [{"asset_id": token, "best_bid": bid, "best_ask": ask,
                                "price": bid, "size": "100", "side": "BUY"}]},
            {"bid_p1": bid, "bid_s1": 1e6, "ask_p1": ask, "ask_s1": 1e6}, spot, rem)


def _favorite_pf():
    cfg = {"strategies": [{"id": "fav", "name": "fav_convergence", "weight": 1.0,
                           "params": {"buy_p": 0.60, "sell_p": 0.97, "time_cutoff": 0.0,
                                      "stop_p": 0.5, "max_buy": 1, "bullet_pct": 0.05}}],
           "engine": {"fee": 0.001, "slippage": 0.002, "cap_fills": True},
           "risk": {"kill_switch_dd": 0.25, "min_capital": 50.0}}
    return build_portfolio(cfg, 1000.0)


class TestTradeOneMarketLoop(unittest.TestCase):
    """Unit tests for the EXTRACTED live per-market loop (run() is just an I/O driver around it).
    This is the code the iter-43 review found a position-leak bug in; now regression-tested."""

    def _run(self, stream):
        pf = _favorite_pf(); pf.new_market()
        db = _FakeDB(); done = set()
        t = {"v": 1000}
        coro = _trade_one_market(pf, end_ts=1000, strike=100000.0, msg_stream=stream,
                                 token="TKN", db=db, session_id="s", done=done,
                                 time_fn=lambda: t["v"], log=None)
        return pf, db, done, coro

    def test_normal_completion_settles_and_records(self):
        # favorite at 0.85 -> buys YES; market resolves YES (median last-5 ~0.95) -> profit booked
        stream = _aiter([_msg(0.84, 0.85, rem=120), _msg(0.90, 0.91, rem=80),
                         _msg(0.94, 0.95, rem=40), _msg(0.95, 0.96, rem=10),
                         _msg(0.95, 0.96, rem=5)])
        pf, db, done, coro = self._run(stream)
        res = asyncio.run(coro)
        self.assertIsNotNone(res)
        self.assertEqual(res.winner, "YES")
        self.assertEqual(len(db.rounds), 1)              # settlement recorded exactly once
        self.assertIn("TKN", done)                        # marked done -> never re-traded
        self.assertGreater(pf.total_cash(), 0.0)

    def test_records_ticks_and_market_for_replay(self):
        # live trader must persist the tick stream + market(winner) so windows are backtest-replayable
        stream = _aiter([_msg(0.84, 0.85, rem=120), _msg(0.90, 0.91, rem=80),
                         _msg(0.95, 0.96, rem=10), _msg(0.95, 0.96, rem=5)])
        pf, db, done, coro = self._run(stream)
        asyncio.run(coro)
        self.assertEqual(len(db.ticks), 4, "every processed tick must be recorded")
        self.assertEqual(db.ticks[0][0], "TKN")            # recorded under the right token
        self.assertEqual(len(db.markets), 1)               # market upserted once
        mid, kw = db.markets[0]
        self.assertEqual(mid, "TKN")
        self.assertEqual(kw.get("winner"), "YES")          # winner recorded (matches settlement)
        self.assertEqual(kw.get("n_ticks"), 4)

    def test_midmarket_disconnect_still_settles_no_leak(self):
        # CRITICAL invariant (iter-43): a disconnect mid-market must STILL settle in finally, so
        # the open position is realized, recorded, and the token marked done — never leaked.
        stream = _aiter([_msg(0.84, 0.85, rem=120), _msg(0.92, 0.93, rem=60)], raise_after=True)
        pf, db, done, coro = self._run(stream)
        with self.assertRaises(ConnectionError):          # exception propagates for reconnect backoff
            asyncio.run(coro)
        self.assertEqual(len(db.rounds), 1, "disconnect must still record a settlement (no leak)")
        self.assertIn("TKN", done)
        self.assertIsNotNone(db.rounds[0][1].winner)

    def test_no_ticks_no_settlement(self):
        # an empty stream (never traded) must NOT settle, NOT record, NOT mark done (-> retried)
        pf, db, done, coro = self._run(_aiter([]))
        res = asyncio.run(coro)
        self.assertIsNone(res)
        self.assertEqual(db.rounds, [])
        self.assertNotIn("TKN", done)

    def test_invalid_and_off_token_messages_filtered(self):
        # quotes for a DIFFERENT token, and non-positive bid/ask, must be skipped (no trade)
        pf, db, done, coro = self._run(_aiter([
            _msg(0.84, 0.85, rem=120, token="OTHER"),    # quote for another asset -> skip
            _msg(0.0, 0.0, rem=110),                      # non-positive -> skip
            _msg(0.95, 0.96, rem=100),                    # valid (token TKN) -> the only processed tick
            _msg(0.95, 0.96, rem=5)]))
        res = asyncio.run(coro)
        self.assertIsNotNone(res)                          # the one valid tick traded + settled
        self.assertEqual(len(db.rounds), 1)

    def test_rem_non_positive_breaks_before_trading(self):
        # if rem<=0 on the first message, the loop breaks immediately -> no trade, no settle
        pf, db, done, coro = self._run(_aiter([_msg(0.85, 0.86, rem=0)]))
        res = asyncio.run(coro)
        self.assertIsNone(res)
        self.assertNotIn("TKN", done)


if __name__ == "__main__":
    unittest.main()
