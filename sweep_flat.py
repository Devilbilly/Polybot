"""Sweep the flat strategy's entry CEILING (sell_p) with buy_p fixed at 0.55, on the master archive.
Faithful replay of the deployed flat logic (decide once at first in-band tick; take only if flat;
mid=(bp1+ap1)/2, lookback 10, flat_tol 0.01). OOS: chronological 60/40 split -> report TEST-half
metrics. Decision metric = TOTAL realized $ at $1/trade on the held-out test (captures fire-rate x
per-trade-EV); win-edge & EV/$1 (cost 0.003) with a bootstrap 95% CI. Plus 0.76 trade-all reference."""
import sys
import numpy as np
from polybot import database

DB = sys.argv[1] if len(sys.argv) > 1 else "archive/master_polybot.db"
COST, LB, TOL = 0.003, 10, 0.01
CONFIGS = [("0.76 trade-all sell0.93 (cur)", 0.76, 0.93, False),
           ("0.76 trade-all sell0.90", 0.76, 0.90, False),
           ("0.76 trade-all sell0.88", 0.76, 0.88, False),
           ("0.76 trade-all sell0.85", 0.76, 0.85, False),
           ("0.76 trade-all sell0.82", 0.76, 0.82, False),
           ("0.76 trade-all sell0.80", 0.76, 0.80, False),
           ("0.70 trade-all sell0.85", 0.70, 0.85, False),
           ("0.72 trade-all sell0.84", 0.72, 0.84, False)]


def mono(m):
    rem = m.get("rem")
    return True if rem is None or len(rem) < 2 else not bool(np.any(np.diff(rem) > 1.0))


def entry(rem, bp, ap, mid, w, lo, hi, flat_req):
    for i in range(len(rem)):
        if rem[i] > 150 or i < LB:
            continue
        ya, na = ap[i], 1.0 - bp[i]
        yb, nb = (lo <= ya <= hi), (lo <= na <= hi)
        if not (yb or nb):
            continue
        if flat_req and abs(mid[i] - mid[i - LB]) > TOL:
            return None
        side, buy = ("YES", ya) if yb else ("NO", na)
        return (buy, 1 if side == w else 0)
    return None


db = database.Database(DB)
rows = db.conn.execute("SELECT market_id,end_ts FROM markets WHERE end_ts IS NOT NULL ORDER BY end_ts").fetchall()
loaded = []
for mid, e in rows:
    m = db.load_market(mid)
    if m is None or not mono(m) or m.get("winner") not in ("YES", "NO"):
        continue
    bp = np.asarray(m["bid_p1"], float); ap = np.asarray(m["ask_p1"], float)
    loaded.append((e, rem := np.asarray(m["rem"], float), bp, ap, (bp + ap) / 2.0, m["winner"]))
db.close()

N = len(loaded)
cut = int(N * 0.6)
rng = np.random.default_rng(0)
print("markets=%d  (train older %d / TEST newer %d)\n" % (N, cut, N - cut))
print("%-30s %5s %6s %6s %7s %13s %10s" % ("config (TEST half)", "n", "fire%", "win%", "buy px", "EV/$1 [95%CI]", "total $"))
print("-" * 92)
for label, lo, hi, fr in CONFIGS:
    res = [entry(rem, bp, ap, mid, w, lo, hi, fr) for (e, rem, bp, ap, mid, w) in loaded[cut:]]
    res = [x for x in res if x]
    n = len(res)
    if n < 5:
        print("%-30s   n<5" % label)
        continue
    p = np.array([x[0] for x in res]); y = np.array([x[1] for x in res], float)
    val = np.where(y > 0, (1 - p) / p, -1.0) - COST
    boot = [val[rng.integers(0, n, n)].mean() for _ in range(2000)]
    clo, chi = np.percentile(boot, [2.5, 97.5])
    sig = "*" if clo > 0 else " "
    print("%-30s %5d %5.0f%% %5.1f %6.1f  %+5.2f[%+4.1f,%+4.1f]%s %+9.2f"
          % (label, n, 100 * n / (N - cut), 100 * y.mean(), 100 * p.mean(),
             100 * val.mean(), 100 * clo, 100 * chi, sig, val.sum()))
print("-" * 92)
print("total $ = realized profit on the TEST half at $1/trade (sum of per-trade outcomes). "
      "EV/$1 CI excluding 0 (*) = real per-trade edge OOS. Pick best total $ with a (*).")
