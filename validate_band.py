"""Validate the mild-favorite (0.55-0.70) edge before believing it. Decisive checks:
  - price PAID = the favorite's ASK (what you'd really pay), minus costs 0.003 (fee+slip).
  - OOS: chronological 60/40 split by end_ts -> does the band edge hold on UNSEEN later data?
  - PLACEBO: shuffle the win labels -> the band edge must vanish (else it's an artifact).
  - data-quality flag: the 0.50-0.55 "40% win" anomaly -> inspect.
On the master archive (~5200 mkts). Monotonic-rem filter. Read-only."""
import sys

import numpy as np

from polybot import database

DB = sys.argv[1] if len(sys.argv) > 1 else "archive/master_polybot.db"
COST = 0.003
TCUT = 0.50


def mono(m):
    rem = m.get("rem")
    return True if rem is None or len(rem) < 2 else not bool(np.any(np.diff(rem) > 1.0))


db = database.Database(DB)
rows = db.conn.execute("SELECT market_id,end_ts FROM markets WHERE end_ts IS NOT NULL").fetchall()
ets, paid, won = [], [], []
for mid, e in rows:
    m = db.load_market(mid)
    if m is None or not mono(m):
        continue
    w = m.get("winner")
    if w not in ("YES", "NO"):
        continue
    rem, bp, ap = m["rem"], m["bid_p1"], m["ask_p1"]
    midp = (bp + ap) / 2.0
    for k in range(len(rem)):
        if rem[k] > 300 * TCUT:
            continue
        yp = midp[k]
        if yp <= 0 or yp >= 1:
            continue
        fav = "YES" if yp >= 0.5 else "NO"
        fmid = yp if fav == "YES" else 1 - yp
        pay = ap[k] if fav == "YES" else (1 - bp[k])     # the favorite's ASK = price actually paid
        if 0.50 <= fmid <= 0.95 and 0.50 <= pay <= 0.99:
            ets.append(e); paid.append(float(pay)); won.append(1 if fav == w else 0)
            break
db.close()

ets = np.array(ets); pay = np.array(paid); y = np.array(won, float)
order = np.argsort(ets)
ets, pay, y = ets[order], pay[order], y[order]
ev = np.where(y > 0, (1 - pay) / pay, -1.0) - COST
n = len(pay)
cut = int(n * 0.6)
print("entries=%d  train(older)=%d  test(newer)=%d  (ASK-priced, cost %.3f)" % (n, cut, n - cut, COST))


def band_ev(sel, rng):
    nb = int(sel.sum())
    if nb < 15:
        return None
    e = ev[sel]
    boot = [e[rng.integers(0, nb, nb)].mean() for _ in range(2000)]
    return nb, e.mean(), np.percentile(boot, 2.5), np.percentile(boot, 97.5), y[sel].mean()


rng = np.random.default_rng(3)
print("\n%-12s %-26s %-26s" % ("band(ask)", "TRAIN EV [CI]", "TEST/OOS EV [CI]"))
for lo, hi in ((0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 0.76), (0.76, 0.93)):
    out = []
    for idx in (np.arange(cut), np.arange(cut, n)):
        s = np.zeros(n, bool); s[idx] = (pay[idx] >= lo) & (pay[idx] < hi)
        r = band_ev(s, rng)
        out.append("n=%-4d %+6.2f%% [%+5.1f,%+5.1f]" % (r[0], 100 * r[1], 100 * r[2], 100 * r[3]) if r else "n<15")
    print("%.2f-%.2f   %-26s %-26s" % (lo, hi, out[0], out[1]))

print("\nPLACEBO (shuffle wins; the 0.55-0.70 'edge' must vanish):")
band = (pay >= 0.55) & (pay < 0.70)
real = ev[band].mean()
null = []
for _ in range(300):
    ys = y.copy(); rng.shuffle(ys)
    evs = np.where(ys > 0, (1 - pay) / pay, -1.0) - COST
    null.append(evs[band].mean())
null = np.array(null)
print("  REAL 0.55-0.70 EV = %+.2f%%   NULL mean = %+.2f%% sd %.2f   p(null>=real) = %.3f  -> %s"
      % (100 * real, 100 * null.mean(), 100 * null.std(), (null >= real).mean(),
         "REAL" if (null >= real).mean() < 0.05 else "ARTIFACT"))

print("\nData-quality flag: 0.50-0.55 band win-rate by source-consistency:")
s = (pay >= 0.50) & (pay < 0.55)
print("  n=%d  win=%.1f%%  (a favorite winning <50%% = suspicious; likely choppy coin-flips or stale prices)"
      % (int(s.sum()), 100 * y[s].mean()))
