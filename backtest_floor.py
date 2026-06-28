"""Backtest the entry-FLOOR change (buy_p) the eth analysis suggested: does skipping weaker
favorites (raising buy_p above 0.70) improve win-rate / per-trade EV? Fast per-market fractions,
reported over the full set + two halves (rough OOS). Then a capacity-honest paper-trade of the
best floor vs the current 0.70.
"""
import copy
import json
import sys

import numpy as np

from polybot.backtester import load_markets, compute_fractions
from polybot.paper import paper_trade

SRC = sys.argv[1] if len(sys.argv) > 1 else "archive/master_polybot.db"
SID = "fav_hold"
raw = load_markets(SRC)


def monotonic(m):
    """Drop merge-artifact markets whose recorded time jumps backward (would trip the look-ahead gate)."""
    rem = m.get("rem")
    if rem is None or len(rem) < 2:
        return True
    return not bool(np.any(np.diff(rem) > 1.0))   # rem must be non-increasing within a market


markets = [m for m in raw if monotonic(m)]
n = len(markets)
print("filtered %d -> %d clean monotonic-time markets" % (len(raw), n))
mid = n // 2
splits = [("FULL", markets), ("halfA", markets[:mid]), ("halfB", markets[mid:])]
base = json.load(open("polybot/portfolio.json"))
print("loaded %d resolved markets from %s\n" % (n, SRC))

print("=== BACKTEST: entry-floor (buy_p) sweep — per-trade win%% + EV ===")
print("%6s %7s %9s %7s %14s %10s" % ("buy_p", "split", "entered", "win%", "avgRet/trade", "sumRet%"))
for floor in [0.70, 0.72, 0.75, 0.78, 0.80]:
    cfg = copy.deepcopy(base)
    cfg["strategies"][0]["params"]["buy_p"] = floor
    for name, mks in splits:
        fr = compute_fractions(mks, cfg)
        vals = [f.get(SID, 0.0) for f in fr if abs(f.get(SID, 0.0)) > 1e-9]
        nt = len(vals)
        if nt == 0:
            print("%6.2f %7s %9d %7s %14s %10s" % (floor, name, 0, "-", "-", "-"))
            continue
        win = 100.0 * sum(1 for v in vals if v > 0) / nt
        avg = sum(vals) / nt
        print("%6.2f %7s %9d %6.1f%% %+14.5f %+10.1f" % (floor, name, nt, win, avg, 100 * sum(vals)))
    print()

print("=== PAPER-TRADE (capacity-honest, compounding): current 0.70 vs floor 0.75 ===")
for floor in [0.70, 0.75]:
    cfg = copy.deepcopy(base)
    cfg["strategies"][0]["params"]["buy_p"] = floor
    for name, mks in [("halfA", markets[:mid]), ("halfB", markets[mid:])]:
        r = paper_trade(mks, cfg, capital=1000.0)
        print("  buy_p=%.2f %s: %s" % (floor, name, r))
