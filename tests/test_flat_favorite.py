"""Regression test for flat_favorite: enters when the favorite price is FLAT over the lookback,
skips the market when it's moving (rising/fading). Decision is made once, at the first in-band tick."""
import unittest
from types import SimpleNamespace

from polybot.core import Tick
from polybot.strategies import get_strategy


def tick(tp, fav_mid, spread=0.01):
    bid = round(fav_mid - spread / 2, 4)
    ask = round(fav_mid + spread / 2, 4)
    return Tick(ts="0", time_progress=tp, ws_bid=bid, ws_ask=ask, bid_p=(bid, 0.0, 0.0), ask_p=(ask, 0.0, 0.0))


def pos():
    return SimpleNamespace(cash=1000.0, n_entries=0, inv_yes=0.0, inv_no=0.0)


def run(prices):
    s = get_strategy("flat_favorite", dict(buy_p=0.55, sell_p=0.95, time_cutoff=0.50, stop_p=0.0,
                                            max_buy=1, lookback=10, flat_tol=0.01, bullet_pct=0.025))
    s.reset()
    out = []
    for tp, mid in prices:
        out += s.decide(tick(tp, mid), pos())
    return out


class FlatFavorite(unittest.TestCase):
    def test_flat_favorite_enters(self):
        # 12 ticks, favorite mid steady at 0.80 (flat); tp crosses 0.5 -> should BUY once
        orders = run([(0.40 + 0.02 * i, 0.80) for i in range(12)])
        buys = [o for o in orders if o.kind == "BUY"]
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0].side, "YES")

    def test_rising_favorite_skipped(self):
        # favorite climbs 0.70 -> ~0.84 over the window (>flat_tol) -> not flat -> NO entry
        orders = run([(0.40 + 0.02 * i, 0.70 + 0.0125 * i) for i in range(12)])
        self.assertEqual([o for o in orders if o.kind == "BUY"], [])

    def test_fading_favorite_skipped(self):
        # favorite fades 0.88 -> ~0.74 (>flat_tol) -> not flat -> NO entry
        orders = run([(0.40 + 0.02 * i, 0.88 - 0.0125 * i) for i in range(12)])
        self.assertEqual([o for o in orders if o.kind == "BUY"], [])

    def test_decides_once_then_skips(self):
        # if the first in-band tick is NOT flat, the market is skipped even if it goes flat later
        seq = [(0.40 + 0.02 * i, 0.70 + 0.0125 * i) for i in range(12)]   # moving at decision
        seq += [(0.64 + 0.02 * i, 0.84) for i in range(8)]                # then flat -> must still skip
        self.assertEqual([o for o in run(seq) if o.kind == "BUY"], [])


if __name__ == "__main__":
    unittest.main()
