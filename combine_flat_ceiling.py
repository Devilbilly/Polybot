"""Deep-dive: does CAPPING the entry price (sell_p ceiling) fix the flat strategy's 'buy too high ->
tiny margin' problem? Faithfully replays the deployed flat logic (decide ONCE at first in-band tick,
take only if flat else skip; mid=(bp1+ap1)/2; lookback 10, flat_tol 0.01) with different ceilings, plus
0.76 trade-all, on the master archive. win-edge = win% - price paid (leverage-free); EV/$1 cost-adj 0.003.
(A) flat margin BY BUY-PRICE BAND; (B) 4-round setup table (avg-first); (C) paired placebo on the winner."""
import sys
import numpy as np
from polybot import database

DB = sys.argv[1] if len(sys.argv) > 1 else "archive/master_polybot.db"
COST, LB, TOL = 0.003, 10, 0.01


def mono(m):
    rem = m.get("rem")
    return True if rem is None or len(rem) < 2 else not bool(np.any(np.diff(rem) > 1.0))


def entry(m, lo, hi, flat_req):
    """Mirror FlatFavorite/FavConvergence: first tick (rem<=150, history>=LB) with favorite ask in
    [lo,hi]; decide once; if flat_req take only when flat else skip. Returns (end_ts, buy_price, win)."""
    rem, bp, ap = m["rem"], m["bid_p1"], m["ask_p1"]
    w = m.get("winner")
    if w not in ("YES", "NO"):
        return None
    mid = (bp + ap) / 2.0
    for i in range(len(rem)):
        if rem[i] > 150 or i < LB:
            continue
        yes_ask, no_ask = ap[i], 1.0 - bp[i]
        yb, nb = (lo <= yes_ask <= hi), (lo <= no_ask <= hi)
        if not (yb or nb):
            continue
        if flat_req and abs(mid[i] - mid[i - LB]) > TOL:
            return None                                  # first in-band tick not flat -> skip market
        side, buy = ("YES", yes_ask) if yb else ("NO", no_ask)
        return (buy, 1 if side == w else 0)
    return None


db = database.Database(DB)
rows = db.conn.execute("SELECT market_id,end_ts FROM markets WHERE end_ts IS NOT NULL ORDER BY end_ts").fetchall()
mk = []
for mid, e in rows:
    m = db.load_market(mid)
    if m is not None and mono(m):
        mk.append((e, m))
db.close()
N = len(mk)
print("markets=%d\n" % N)

# ---- (A) flat-favorite margin BY BUY-PRICE BAND (the user's intuition) ----
flats = [entry(m, 0.55, 0.97, True) for _, m in mk]
flats = [x for x in flats if x]
p = np.array([x[0] for x in flats])
y = np.array([x[1] for x in flats], float)
print("(A) FLAT favorites by BUY-PRICE band  (margin = win%% - price; EV/$1 cost-adj):")
print("  band       n    win%%   price%%   margin    EV/$1")
for lo in (0.55, 0.65, 0.75, 0.85):
    hi = lo + 0.10
    s = (p >= lo) & (p < hi)
    if s.sum() < 10:
        continue
    ev = (np.where(y[s] > 0, (1 - p[s]) / p[s], -1.0) - COST).mean()
    print("  %.2f-%.2f %5d  %5.1f  %6.1f  %+6.1f  %+7.2f%%"
          % (lo, hi, int(s.sum()), 100 * y[s].mean(), 100 * p[s].mean(),
             100 * (y[s].mean() - p[s].mean()), 100 * ev))

# ---- (B) 4-round setup table (avg-first) ----
SETUPS = [("0.76 trade-all (old)", 0.76, 0.93, False),
          ("0.55+flat ceil0.95 (LIVE)", 0.55, 0.95, True),
          ("0.55+flat ceil0.85", 0.55, 0.85, True),
          ("0.55+flat ceil0.80", 0.55, 0.80, True),
          ("0.55+flat ceil0.75", 0.55, 0.75, True),
          ("0.55+flat ceil0.70", 0.55, 0.70, True)]
q = [mk[r * N // 4:(r + 1) * N // 4] for r in range(4)]
print("\n(B) 4-round table (avg of the 4 chronological rounds):")
print("  %-26s %6s %6s %7s %7s %12s %9s" % ("setup", "n/rnd", "fire%", "win%", "buy px", "win-edge pp", "EV/$1"))
best = None
for label, lo, hi, fr in SETUPS:
    per = {k: [] for k in ("n", "fire", "win", "buy", "edge", "ev")}
    for rnd in q:
        es = [entry(m, lo, hi, fr) for _, m in rnd]
        es = [x for x in es if x]
        if len(es) < 4:
            continue
        pr = np.array([x[0] for x in es]); yw = np.array([x[1] for x in es], float)
        per["n"].append(len(es)); per["fire"].append(100 * len(es) / len(rnd))
        per["win"].append(100 * yw.mean()); per["buy"].append(100 * pr.mean())
        per["edge"].append(100 * (yw.mean() - pr.mean()))
        per["ev"].append(100 * (np.where(yw > 0, (1 - pr) / pr, -1.0) - COST).mean())
    if not per["n"]:
        continue
    a = {k: np.mean(v) for k, v in per.items()}
    print("  %-26s %6.0f %5.0f%% %6.1f %6.1f %+8.1f(±%2.0f) %+8.2f%%"
          % (label, a["n"], a["fire"], a["win"], a["buy"], a["edge"], np.std(per["edge"]), a["ev"]))
    if fr and (best is None or a["ev"] > best[1]):
        best = (label, a["ev"], lo, hi)

# ---- (C) paired placebo on the best-EV ceiling variant ----
if best:
    label, _, lo, hi = best
    allflat = [entry(m, lo, hi, True) for _, m in mk]
    allflat_all = [entry(m, 0.55, 0.95, True) for _, m in mk]
    pe = np.array([x[0] for x in allflat if x]); ye = np.array([x[1] for x in allflat if x], float)
    rng = np.random.default_rng(0)
    real = (ye.mean() - pe.mean())
    null = []
    for _ in range(300):
        ys = ye.copy(); rng.shuffle(ys)
        null.append(ys.mean() - pe.mean())
    null = np.array(null)
    print("\n(C) PLACEBO on BEST variant '%s' (n=%d): win-edge %+.2fpp  vs null mean %+.2fpp  p=%.3f -> %s"
          % (label, len(pe), 100 * real, 100 * null.mean(), (null >= real).mean(),
             "REAL" if (null >= real).mean() < 0.05 else "not sig"))
