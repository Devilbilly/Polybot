"""
Synthetic BTC up/down markets for testing the spot-divergence edge without live data.

A driftless lognormal BTC path defines the TRUE outcome. The prediction-market mid is the
CORRECT model probability given the spot `lag` seconds ago (efficient but lagging). So:
  lag = 0  -> market == true model  -> spot strategy has NO edge (fair-test control)
  lag > 0  -> market trails reality -> spot strategy (using current spot) profits.
This isolates the INFORMATIONAL edge (spot leads the book) from the behavioural
favorite-longshot edge, which an efficient synthetic market does NOT contain.
"""
from __future__ import annotations
import math
from typing import Dict
import numpy as np

from .btc_model import prob_up


def market_from_path(spot, strike: float = None, window: int = 300, vol: float = 0.0006,
                     lag: int = 0, noise: float = 0.0, spread: float = 0.01, rng=None,
                     fav_bias: float = 0.0) -> Dict[str, np.ndarray]:
    """Build an engine-format market from ANY BTC price path (real or synthetic). The book
    mid is the CORRECT model probability given the spot `lag` ticks ago (efficient but lagging).
    `fav_bias` (0..1) compresses the mid toward 0.5 -> favorites underpriced / longshots
    overpriced (the behavioural favorite-longshot bias), so both edges can coexist."""
    spot = np.asarray(spot, dtype=float)
    n = len(spot)
    if strike is None:
        strike = float(spot[0])
    winner = "YES" if spot[-1] > strike else "NO"
    rem = window * (1.0 - np.arange(n) / n)
    mid = np.empty(n)
    for i in range(n):
        p = prob_up(spot[max(0, i - lag)], strike, max(0.0, rem[i]), vol)
        if fav_bias > 0.0:
            p = 0.5 + (p - 0.5) * (1.0 - fav_bias)        # compress toward 0.5
        if noise > 0.0 and rng is not None:
            p = min(0.99, max(0.01, p + rng.gauss(0, noise)))
        mid[i] = p
    ws_bid = np.clip(mid - spread / 2, 0.01, 0.99)
    ws_ask = np.clip(mid + spread / 2, 0.01, 0.99)
    big = np.full(n, 1e6)
    z = np.zeros(n)
    return {"rem": rem, "ws_bid": ws_bid, "ws_ask": ws_ask,
            "bid_p1": ws_bid, "bid_s1": big, "ask_p1": ws_ask, "ask_s1": big,
            "bid_p2": z, "bid_s2": z, "ask_p2": z, "ask_s2": z,
            "bid_p3": z, "bid_s3": z, "ask_p3": z, "ask_s3": z,
            "spot": spot, "strike": np.full(n, strike), "winner": winner}


def synth_market(rng, n: int = 300, window: int = 300, vol: float = 0.0006,
                 lag: int = 0, noise: float = 0.0, strike: float = 100000.0,
                 spread: float = 0.01) -> Dict[str, np.ndarray]:
    """A driftless-GBM synthetic market (for tests/controls)."""
    spot = np.empty(n); spot[0] = strike
    for i in range(1, n):
        spot[i] = spot[i - 1] * math.exp(vol * rng.gauss(0, 1))
    return market_from_path(spot, strike=strike, window=window, vol=vol, lag=lag,
                            noise=noise, spread=spread, rng=rng)


def synth_dataset(seed: int, count: int, **kw):
    import random
    rng = random.Random(seed)
    return [synth_market(rng, **kw) for _ in range(count)]
