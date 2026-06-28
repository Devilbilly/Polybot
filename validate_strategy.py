"""SAFETY GATE before going live: run the new flat_favorite strategy through the REAL engine over the
archive and confirm it reproduces setup_table (flat 0.55 -> ~18% fire, ~85% win), and that trade-all at
0.55 would (correctly) fire far more / win less. If flat doesn't restrict entries, DO NOT deploy."""
import copy
import json
import numpy as np
from polybot import database
from polybot.backtester import compute_fractions

DB = "archive/recent_market_data.db"


def mono(m):
    rem = m.get("rem")
    return True if rem is None or len(rem) < 2 else not bool(np.any(np.diff(rem) > 1.0))


db = database.Database(DB)
rows = db.conn.execute("SELECT market_id FROM markets WHERE end_ts IS NOT NULL").fetchall()
arrs = []
for (mid,) in rows:
    m = db.load_market(mid)
    if m is not None and mono(m) and m.get("winner") in ("YES", "NO"):
        arrs.append(m)
db.close()
base = json.load(open("polybot/portfolio.json"))


def cfg(name, params):
    c = copy.deepcopy(base)
    c["strategies"] = [{"id": "s", "name": name, "weight": 1.0, "params": params}]
    return c


P = dict(buy_p=0.55, sell_p=0.95, time_cutoff=0.50, stop_p=0.0, max_buy=1, bullet_pct=0.025)
setups = [
    ("flat_favorite 0.55", "flat_favorite", {**P, "lookback": 10, "flat_tol": 0.01}),
    ("trade-all     0.55", "fav_hold", P),
    ("flat_favorite 0.76", "flat_favorite", {**P, "buy_p": 0.76, "lookback": 10, "flat_tol": 0.01}),
]
print("markets=%d\n" % len(arrs))
print("%-22s %8s %8s %8s" % ("setup", "fire%", "win%", "n"))
for label, name, params in setups:
    fr = compute_fractions(arrs, cfg(name, params))
    v = np.array([x.get("s", 0.0) for x in fr])
    ent = np.abs(v) > 1e-12
    n = int(ent.sum())
    fire = 100 * n / len(v)
    win = 100 * (v[ent] > 0).mean() if n else 0
    print("%-22s %7.0f%% %7.0f%% %8d" % (label, fire, win, n))
print("\nEXPECT (from setup_table): flat 0.55 ~18%% fire / ~86%% win ; trade-all 0.55 high fire / lower win.")
print("If flat 0.55 fire-rate is NOT far below trade-all, the filter is broken -> DO NOT deploy.")
