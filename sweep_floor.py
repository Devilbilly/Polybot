"""Fine sweep of the entry floor buy_p (0.50 -> 0.90, step 0.01) over the recent recorded data.
Markets fed in CHRONOLOGICAL order so ROI/maxDD are comparable across floors (sell_p=0.93 fixed).
win% / entered / EV-per-trade are order-independent (the robust comparators); ROI/maxDD are
path-influenced but on one fixed path. Read-only, fully offline."""
import copy
import json

import numpy as np

from polybot import database
from polybot.backtester import compute_fractions, _compound

DB = "archive/recent_market_data.db"
SID = "fav_hold"


def mono(m):
    rem = m.get("rem")
    if rem is None or len(rem) < 2:
        return True
    return not bool(np.any(np.diff(rem) > 1.0))


db = database.Database(DB)
rows = db.conn.execute("SELECT market_id, end_ts FROM markets WHERE end_ts IS NOT NULL").fetchall()
markets = []
for mid, ets in sorted(rows, key=lambda x: x[1]):     # chronological
    m = db.load_market(mid)
    if m is not None and mono(m):
        markets.append(m)
db.close()

base = json.load(open("polybot/portfolio.json"))
print("sweep over %d chronological markets (sell_p=0.93 fixed)\n" % len(markets))
print("%6s %8s %7s %10s %10s %8s %9s %8s"
      % ("buy_p", "entered", "win%", "EV/trade", "STD/trade", "Sharpe", "ROI%", "maxDD%"))
for i in range(41):
    f = round(0.50 + 0.01 * i, 2)
    cfg = copy.deepcopy(base)
    cfg["strategies"][0]["params"]["buy_p"] = f
    fr = compute_fractions(markets, cfg)
    vals = np.array([x.get(SID, 0.0) for x in fr if abs(x.get(SID, 0.0)) > 1e-9])
    ent = len(vals)
    ev = vals.mean() if ent else 0.0
    std = vals.std() if ent else 0.0
    sharpe = (ev / std) if std > 0 else 0.0   # per-trade Sharpe = EV/variance(std) — risk-adjusted edge
    r = _compound(fr, cfg)
    print("%6.2f %8d %6.1f%% %+10.5f %10.5f %8.3f %+9.1f %8.1f"
          % (f, ent, r.win_rate_pct, ev, std, sharpe, r.roi_pct, r.max_dd_pct))
