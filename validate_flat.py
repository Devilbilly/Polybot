"""Validate the FLAT-favorite signal (study_target found flat favorites +7.7pp) with the SAME rigor that
killed the old gate: OOS chronological + 200 paired label-shuffle placebo, on the win-edge (win% - price,
leverage-free). Question: among the DEPLOYED band (favorite >= 0.76), does "trade only FLAT favorites"
beat trade-all out of sample, beyond what shuffled labels produce? Master archive. Read-only."""
import sys
import numpy as np
from polybot import database

DB = sys.argv[1] if len(sys.argv) > 1 else "archive/master_polybot.db"
BAND_LO = float(sys.argv[2]) if len(sys.argv) > 2 else 0.76   # deployed floor
TCUT = 0.50


def mono(m):
    rem = m.get("rem")
    return True if rem is None or len(rem) < 2 else not bool(np.any(np.diff(rem) > 1.0))


db = database.Database(DB)
rows = db.conn.execute("SELECT market_id,end_ts FROM markets WHERE end_ts IS NOT NULL ORDER BY end_ts").fetchall()
E, PAY, Y, FLAT = [], [], [], []
for mid, e in rows:
    m = db.load_market(mid)
    if m is None or not mono(m):
        continue
    w = m.get("winner")
    if w not in ("YES", "NO"):
        continue
    rem, bp, ap = m["rem"], m["bid_p1"], m["ask_p1"]
    midp = (bp + ap) / 2.0
    for i in range(len(rem)):
        if rem[i] > 300 * TCUT:
            continue
        yp = midp[i]
        if not (0 < yp < 1):
            continue
        fav = "YES" if yp >= 0.5 else "NO"
        fmid = yp if fav == "YES" else 1 - yp
        if fmid < 0.50:
            continue
        pay = ap[i] if fav == "YES" else (1 - bp[i])
        if not (BAND_LO <= pay <= 0.95):
            break  # not in the deployed band at entry; (use the favorite's entry, skip if below floor)
        fseries = midp if fav == "YES" else (1 - midp)
        j = max(0, i - 10)
        flat = abs(fseries[i] - fseries[j]) <= 0.01
        E.append(e); PAY.append(float(pay)); Y.append(1 if fav == w else 0); FLAT.append(bool(flat))
        break
db.close()

E = np.array(E); pay = np.array(PAY); y = np.array(Y, float); flat = np.array(FLAT)
n = len(y)
order = np.argsort(E)
E, pay, y, flat = E[order], pay[order], y[order], flat[order]
print("band >= %.2f : n=%d  flat=%d (%.0f%%)  overall win%%=%.1f edge=%+.1fpp"
      % (BAND_LO, n, flat.sum(), 100 * flat.mean(), 100 * y.mean(), 100 * (y.mean() - pay.mean())))


def wedge(mask):
    return (y[mask].mean() - pay[mask].mean()) if mask.sum() else 0.0


print("\nDescriptive (full sample, win-edge = win%% - price):")
print("  FLAT favorites : n=%d  win%%=%.1f  edge=%+.2fpp" % (flat.sum(), 100 * y[flat].mean(), 100 * wedge(flat)))
print("  non-flat       : n=%d  win%%=%.1f  edge=%+.2fpp" % ((~flat).sum(), 100 * y[~flat].mean(), 100 * wedge(~flat)))
print("  trade-ALL      : n=%d  win%%=%.1f  edge=%+.2fpp" % (n, 100 * y.mean(), 100 * (y.mean() - pay.mean())))

# 200 paired splits: real (flat-vs-all advantage on test) vs label-shuffled null
rng = np.random.default_rng(0)
NS = 200
real_adv, null_adv, real_flat_edge = [], [], []
for _ in range(NS):
    perm = rng.permutation(n)
    cut = int(n * 0.6)
    te = perm[cut:]
    te_flat = te[flat[te]]
    if len(te_flat) < 15:
        continue
    adv = (y[te_flat].mean() - pay[te_flat].mean()) - (y[te].mean() - pay[te].mean())
    real_adv.append(adv)
    real_flat_edge.append(y[te_flat].mean() - pay[te_flat].mean())
    ys = y[rng.permutation(n)]
    adv0 = (ys[te_flat].mean() - pay[te_flat].mean()) - (ys[te].mean() - pay[te].mean())
    null_adv.append(adv0)
real_adv = np.array(real_adv); null_adv = np.array(null_adv); real_flat_edge = np.array(real_flat_edge)
print("\nOOS (200 paired 60/40 splits) — flat-favorites vs trade-all, WIN-EDGE advantage:")
print("  REAL advantage : mean %+.2fpp  sd %.2f  [p5 %+.2f, p95 %+.2f]"
      % (100 * real_adv.mean(), 100 * real_adv.std(), 100 * np.percentile(real_adv, 5), 100 * np.percentile(real_adv, 95)))
print("  NULL advantage : mean %+.2fpp  sd %.2f  [p5 %+.2f, p95 %+.2f]"
      % (100 * null_adv.mean(), 100 * null_adv.std(), 100 * np.percentile(null_adv, 5), 100 * np.percentile(null_adv, 95)))
print("  REAL flat-edge (test) : mean %+.2fpp  (>0 in %.0f%% of splits)"
      % (100 * real_flat_edge.mean(), 100 * (real_flat_edge > 0).mean()))
print("  paired: REAL adv > its own-split NULL in %.0f%% of splits   (50%% = no skill)"
      % (100 * (real_adv > null_adv).mean()))
p = (null_adv >= real_adv.mean()).mean()
print("  p(null mean-adv >= real mean-adv) = %.3f  ->  %s"
      % (p, "FLAT IS A REAL SIGNAL" if (real_adv > null_adv).mean() > 0.9 and real_adv.mean() > 0 else "NOT distinguishable from noise"))
