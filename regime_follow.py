"""Last legitimate route to 3%: the edge is regime-switching (+5% / -6% across halves). If regimes are
PERSISTENT (autocorrelated), a causal trailing filter (trade only when the strategy's recent win-rate is
above breakeven) harvests the good regimes and skips the bad -> lifts effective EV. If regimes are i.i.d.
(autocorr~0), no such filter can work. Tests: (1) lag autocorrelation of hourly EV; (2) trailing-winrate
filter, OOS (tune on first 60%, apply last 40%); (3) PLACEBO = same filter on TIME-SHUFFLED trades (kills
regime structure) -> if real benefit ~= shuffled benefit, the filter isn't following real persistence.
Master archive, ask-priced, cost-adj. Read-only."""
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
data = []
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
        pay = ap[k] if fav == "YES" else (1 - bp[k])
        if 0.76 <= pay <= 0.95:                       # the DEPLOYED band (regime-switching one)
            data.append((e, 1 if fav == w else 0, (1 - pay) / pay if fav == w else -1.0))
            break
db.close()

data.sort(key=lambda r: r[0])
ets = np.array([d[0] for d in data])
y = np.array([d[1] for d in data], float)
ev = np.array([d[2] for d in data]) - COST
n = len(ev)
print("deployed-band trades = %d (ask-priced, cost-adj). overall EV/$1 = %+.2f%%  win=%.1f%%"
      % (n, 100 * ev.mean(), 100 * y.mean()))

# (1) autocorrelation of per-hour mean EV
hr = (ets // 3600).astype(int)
uh = np.unique(hr)
hev = np.array([ev[hr == h].mean() for h in uh])
for lag in (1, 2, 3):
    a, b = hev[:-lag], hev[lag:]
    r = np.corrcoef(a, b)[0, 1] if len(a) > 3 else float("nan")
    print("  per-hour EV autocorrelation lag-%d r = %+.3f  (n_hours=%d)" % (lag, r, len(hev)))
# per-trade lag-1
r1 = np.corrcoef(ev[:-1], ev[1:])[0, 1]
print("  per-trade EV autocorrelation lag-1 r = %+.3f" % r1)


def trailing_filter(ev_in, y_in, K, thr):
    """causal: trade index i only if win-rate of the K trades strictly before i >= thr."""
    traded = np.zeros(len(ev_in), bool)
    for i in range(len(ev_in)):
        if i < K:
            traded[i] = True                          # warmup: trade
        else:
            traded[i] = y_in[i - K:i].mean() >= thr
    return traded


cut = int(n * 0.6)
# (2) tune K, thr on train to maximize filtered mean-EV, apply to test
best = (None, None, -9)
for K in (10, 20, 40, 80):
    for thr in (0.74, 0.78, 0.80, 0.82):
        tr = trailing_filter(ev[:cut], y[:cut], K, thr)
        if tr.sum() < 20:
            continue
        m = ev[:cut][tr].mean()
        if m > best[2]:
            best = (K, thr, m)
K, thr, _ = best
tr_te = trailing_filter(ev, y, K, thr)[cut:]
all_te = ev[cut:].mean()
filt_te = ev[cut:][tr_te].mean() if tr_te.sum() else 0.0
print("\n(2) OOS trailing-filter (tuned K=%d thr=%.2f on train):" % (K, thr))
print("    TEST trade-all EV = %+.2f%%   filtered EV = %+.2f%%   (kept %d/%d)   benefit = %+.2f%%"
      % (100 * all_te, 100 * filt_te, int(tr_te.sum()), len(tr_te), 100 * (filt_te - all_te)))

# (3) placebo: shuffle TIME ORDER (destroy regime persistence), redo tuned filter on test slice
rng = np.random.default_rng(5)
benefit_real = filt_te - all_te
null = []
for _ in range(300):
    perm = rng.permutation(n)
    evs, ys = ev[perm], y[perm]
    trs = trailing_filter(evs, ys, K, thr)[cut:]
    a = evs[cut:].mean()
    f = evs[cut:][trs].mean() if trs.sum() else 0.0
    null.append(f - a)
null = np.array(null)
print("(3) PLACEBO (time-shuffled, regime structure destroyed):")
print("    real benefit = %+.2f%%   null benefit mean = %+.2f%% sd %.2f   p(null>=real) = %.3f  -> %s"
      % (100 * benefit_real, 100 * null.mean(), 100 * null.std(), (null >= benefit_real).mean(),
         "REGIME-FOLLOWING WORKS" if (null >= benefit_real).mean() < 0.05 and benefit_real > 0 else "NO real persistence to harvest"))
