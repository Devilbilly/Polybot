"""A/B (0.70 vs 0.78) segmented by time: 'that period' (the eth-bad window 18:00-21:30 UTC where the
weak-favorite losers clustered) vs OVERALL. Replays both configs over the recorder's market_data.db,
filtering markets by settlement end_ts. Read-only; nothing online touched."""
import calendar
import json

import numpy as np

from polybot import database
from polybot.paper import paper_trade

DB = "/home/palacedeforsaken/Polybot/market_data.db"
ORIG = "/home/palacedeforsaken/Polybot/polybot/portfolio.json"
VAR = "/home/palacedeforsaken/Polybot/portfolio_floor.json"

P0 = calendar.timegm((2026, 6, 27, 18, 0, 0, 0, 0, 0))   # 2026-06-27 18:00:00 UTC
P1 = calendar.timegm((2026, 6, 27, 21, 30, 0, 0, 0, 0))  # 21:30:00 UTC


def mono(m):
    rem = m.get("rem")
    if rem is None or len(rem) < 2:
        return True
    return not bool(np.any(np.diff(rem) > 1.0))


db = database.Database(DB)
rows = [(mid, ets) for mid, ets in
        db.conn.execute("SELECT market_id, end_ts FROM markets WHERE end_ts IS NOT NULL").fetchall()]


def load(subset):
    out = []
    for mid, ets in sorted(subset, key=lambda x: x[1]):   # chronological for honest compounding
        m = db.load_market(mid)
        if m is not None and mono(m):
            out.append(m)
    return out


period = load([(m, e) for m, e in rows if P0 <= e <= P1])
overall = load(rows)
db.close()
print("THAT PERIOD (18:00-21:30Z): %d markets   |   OVERALL: %d markets\n" % (len(period), len(overall)))

orig = json.load(open(ORIG))
var = json.load(open(VAR))
for label, mks in [("THAT PERIOD (eth-bad, 18:00-21:30Z)", period), ("OVERALL (full recorded)", overall)]:
    print("=== %s ===" % label)
    for cl, cfg in [("0.70 (LIVE)   ", orig), ("0.78 (variant)", var)]:
        r = paper_trade(mks, cfg, capital=1000.0)
        print("  %s %s" % (cl, r))
    print()
