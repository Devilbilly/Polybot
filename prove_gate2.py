"""Rigorous version: 200 PAIRED splits. For each random 60/40 split, evaluate the REAL gate AND a
label-shuffled NULL gate on the SAME split -> two matched distributions, not 1-vs-many. Two metrics:
  - EV/$1 advantage (gated - all): mechanically leverage-confounded, shown for completeness.
  - WIN-EDGE = gated-kept win% - the avg price they paid (breakeven): LEVERAGE-FREE. If the gate has
    real predictive skill, kept favorites win MORE than they were priced, out of sample. This is the
    honest test of "can we pick winners". Compared to trade-all's own win-edge and to the null.
Read-only/offline. Binance 1-min spot."""
import json
import math
import sys
import urllib.request

import numpy as np

from polybot import database

DB = sys.argv[1] if len(sys.argv) > 1 else "archive/recent_market_data.db"
CMAP = sys.argv[2] if len(sys.argv) > 2 else "coin_map.json"
NS = int(sys.argv[3]) if len(sys.argv) > 3 else 200
FLOOR, SELL, TCUT = 0.70, 0.93, 0.50
SYM = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}


def load_coin_map(path):
    if path.endswith(".db"):
        import sqlite3
        c = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        m = {str(mid): coin for mid, coin in c.execute(
            "SELECT DISTINCT market_id, coin FROM ledger WHERE market_id IS NOT NULL AND coin IS NOT NULL")}
        c.close()
        return m
    return json.load(open(path))


coin_map = load_coin_map(CMAP)


def fetch(sym, a, b):
    out, t = [], a
    while t < b:
        u = ("https://api.binance.com/api/v3/klines?symbol=%s&interval=1m&startTime=%d&endTime=%d&limit=1000"
             % (sym, t, b))
        r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla"}), timeout=20))
        if not r:
            break
        out += r
        t = r[-1][0] + 60000
        if len(r) < 1000:
            break
    return (np.array([k[0] // 1000 for k in out]), np.array([float(k[4]) for k in out]))


db = database.Database(DB)
rows = [(m, e) for m, e in db.conn.execute(
    "SELECT market_id,end_ts FROM markets WHERE end_ts IS NOT NULL ORDER BY end_ts").fetchall()
    if coin_map.get(m) in SYM]
a = (min(e for _, e in rows) - 2400) * 1000
b = (max(e for _, e in rows) + 120) * 1000
kl = {c: fetch(s, a, b) for c, s in SYM.items()}


def spot(c, ts):
    ot, cl = kl[c]
    return float(cl[max(0, min(np.searchsorted(ot, ts) - 1, len(cl) - 1))]) if len(cl) else float("nan")


def rvol(c, ts, m=30):
    ot, cl = kl[c]
    j = int(np.searchsorted(ot, ts))
    seg = cl[max(0, j - m):j + 1]
    return float(np.std(np.diff(np.log(seg)))) if len(seg) >= 6 else float("nan")


recs = []
for mid, e in rows:
    c = coin_map[mid]
    m = db.load_market(mid)
    if m is None:
        continue
    rem, bp, ap = m["rem"], m["bid_p1"], m["ask_p1"]
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
        if FLOOR <= fp <= SELL:
            ent = (k, fav, fp)
            break
    if ent is None:
        continue
    k, fav, fp = ent
    ts = int(e - rem[k])
    rv = rvol(c, ts)
    strike = spot(c, e - 300)
    sc = abs((spot(c, ts) - strike) / strike) if strike and strike == strike else float("nan")
    depth = float(sum(m["%s_s%d" % (s, l)][k] for s in ("bid", "ask") for l in (1, 2, 3)))
    if rv != rv or sc != sc:
        continue
    recs.append((1 if fav == m["winner"] else 0, fp, rv * math.sqrt(rem[k]), sc, rv, depth, float(rem[k])))
db.close()

y = np.array([r[0] for r in recs], float)
p = np.array([r[1] for r in recs], float)
val = np.where(y > 0, (1 - p) / p, -1.0)
F = np.array([[r[1], r[2], r[3], r[4], r[5], r[6]] for r in recs], float)
n = len(recs)
COLS = [0, 1, 2, 3, 4, 5]
print("markets=%d   NS=%d paired splits (60/40 random)" % (n, NS))


def run(yv, valv, tr, te):
    mu, sd = F[tr][:, COLS].mean(0), F[tr][:, COLS].std(0) + 1e-9
    A = np.column_stack([np.ones(n), (F[:, COLS] - mu) / sd])
    bc, *_ = np.linalg.lstsq(A[tr], yv[tr], rcond=None)
    pred = A @ bc
    pl = pred > p
    tp = te[pl[te]]
    allEV = valv[te].mean()
    gEV = valv[tp].mean() if len(tp) else 0.0
    we = (yv[tp].mean() - p[tp].mean()) if len(tp) else 0.0       # leverage-free: win% - price paid
    return gEV - allEV, we, len(tp)


rng = np.random.default_rng(0)
r_adv, r_we, n_adv, n_we, kept = [], [], [], [], []
all_we = []
for _ in range(NS):
    perm = rng.permutation(n)
    cut = int(n * 0.6)
    tr, te = perm[:cut], perm[cut:]
    a1, w1, k1 = run(y, val, tr, te)
    r_adv.append(a1); r_we.append(w1); kept.append(k1)
    all_we.append(y[te].mean() - p[te].mean())
    ys = y[rng.permutation(n)]
    vs = np.where(ys > 0, (1 - p) / p, -1.0)
    a2, w2, _ = run(ys, vs, tr, te)
    n_adv.append(a2); n_we.append(w2)

r_adv, r_we, n_adv, n_we, all_we = map(np.array, (r_adv, r_we, n_adv, n_we, all_we))


def stat(x):
    return "mean=%+.4f  sd=%.4f  [p5 %+.4f, p95 %+.4f]" % (x.mean(), x.std(), np.percentile(x, 5), np.percentile(x, 95))


print("\n=== EV/$1 advantage (gated - all) — leverage-CONFOUNDED ===")
print("  REAL : %s" % stat(r_adv))
print("  NULL : %s" % stat(n_adv))
print("  paired: REAL beats its own-split NULL in %.0f%% of splits  (50%% = no skill)" % (100 * (r_adv > n_adv).mean()))

print("\n=== WIN-EDGE = gated-kept win%% minus price paid — LEVERAGE-FREE (the honest test) ===")
print("  REAL gated  : %s" % stat(100 * r_we) + "  (pp)")
print("  NULL gated  : %s" % stat(100 * n_we) + "  (pp)")
print("  trade-ALL   : %s" % stat(100 * all_we) + "  (pp)")
print("  paired: REAL win-edge beats its own-split NULL in %.0f%% of splits" % (100 * (r_we > n_we).mean()))
print("  REAL gated win-edge > 0 in %.0f%% of splits ; > trade-all in %.0f%%"
      % (100 * (r_we > 0).mean(), 100 * (r_we > all_we).mean()))
print("  avg kept = %.0f / %d test trades" % (np.mean(kept), int(n * 0.4)))
