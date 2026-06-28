"""2D sweep: buy_p (entry floor) x sell_p (entry ceiling), with win% / Sharpe(EV/std) / ROI / DD.
Combined (all markets) AND per-coin (via coin_map.json). Chronological order; offline."""
import copy
import json

import numpy as np

from polybot import database
from polybot.backtester import compute_fractions, _compound

DB = "archive/recent_market_data.db"
SID = "fav_hold"
coin_map = json.load(open("coin_map.json"))
BUY = [0.70, 0.74, 0.76, 0.78]
SELL = [0.90, 0.93, 0.96, 0.99]


def mono(m):
    rem = m.get("rem")
    if rem is None or len(rem) < 2:
        return True
    return not bool(np.any(np.diff(rem) > 1.0))


db = database.Database(DB)
rows = db.conn.execute("SELECT market_id, end_ts FROM markets WHERE end_ts IS NOT NULL").fetchall()
mk = []   # (coin_or_None, array)
for mid, ets in sorted(rows, key=lambda x: x[1]):
    m = db.load_market(mid)
    if m is not None and mono(m):
        mk.append((coin_map.get(mid), m))
db.close()
base = json.load(open("polybot/portfolio.json"))


def evalp(arrs, bp, sp):
    cfg = copy.deepcopy(base)
    cfg["strategies"][0]["params"]["buy_p"] = bp
    cfg["strategies"][0]["params"]["sell_p"] = sp
    fr = compute_fractions(arrs, cfg)
    vals = np.array([x.get(SID, 0.0) for x in fr if abs(x.get(SID, 0.0)) > 1e-9])
    if len(vals) == 0:
        return None
    ev, std = vals.mean(), vals.std()
    r = _compound(fr, cfg)
    return (len(vals), 100 * (vals > 0).mean(), (ev / std if std > 0 else 0.0), r.roi_pct, r.max_dd_pct)


groups = [("ALL", [a for _, a in mk])]
for c in ("btc", "eth", "sol", "xrp"):
    groups.append((c, [a for cn, a in mk if cn == c]))

for gname, arrs in groups:
    print("\n===== %s  (%d markets) =====" % (gname, len(arrs)))
    print("%5s %5s %5s %6s %8s %8s %7s" % ("buy", "sell", "n", "win%", "Sharpe", "ROI%", "DD%"))
    best = None
    for bp in BUY:
        for sp in SELL:
            if sp <= bp:
                continue
            r = evalp(arrs, bp, sp)
            if not r:
                continue
            n, win, sharpe, roi, dd = r
            star = ""
            if best is None or sharpe > best[0]:
                best = (sharpe, bp, sp); star = ""
            print("%5.2f %5.2f %5d %6.1f %8.3f %+8.1f %7.1f" % (bp, sp, n, win, sharpe, roi, dd))
    if best:
        print("  -> best Sharpe: buy_p=%.2f sell_p=%.2f (%.3f)" % (best[1], best[2], best[0]))
