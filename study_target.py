"""DEEP descriptive study of the prediction target P(favorite wins) and the variables, on the full
master archive (~6300 markets). NOT an edge hunt — a characterization of the data-generating process:
  (1) CALIBRATION: realized win% vs price paid (favorite-longshot curve); residual edge = win% - price,
      with bootstrap CIs. Where is the target actually mispriced?
  (2) TRAJECTORY (new — uses the path, not just the entry snapshot): rising vs fading favorite at entry;
      how long it's been the favorite; how far winners vs losers reverse after entry.
  (3) STABILITY: is the calibration/edge stable across 3 chronological thirds, or drifting (non-stationarity)?
Monotonic-rem filter. Read-only/offline."""
import sys
import numpy as np
from polybot import database

DB = sys.argv[1] if len(sys.argv) > 1 else "archive/master_polybot.db"
TCUT = 0.50


def mono(m):
    rem = m.get("rem")
    return True if rem is None or len(rem) < 2 else not bool(np.any(np.diff(rem) > 1.0))


db = database.Database(DB)
rows = db.conn.execute("SELECT market_id,end_ts FROM markets WHERE end_ts IS NOT NULL ORDER BY end_ts").fetchall()
rec = []   # (end_ts, price_paid, fav_mid, win, dir, ticks_as_fav, max_after, min_after)
for mid, e in rows:
    m = db.load_market(mid)
    if m is None or not mono(m):
        continue
    w = m.get("winner")
    if w not in ("YES", "NO"):
        continue
    rem, bp, ap = m["rem"], m["bid_p1"], m["ask_p1"]
    midp = (bp + ap) / 2.0
    k = None
    for i in range(len(rem)):
        if rem[i] > 300 * TCUT:
            continue
        yp = midp[i]
        if 0 < yp < 1:
            fav = "YES" if yp >= 0.5 else "NO"
            fmid = yp if fav == "YES" else 1 - yp
            if 0.55 <= fmid <= 0.95:
                k = i
                break
    if k is None:
        continue
    fav = "YES" if midp[k] >= 0.5 else "NO"
    fmid = midp[k] if fav == "YES" else 1 - midp[k]
    pay = ap[k] if fav == "YES" else (1 - bp[k])
    win = 1 if fav == w else 0
    # favorite-price series (same side) over the whole window
    fseries = midp if fav == "YES" else (1 - midp)
    j = max(0, k - 10)
    delta = fseries[k] - fseries[j]
    direction = "rising" if delta > 0.01 else ("fading" if delta < -0.01 else "flat")
    taf = 0
    i = k - 1
    while i >= 0 and fseries[i] >= 0.50:
        taf += 1
        i -= 1
    after = fseries[k:]
    rec.append((e, float(pay), float(fmid), win, direction, taf,
                float(after.max()) if len(after) else fmid, float(after.min()) if len(after) else fmid))
db.close()

E = np.array([r[0] for r in rec]); pay = np.array([r[1] for r in rec]); fmid = np.array([r[2] for r in rec])
y = np.array([r[3] for r in rec], float); direction = np.array([r[4] for r in rec])
taf = np.array([r[5] for r in rec]); mx = np.array([r[6] for r in rec]); mn = np.array([r[7] for r in rec])
n = len(rec)
rng = np.random.default_rng(0)


def ci(mask):
    s = y[mask]
    if len(s) < 15:
        return None
    boot = [s[rng.integers(0, len(s), len(s))].mean() for _ in range(1500)]
    return len(s), s.mean(), np.percentile(boot, 2.5), np.percentile(boot, 97.5)


print("usable markets = %d  (overall win%% = %.1f, mean price paid = %.3f, edge = %+.1fpp)"
      % (n, 100 * y.mean(), pay.mean(), 100 * (y.mean() - pay.mean())))

print("\n(1) CALIBRATION — realized win%% vs price PAID  (edge = win%% - price; >0 = underpriced):")
print("  price band     n     win%%   price%%   edge_pp   95%%CI(win)        EV/$1")
for lo in np.arange(0.55, 0.95, 0.05):
    hi = lo + 0.05
    s = (pay >= lo) & (pay < hi)
    r = ci(s)
    if not r:
        continue
    ev = (np.where(y[s] > 0, (1 - pay[s]) / pay[s], -1.0)).mean()
    print("  %.2f-%.2f   %5d   %4.1f   %5.1f   %+6.1f   [%4.1f,%4.1f]   %+6.2f%%"
          % (lo, hi, r[0], 100 * r[1], 100 * pay[s].mean(), 100 * (r[1] - pay[s].mean()),
             100 * r[2], 100 * r[3], 100 * ev))

print("\n(2a) TRAJECTORY — favorite RISING vs FADING at entry (mid now vs 10 ticks ago):")
print("  dir       n     win%%   price%%   edge_pp   95%%CI")
for d in ("rising", "flat", "fading"):
    s = direction == d
    r = ci(s)
    if r:
        print("  %-7s %5d   %4.1f   %5.1f   %+6.1f   [%4.1f,%4.1f]"
              % (d, r[0], 100 * r[1], 100 * pay[s].mean(), 100 * (r[1] - pay[s].mean()), 100 * r[2], 100 * r[3]))

print("\n(2b) TIME-AS-FAVORITE before entry (consecutive ticks this side held >=0.50):")
for lo, hi, lab in ((0, 20, "new <20t"), (20, 60, "20-60t"), (60, 9999, "established 60t+")):
    s = (taf >= lo) & (taf < hi)
    r = ci(s)
    if r:
        print("  %-16s n=%5d  win%%=%.1f  edge=%+.1fpp  CI[%4.1f,%4.1f]"
              % (lab, r[0], 100 * r[1], 100 * (r[1] - pay[s].mean()), 100 * r[2], 100 * r[3]))

print("\n(2c) REVERSAL characterization — how far the favorite moves AFTER entry (winners vs losers):")
for lab, m_ in (("winners", y > 0), ("losers", y == 0)):
    print("  %-8s avg max-after=%.3f  avg min-after=%.3f  (entry mid avg=%.3f)"
          % (lab, mx[m_].mean(), mn[m_].mean(), fmid[m_].mean()))
print("  -> losers' min-after shows how deep the collapse goes; winners hold/rise.")

print("\n(3) STABILITY — edge (win%% - price) across 3 chronological thirds (drift = non-stationary):")
order = np.argsort(E)
for t, lab in enumerate(("oldest", "middle", "newest")):
    idx = order[t * n // 3:(t + 1) * n // 3]
    s = np.zeros(n, bool); s[idx] = True
    r = ci(s)
    if r:
        edge = r[1] - pay[s].mean()
        print("  %-7s n=%5d  win%%=%.1f  price%%=%.1f  edge=%+.1fpp  CI(win)[%4.1f,%4.1f]"
              % (lab, r[0], 100 * r[1], 100 * pay[s].mean(), 100 * edge, 100 * r[2], 100 * r[3]))
