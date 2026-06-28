"""Learn a SKIP/PLAY rule for the favorite-longshot trade as f(poly_goal, spot_change, vol, volume,
players, ...). Hypothesis (from the XRP dig): when the underlying barely moves, the 5-min up/down is a
near-coin-flip but the book still prices the favorite at 0.76-0.93 -> favorite OVERpriced -> -EV. So the
discriminator is the underlying's MOVE MAGNITUDE / realized vol, possibly interacted with poly_goal.

Per recorded market: replicate the entry (first tick after time_cutoff where the favorite price in
[FLOOR,SELL]); extract decision-time features from the book + Binance spot; outcome = did the favorite
win. Then: OLS feature importance (win and edge=win-poly_goal), bucket calibration, and an OOS skip rule.
Read-only; offline. Binance 1-min klines (reachable)."""
import json
import math
import time
import urllib.request

import numpy as np

from polybot import database

DB = "archive/recent_market_data.db"
FLOOR, SELL, TCUT = 0.70, 0.93, 0.50          # entry: favorite price in [FLOOR,SELL] after rem<=150s
SYM = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}
coin_map = json.load(open("coin_map.json"))


def fetch_klines(sym, start_ms, end_ms):
    out, t = [], start_ms
    while t < end_ms:
        u = (f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1m"
             f"&startTime={t}&endTime={end_ms}&limit=1000")
        r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla"}), timeout=20))
        if not r:
            break
        out += r
        t = r[-1][0] + 60000
        if len(r) < 1000:
            break
    ot = np.array([k[0] // 1000 for k in out])
    cl = np.array([float(k[4]) for k in out])
    vol = np.array([float(k[5]) for k in out])
    return ot, cl, vol


db = database.Database(DB)
rows = db.conn.execute("SELECT market_id,end_ts FROM markets WHERE end_ts IS NOT NULL ORDER BY end_ts").fetchall()
rows = [(mid, e) for mid, e in rows if coin_map.get(mid) in SYM]
start = min(e for _, e in rows) - 2400
end = max(e for _, e in rows) + 120
kl = {c: fetch_klines(s, start * 1000, end * 1000) for c, s in SYM.items()}
print("klines/coin:", {c: len(kl[c][0]) for c in kl})


def spot_at(c, ts):
    ot, cl, _ = kl[c]
    i = max(0, min(np.searchsorted(ot, ts) - 1, len(cl) - 1))
    return float(cl[i])


def rvol(c, ts, mins=30):
    ot, cl, _ = kl[c]
    j = int(np.searchsorted(ot, ts))
    seg = cl[max(0, j - mins):j + 1]
    return float(np.std(np.diff(np.log(seg)))) if len(seg) >= 6 else np.nan


def bvol(c, ts, mins=30):
    ot, _, vol = kl[c]
    j = int(np.searchsorted(ot, ts))
    return float(np.sum(vol[max(0, j - mins):j + 1]))


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
        favp = yp if fav == "YES" else 1 - yp
        if FLOOR <= favp <= SELL:
            ent = (k, fav, favp)
            break
    if ent is None:
        continue
    k, fav, favp = ent
    win = 1 if fav == m["winner"] else 0
    ts = int(e - rem[k])
    strike = spot_at(c, e - 300)
    spot_e = spot_at(c, ts)
    sc = (spot_e - strike) / strike if strike > 0 else np.nan
    rv = rvol(c, ts)
    recs.append(dict(
        coin=c, end=e, win=win, poly_goal=float(favp),
        spread=float(ap[k] - bp[k]),
        depth_all=float(sum(m[f"{s}_s{l}"][k] for s in ("bid", "ask") for l in (1, 2, 3))),
        players=int(sum(1 for s in ("bid", "ask") for l in (1, 2, 3) if m[f"{s}_p{l}"][k] > 0)),
        abs_spot_change=abs(sc), realized_vol=rv, bvol=bvol(c, ts), secs_left=float(rem[k]),
        exp_move=(rv * math.sqrt(rem[k]) if rv == rv else np.nan),   # vol(1m) * sqrt(secs_left) ~ expected terminal move
    ))
db.close()

FEATS = ["poly_goal", "abs_spot_change", "realized_vol", "exp_move", "bvol", "depth_all", "players", "spread", "secs_left"]
good = [r for r in recs if all(r[f] == r[f] for f in FEATS)]    # drop NaN
print(f"\nmarkets entered: {len(recs)}  usable (no NaN): {len(good)}")
y = np.array([r["win"] for r in good], float)
p = np.array([r["poly_goal"] for r in good], float)
edge = y - p                                                    # realized win - implied prob (>0 = favorite underpriced)
val = np.where(y > 0, (1 - p) / p, -1.0)                        # $ per $1 staked buying favorite at p
print(f"overall: win={100*y.mean():.1f}%  mean implied(poly_goal)={100*p.mean():.1f}%  "
      f"edge={100*edge.mean():+.1f}pp  EV/$1={val.mean():+.3f}")

X = np.array([[r[f] for f in FEATS] for r in good], float)
Xs = (X - X.mean(0)) / (X.std(0) + 1e-9)


def ols(Xs, t):
    A = np.column_stack([np.ones(len(Xs)), Xs])
    b, *_ = np.linalg.lstsq(A, t, rcond=None)
    return b[1:]


print("\nStandardized OLS coefficients (bigger |coef| = more influence):")
bw, be = ols(Xs, y), ols(Xs, edge)
print(f"  {'feature':<16}{'->win':>10}{'->edge':>10}")
for i, f in sorted(enumerate(FEATS), key=lambda kv: -abs(be[kv[0]])):
    print(f"  {f:<16}{bw[i]:>+10.3f}{be[i]:>+10.3f}")

print("\nCalibration by exp_move quartile (expected terminal move = realized_vol*sqrt(secs_left)):")
q = np.quantile([r["exp_move"] for r in good], [0, .25, .5, .75, 1.0])
print(f"  {'bucket':<22}{'n':>5}{'win%':>7}{'implied%':>9}{'edge_pp':>8}{'EV/$1':>8}")
for i in range(4):
    lo, hi = q[i], q[i + 1]
    sel = np.array([(r["exp_move"] >= lo) and (r["exp_move"] <= hi if i == 3 else r["exp_move"] < hi) for r in good])
    if sel.sum() == 0:
        continue
    print(f"  [{lo:.4f},{hi:.4f}){'':<2}{sel.sum():>5}{100*y[sel].mean():>7.1f}{100*p[sel].mean():>9.1f}"
          f"{100*edge[sel].mean():>+8.1f}{val[sel].mean():>+8.3f}")

print("\nOOS skip rule (chronological 60/40 split; rule fit on TRAIN, scored on TEST):")
order = np.argsort([r["end"] for r in good])
cut = int(len(order) * 0.6)
tr, te = order[:cut], order[cut:]
# candidate rule: SKIP if exp_move < thr (sweep thr on train to maximize train total EV)
em = np.array([r["exp_move"] for r in good])
ths = np.quantile(em[tr], np.linspace(0.05, 0.6, 23))
best = max(ths, key=lambda t: val[tr][em[tr] >= t].sum())
keep_te = em[te] >= best
print(f"  best TRAIN threshold: skip if exp_move < {best:.4f}")
print(f"  TEST trade-all : n={len(te):>4}  win={100*y[te].mean():.1f}%  EV/$1={val[te].mean():+.3f}  totalEV={val[te].sum():+.1f}")
print(f"  TEST with rule : kept={keep_te.sum():>3}/{len(te)}  win={100*y[te][keep_te].mean():.1f}%  "
      f"EV/$1={val[te][keep_te].mean():+.3f}  totalEV={val[te][keep_te].sum():+.1f}  "
      f"(skipped {(~keep_te).sum()} trades worth {val[te][~keep_te].sum():+.1f})")
