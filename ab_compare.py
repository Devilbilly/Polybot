"""A/B comparison over RECENT RECORDED live data: replay the live config (buy_p=0.70) vs the
variant (buy_p=0.78) over the recorder's market_data.db. Same live markets, paper P&L side by side,
zero new WS/REST load (uses already-captured ticks) so the real bot is never disturbed.

    python3 ab_compare.py [market_data.db] [orig_cfg] [variant_cfg]
"""
import json
import sys

import numpy as np

from polybot.backtester import load_markets
from polybot.paper import paper_trade

src = sys.argv[1] if len(sys.argv) > 1 else "/home/palacedeforsaken/Polybot/market_data.db"
orig_cfg = sys.argv[2] if len(sys.argv) > 2 else "/home/palacedeforsaken/Polybot/polybot/portfolio.json"
var_cfg = sys.argv[3] if len(sys.argv) > 3 else "/home/palacedeforsaken/Polybot/portfolio_floor.json"


def mono(m):
    rem = m.get("rem")
    if rem is None or len(rem) < 2:
        return True
    return not bool(np.any(np.diff(rem) > 1.0))


markets = [m for m in load_markets(src) if mono(m)]
print("A/B replay over %d recent recorded markets (%s)\n" % (len(markets), src.split("/")[-1]))
for label, cfgp in [("ORIGINAL buy_p=0.70 (LIVE)", orig_cfg), ("VARIANT  buy_p=0.78", var_cfg)]:
    cfg = json.load(open(cfgp))
    r = paper_trade(markets, cfg, capital=1000.0)
    print("  %-26s %s" % (label, r))
