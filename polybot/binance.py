"""
Binance BTC price data — for (a) live spot in the recorder/live trader, and (b) building
REAL-path test markets for the spot-divergence edge (real momentum/jumps, not GBM).

Pure helpers (closes_to_windows, estimate_vol) are unit-tested; the network fetch is not.
"""
from __future__ import annotations
import json
import urllib.request
from typing import List, Tuple
import numpy as np

API = "https://api.binance.com/api/v3/klines"
SPOT_API = "https://api.binance.com/api/v3/ticker/price"


def fetch_klines(symbol="BTCUSDT", interval="1s", total=6000, end_time=None
                 ) -> List[Tuple[int, float]]:  # pragma: no cover (network)
    """Fetch `total` recent klines, paginating backwards. Returns [(open_time_ms, close)]."""
    out: List[Tuple[int, float]] = []
    et = end_time
    while len(out) < total:
        limit = min(1000, total - len(out))
        url = f"{API}?symbol={symbol}&interval={interval}&limit={limit}"
        if et is not None:
            url += f"&endTime={et}"
        with urllib.request.urlopen(url, timeout=10) as r:
            rows = json.load(r)
        if not rows:
            break
        out = [(int(k[0]), float(k[4])) for k in rows] + out
        et = int(rows[0][0]) - 1
    return out


def fetch_spot(symbol="BTCUSDT") -> float:  # pragma: no cover (network)
    with urllib.request.urlopen(f"{SPOT_API}?symbol={symbol}", timeout=10) as r:
        return float(json.load(r)["price"])


def closes_to_windows(closes, window: int = 300) -> List[np.ndarray]:
    """Slice a flat close series into non-overlapping windows of `window` points."""
    c = np.asarray(closes, dtype=float)
    n = len(c) // window
    return [c[i * window:(i + 1) * window] for i in range(n)]


def estimate_vol(closes) -> float:
    """Per-step log-return stdev (== btc_model's per-sqrt-second vol when the step is 1s)."""
    c = np.asarray(closes, dtype=float)
    c = c[c > 0]
    if len(c) < 2:
        return 0.0
    return float(np.std(np.diff(np.log(c))))
