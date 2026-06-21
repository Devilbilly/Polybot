"""Synthetic market builders for deterministic tests."""
import numpy as np

L2_ZERO = ["bid_p2", "bid_s2", "ask_p2", "ask_s2", "bid_p3", "bid_s3", "ask_p3", "ask_s3"]


def make_market(n=200, fav="YES", fav_price=0.85, fav_start_frac=0.5, depth=1000.0):
    """A market where `fav` becomes a stable favorite in the 2nd half and wins."""
    rem = np.linspace(300.0, 0.0, n)
    bid = np.full(n, 0.50); ask = np.full(n, 0.51)
    k = int(n * fav_start_frac)
    if fav == "YES":
        bid[k:] = fav_price - 0.01; ask[k:] = fav_price
    else:
        bid[k:] = 1.0 - fav_price; ask[k:] = 1.0 - fav_price + 0.01
    a = {"rem": rem, "ws_bid": bid, "ws_ask": ask,
         "bid_p1": bid.copy(), "ask_p1": ask.copy(),
         "bid_s1": np.full(n, depth), "ask_s1": np.full(n, depth)}
    for c in L2_ZERO:
        a[c] = np.zeros(n)
    a["winner"] = "YES" if np.median(bid[-5:]) > 0.5 else "NO"
    return a


def make_reversal_market(n=200, depth=1000.0):
    """YES is a favorite at 0.85 DURING the late window (tp 0.5-0.75), then CRASHES to 0.20
    and loses. FAV (start 0.5) should buy YES then hit the reversal stop below 0.50."""
    rem = np.linspace(300.0, 0.0, n)
    bid = np.full(n, 0.50); ask = np.full(n, 0.51)
    h = n // 2          # late window begins here (tp ~0.5)
    q = n // 4
    bid[h:h+q] = 0.84; ask[h:h+q] = 0.85           # YES favorite during late window
    bid[h+q:] = 0.19; ask[h+q:] = 0.20             # crashes -> NO wins, triggers the stop
    a = {"rem": rem, "ws_bid": bid, "ws_ask": ask,
         "bid_p1": bid.copy(), "ask_p1": ask.copy(),
         "bid_s1": np.full(n, depth), "ask_s1": np.full(n, depth)}
    for c in L2_ZERO:
        a[c] = np.zeros(n)
    a["winner"] = "NO"
    return a
