"""PROVE whether the 50/50-gate's OOS edge (+0.037 EV/$1) is REAL or OVERFIT noise.

Three decisive tests on the same ~370-market dataset (real markets + Binance spot):
  (A) Honest holdout: fit win-model on older 60%, FREEZE, score newer 40% once. Report gated vs all
      EV/$1, the advantage, a bootstrap 95% CI on the advantage, and a win-rate significance test.
  (B) Permutation null: shuffle the win/loss outcomes (destroying ANY real signal), refit the SAME
      gate procedure N times -> null distribution of the gated-vs-all advantage. p = P(null >= real).
      If the real advantage sits inside the null, the "edge" is what this fitting produces from noise.
  (C) Ablation: does adding the SPOT features beat a price-only model OOS? (Is it a 50/50 detector,
      or just the known favorite-longshot price band?)
Read-only/offline. Binance 1-min spot. Usage: python3 prove_gate.py [market_db] [coin_map_or_ledger]
"""
import json
import math
import sys
import time
import urllib.request

import numpy as np

from polybot import database

DB = sys.argv[1] if len(sys.argv) > 1 else "archive/recent_market_data.db"
CMAP = sys.argv[2] if len(sys.argv) > 2 else "coin_map.json"
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
    recs.append((e, 1 if fav == m["winner"] else 0, fp, rv * math.sqrt(rem[k]), sc, rv, depth, float(rem[k])))
db.close()

recs.sort(key=lambda r: r[0])
y = np.array([r[1] for r in recs], float)
p = np.array([r[2] for r in recs], float)
val = np.where(y > 0, (1 - p) / p, -1.0)
# feature columns: poly_goal, exp_move, abs_spot_change, realized_vol, depth, secs_left
F = np.array([[r[2], r[3], r[4], r[5], r[6], r[7]] for r in recs], float)
n = len(recs)
cut = int(n * 0.6)
tr = np.arange(cut)
te = np.arange(cut, n)
print("markets=%d  train=%d  test=%d" % (n, len(tr), len(te)))


def fit_predict(cols, y_in):
    mu, sd = F[tr][:, cols].mean(0), F[tr][:, cols].std(0) + 1e-9
    Xs = (F[:, cols] - mu) / sd
    A = np.column_stack([np.ones(n), Xs])
    bcoef, *_ = np.linalg.lstsq(A[tr], y_in[tr], rcond=None)
    return A @ bcoef


def gate_adv(pred, y_in, vv):
    play = pred > p                       # model says favorite underpriced
    allEV = vv[te].mean()
    g = play[te]
    gEV = vv[te][g].mean() if g.sum() else 0.0
    return gEV, allEV, gEV - allEV, int(g.sum())


ALL = [0, 1, 2, 3, 4, 5]
pred = fit_predict(ALL, y)
gEV, aEV, adv, kept = gate_adv(pred, y, val)
print("\n(A) HONEST HOLDOUT (train-on-old, score-new ONCE):")
print("    trade-all EV/$1 = %+.4f   gated EV/$1 = %+.4f   ADVANTAGE = %+.4f   (kept %d/%d)"
      % (aEV, gEV, adv, kept, len(te)))
# bootstrap CI on the advantage (resample test rows)
rng = np.random.default_rng(7)
play_te = (pred > p)[te]
vte = val[te]
boot = []
for _ in range(2000):
    idx = rng.integers(0, len(te), len(te))
    vv = vte[idx]; pl = play_te[idx]
    boot.append((vv[pl].mean() if pl.any() else 0.0) - vv.mean())
lo, hi = np.percentile(boot, [2.5, 97.5])
print("    bootstrap 95%% CI on advantage = [%+.4f, %+.4f]  ->  %s"
      % (lo, hi, "excludes 0 (real)" if lo > 0 else "INCLUDES 0 (not significant)"))
# win-rate significance: gated kept vs its own breakeven (mean price)
kp = play_te
wk = y[te][kp]
pk = p[te][kp]
if kp.sum():
    wr = wk.mean(); be = pk.mean()
    se = math.sqrt(wr * (1 - wr) / len(wk))
    z = (wr - be) / se if se > 0 else 0.0
    print("    gated kept: win%%=%.1f  breakeven(avg price)=%.1f  z=%.2f  %s"
          % (100 * wr, 100 * be, z, "edge>0 sig" if z > 1.96 else "NOT sig"))

print("\n(B) PERMUTATION NULL (shuffle outcomes 300x, refit same gate):")
null = []
for i in range(300):
    ys = y.copy()
    rng.shuffle(ys)
    vs = np.where(ys > 0, (1 - p) / p, -1.0)
    pr = fit_predict(ALL, ys)
    _, _, ad, _ = gate_adv(pr, ys, vs)
    null.append(ad)
null = np.array(null)
pval = (null >= adv).mean()
print("    null advantage: mean=%+.4f  sd=%.4f  95th pct=%+.4f" % (null.mean(), null.std(), np.percentile(null, 95)))
print("    REAL advantage = %+.4f   ->   p-value = %.3f   %s"
      % (adv, pval, "REAL (beats noise)" if pval < 0.05 else "CONSISTENT WITH OVERFIT NOISE"))

print("\n(C) ABLATION (does spot add beyond the price band, OOS?):")
for name, cols in (("price-only [poly_goal]", [0]),
                   ("price+depth", [0, 4]),
                   ("price+SPOT (exp_move,absSC,rvol)", [0, 1, 2, 3]),
                   ("ALL features", ALL)):
    pr = fit_predict(cols, y)
    _, aE, ad2, kp2 = gate_adv(pr, y, val)
    print("    %-34s gated-vs-all advantage = %+.4f  (kept %d)" % (name, ad2, kp2))
