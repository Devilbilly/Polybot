"""Favorite-longshot edge by ENTRY-PRICE BAND on the largest dataset (master archive, ~5200 markets).
Replicate the fav_hold entry (first tick at time_progress>=0.5 where the favorite is in [0.50,0.95]),
hold to settlement. Bucket by the price paid; report win%, implied (=price), win-edge, EV/$1 with a
bootstrap 95% CI. Directly answers: does ANY band reach the 3% EV target on the big sample, or is the
edge structurally thin? Uses the monotonic-rem filter (merged archive has scrambled-seq markets)."""
import sys

import numpy as np

from polybot import database

DB = sys.argv[1] if len(sys.argv) > 1 else "archive/master_polybot.db"
TCUT = 0.50


def mono(m):
    rem = m.get("rem")
    return True if rem is None or len(rem) < 2 else not bool(np.any(np.diff(rem) > 1.0))


db = database.Database(DB)
rows = db.conn.execute("SELECT market_id FROM markets WHERE end_ts IS NOT NULL").fetchall()
prices, wins = [], []
bad = 0
for (mid,) in rows:
    m = db.load_market(mid)
    if m is None or not mono(m):
        bad += 1
        continue
    rem = m["rem"]
    w = m.get("winner")
    if w not in ("YES", "NO"):
        continue
    bp, ap = m["bid_p1"], m["ask_p1"]
    midp = (bp + ap) / 2.0
    ent = None
    for k in range(len(rem)):
        if rem[k] > 300 * TCUT:
            continue
        yp = midp[k]
        if yp <= 0 or yp >= 1:
            continue
        fav = "YES" if yp >= 0.5 else "NO"
        fp = yp if fav == "YES" else 1 - yp
        if 0.50 <= fp <= 0.95:
            ent = (fp, 1 if fav == w else 0)
            break
    if ent:
        prices.append(ent[0]); wins.append(ent[1])
db.close()

p = np.array(prices)
y = np.array(wins, float)
ev = np.where(y > 0, (1 - p) / p, -1.0)        # EV/$1 per trade (no fees here; subtract ~0.003 for costs)
print("usable entries = %d  (dropped %d non-monotonic/no-winner markets)" % (len(p), bad))
print("\nband        n     win%%   implied%%  win-edge   EV/$1     95%% CI            cost-adj EV")
rng = np.random.default_rng(1)
edges = np.arange(0.50, 0.96, 0.05)
for i in range(len(edges) - 1):
    lo, hi = edges[i], edges[i + 1]
    s = (p >= lo) & (p < hi)
    nb = int(s.sum())
    if nb < 20:
        continue
    wr = y[s].mean()
    imp = p[s].mean()
    evs = ev[s]
    boot = [evs[rng.integers(0, nb, nb)].mean() for _ in range(2000)]
    clo, chi = np.percentile(boot, [2.5, 97.5])
    star = "  <-- best" if False else ""
    flag = " ***3%+" if clo > 0.03 else (" **>0" if clo > 0 else "")
    print("%.2f-%.2f  %5d   %4.1f    %4.1f     %+5.1fpp   %+6.2f%%   [%+5.2f%%,%+5.2f%%]   %+5.2f%%%s"
          % (lo, hi, nb, 100 * wr, 100 * imp, 100 * (wr - imp), 100 * evs.mean(),
             100 * clo, 100 * chi, 100 * (evs.mean() - 0.003), flag))
# the deployed band
for lab, lo, hi in (("DEPLOYED 0.76-0.93", 0.76, 0.93), ("band 0.70-0.80", 0.70, 0.80), ("band 0.55-0.70", 0.55, 0.70)):
    s = (p >= lo) & (p < hi)
    if s.sum() < 20:
        continue
    evs = ev[s]; nb = int(s.sum())
    boot = [evs[rng.integers(0, nb, nb)].mean() for _ in range(2000)]
    clo, chi = np.percentile(boot, [2.5, 97.5])
    print("\n%-20s n=%d  win=%.1f%%  EV/$1=%+.2f%%  cost-adj=%+.2f%%  95%%CI[%+.2f%%,%+.2f%%]  %s"
          % (lab, nb, 100 * y[s].mean(), 100 * evs.mean(), 100 * (evs.mean() - 0.003),
             100 * clo, 100 * chi, "REACHES 3%" if clo > 0.03 else ("+EV sig" if clo > 0 else "not sig")))
