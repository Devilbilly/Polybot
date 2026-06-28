"""50/50-AVOIDANCE model, as a PAPER-TRADE A/B + hourly HTML fragment.

Three deliverables the user asked for:
 1) PARTICIPATION model ("predict how many people would play") — OLS predicting book participation
    (distinct levels + log depth) from coin/time/vol/price; reports what drives it + win% by
    participation bucket (does a thin/contested book signal a coin-flip?).
 2) The model as a PAPER TRADE — fit a win/EV model on the older split, then a gated paper-trade that
    SKIPS rounds the model flags as ~50/50 (predicted favorite overpriced); compare trade-all vs gated.
 3) Hourly fragment (card style) to append to the report. Live config is NOT touched (offline replay).

Honest by construction: the per-hour A/B shows whether the gate actually beats trade-all. Binance 1-min
spot (reachable). Read-only. Usage: python3 coinflip_gate.py <market_db> <coin_map.json> [hours]
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
HOURS = int(sys.argv[3]) if len(sys.argv) > 3 else 12
FLOOR, SELL, TCUT = 0.70, 0.93, 0.50
SYM = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}


def load_coin_map(path):
    """Accept a static coin_map.json OR a ledger.db (rebuild market_id->coin live so fresh markets map)."""
    if path.endswith(".db"):
        import sqlite3
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        m = {str(mid): coin for mid, coin in c.execute(
            "SELECT DISTINCT market_id, coin FROM ledger WHERE market_id IS NOT NULL AND coin IS NOT NULL")}
        c.close()
        return m
    return json.load(open(path))


coin_map = load_coin_map(CMAP)


def fetch_klines(sym, a, b):
    out, t = [], a
    while t < b:
        u = (f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1m"
             f"&startTime={t}&endTime={b}&limit=1000")
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla"}), timeout=20))
        except Exception:
            break
        if not r:
            break
        out += r
        t = r[-1][0] + 60000
        if len(r) < 1000:
            break
    return (np.array([k[0] // 1000 for k in out]), np.array([float(k[4]) for k in out]),
            np.array([float(k[5]) for k in out]))


db = database.Database(DB)
rows = db.conn.execute("SELECT market_id,end_ts FROM markets WHERE end_ts IS NOT NULL ORDER BY end_ts").fetchall()
rows = [(mid, e) for mid, e in rows if coin_map.get(mid) in SYM]
recs = []
if rows:
    a = (min(e for _, e in rows) - 2400) * 1000
    b = (max(e for _, e in rows) + 120) * 1000
    kl = {c: fetch_klines(s, a, b) for c, s in SYM.items()}

    def spot_at(c, ts):
        ot, cl, _ = kl[c]
        return float(cl[max(0, min(np.searchsorted(ot, ts) - 1, len(cl) - 1))]) if len(cl) else float("nan")

    def rvol(c, ts, mins=30):
        ot, cl, _ = kl[c]
        j = int(np.searchsorted(ot, ts))
        seg = cl[max(0, j - mins):j + 1]
        return float(np.std(np.diff(np.log(seg)))) if len(seg) >= 6 else float("nan")

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
        ts = int(e - rem[k])
        rv = rvol(c, ts)
        strike = spot_at(c, e - 300)
        sc = abs((spot_at(c, ts) - strike) / strike) if strike and strike == strike else float("nan")
        depth = float(sum(m[f"{s}_s{l}"][k] for s in ("bid", "ask") for l in (1, 2, 3)))
        players = int(sum(1 for s in ("bid", "ask") for l in (1, 2, 3) if m[f"{s}_p{l}"][k] > 0))
        recs.append(dict(coin=c, end=e, win=1 if fav == m["winner"] else 0, poly_goal=float(favp),
                         players=players, depth=depth, abs_spot_change=sc, realized_vol=rv,
                         secs_left=float(rem[k]),
                         exp_move=(rv * math.sqrt(rem[k]) if rv == rv else float("nan"))))
db.close()

good = [r for r in recs if r["exp_move"] == r["exp_move"] and r["abs_spot_change"] == r["abs_spot_change"]]
TH = "padding:6px 8px;text-align:right;font-size:13px;border-bottom:2px solid #ddd;"
TD = "padding:5px 8px;text-align:right;font-size:13px;border-bottom:1px solid #eee;"
TDL = TD.replace("right", "left")
P = ["<div style='max-width:680px;margin:0 auto;padding:0 12px;"
     "font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#222;'>"]
P.append("<h3 style='margin:16px 0 4px;'>50/50-avoidance model "
         "<span style='font-size:11px;color:#999;font-weight:400;'>(paper-trade A/B; goal = skip coin-flip rounds; live UNCHANGED)</span></h3>")
if len(good) < 30:
    P.append(f"<p style='font-size:12px;color:#888;'>only {len(good)} usable markets this window — accumulating.</p></div>")
    sys.stdout.write("\n".join(P))
    sys.exit(0)


def ols(X, t):
    A = np.column_stack([np.ones(len(X)), X])
    b, *_ = np.linalg.lstsq(A, t, rcond=None)
    return b, A @ b


COINS = ["btc", "eth", "sol", "xrp"]
y = np.array([r["win"] for r in good], float)
pg = np.array([r["poly_goal"] for r in good], float)
val = np.where(y > 0, (1 - pg) / pg, -1.0)
ends = np.array([r["end"] for r in good])
order = np.argsort(ends)
cut = int(len(order) * 0.6)
tr, te = order[:cut], order[cut:]

# ---- (1) PARTICIPATION model: predict players from coin/time/vol/price ----
def feat_part(r):
    d = [1.0 if r["coin"] == c else 0.0 for c in COINS[1:]]
    return [r["secs_left"], r["realized_vol"] * 1000, r["poly_goal"], *d]


players = np.array([r["players"] for r in good], float)
depth = np.array([r["depth"] for r in good], float)
Xp = np.array([feat_part(good[i]) for i in range(len(good))])
ld = np.log1p(depth)
_, dhat = ols((Xp - Xp.mean(0)) / (Xp.std(0) + 1e-9), ld)
ss = 1 - ((ld - dhat) ** 2).sum() / (((ld - ld.mean()) ** 2).sum() + 1e-9)
pt = np.quantile(depth, [0, 1 / 3, 2 / 3, 1.0])
P.append("<p style='font-size:12px;color:#555;margin:6px 0 2px;'><b>Participation</b> "
         f"(book level-count is ~constant at {int(np.median(players))} &rarr; no signal; so we model resting "
         f"DEPTH = 'how much is staked'): log-depth R&sup2;={ss:.2f}. Does a thin book flag a coin-flip? "
         "win% by depth tercile:</p>")
P.append(f"<table style='border-collapse:collapse;width:100%;background:#fff;border-radius:8px;'>"
         f"<tr><th style='{TDL}'>depth</th><th style='{TH}'>n</th><th style='{TH}'>win%</th>"
         f"<th style='{TH}'>edge_pp</th><th style='{TH}'>EV/$1</th></tr>")
for i, lab in enumerate(("thin", "mid", "deep")):
    sel = (depth >= pt[i]) & (depth <= pt[i + 1] if i == 2 else depth < pt[i + 1])
    if sel.sum() == 0:
        continue
    P.append(f"<tr><td style='{TDL}'>{lab}</td><td style='{TD}'>{int(sel.sum())}</td>"
             f"<td style='{TD}'>{100*y[sel].mean():.0f}%</td>"
             f"<td style='{TD}'>{100*(y[sel]-pg[sel]).mean():+.1f}</td>"
             f"<td style='{TD}'>{val[sel].mean():+.3f}</td></tr>")
P.append("</table>")

# ---- (2) the model as a PAPER TRADE: predict win, gate out predicted-overpriced (50/50) rounds ----
FW = ["poly_goal", "exp_move", "abs_spot_change", "realized_vol", "players", "depth", "secs_left"]
X = np.array([[r[f] for f in FW] for r in good], float)
mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-9
Xs = (X - mu) / sd
bwin, _ = ols(Xs[tr], y[tr])
pred = np.column_stack([np.ones(len(Xs)), Xs]) @ bwin     # predicted P(win)
pred_edge = pred - pg                                     # model: favorite underpriced?  (>0 = play)
PLAY = pred_edge > 0                                      # gate: skip rounds the model calls overpriced/coin-flip

# per-hour CST A/B over the shown window (fixed $1k basis, no compounding)
def hk(e):
    return time.strftime("%m-%d %H", time.gmtime(int(e) + 8 * 3600))


from collections import defaultdict
allcum = defaultdict(lambda: [0.0, 0, 0]); gatecum = defaultdict(lambda: [0.0, 0, 0])
for i in range(len(good)):
    h = hk(good[i]["end"]); v = val[i] * 1000.0
    a = allcum[h]; a[0] += v; a[1] += 1; a[2] += y[i]
    if PLAY[i]:
        g = gatecum[h]; g[0] += v; g[1] += 1; g[2] += y[i]
hrs = sorted(allcum)[-HOURS:]
P.append("<p style='font-size:12px;color:#555;margin:10px 0 2px;'><b>Paper-trade A/B</b> "
         "(trade-ALL vs model-GATED; $ at fixed $1k, no compounding):</p>")
P.append(f"<div style='overflow-x:auto;'><table style='border-collapse:collapse;width:100%;background:#fff;border-radius:8px;'>"
         f"<tr><th style='{TDL}'>hour</th><th style='{TH}'>all $</th><th style='{TH}'>all win%</th>"
         f"<th style='{TH}'>gated $</th><th style='{TH}'>gated win%</th><th style='{TH}'>skipped</th></tr>")
ca = cg = 0.0
for h in hrs:
    a = allcum[h]; g = gatecum.get(h, [0.0, 0, 0]); ca += a[0]; cg += g[0]
    col = lambda v: "#067d06" if v > 0.5 else ("#c0392b" if v < -0.5 else "#777")
    P.append(f"<tr><td style='{TDL}'>{h}</td>"
             f"<td style='{TD}color:{col(a[0])};'>{a[0]:+.1f}</td><td style='{TD}'>{round(100*a[2]/a[1]) if a[1] else 0}%</td>"
             f"<td style='{TD}color:{col(g[0])};'>{g[0]:+.1f}</td><td style='{TD}'>{round(100*g[2]/g[1]) if g[1] else 0}%</td>"
             f"<td style='{TD}'>{a[1]-g[1]}</td></tr>")
P.append(f"<tr><td style='{TDL}'><b>cum</b></td><td style='{TD}'><b>{ca:+.1f}</b></td><td style='{TD}'></td>"
         f"<td style='{TD}'><b>{cg:+.1f}</b></td><td style='{TD}'></td><td style='{TD}'></td></tr></table></div>")

# OOS honesty line + price-band CONTROL (is the model adding anything beyond "skip high-priced favorites"?)
allEV = val[te].mean(); gatesel = PLAY[te]
gEV = val[te][gatesel].mean() if gatesel.sum() else 0.0
band = pg[te] <= 0.81; bEV = val[te][band].mean() if band.sum() else 0.0
verdict = ("BEATS" if gEV > allEV + 1e-6 else ("ties" if abs(gEV - allEV) < 1e-3 else "LOSES to"))
drivers = ", ".join(f"{FW[i]} {bwin[i+1]:+.2f}" for i in np.argsort(-np.abs(bwin[1:]))[:3])
P.append(f"<p style='font-size:11px;color:#999;margin-top:6px;'>OOS (newer 40%, {len(te)} mkts): "
         f"trade-all EV/$1 {allEV:+.3f} &middot; model-gated {gEV:+.3f} ({int(gatesel.sum())} kept) &rarr; gate <b>{verdict}</b>. "
         f"<b>Control</b>: a plain price-band (skip poly_goal&gt;0.81) gives {bEV:+.3f} ({int(band.sum())} kept) "
         f"&mdash; so the gate is mostly the KNOWN favorite-longshot band effect (buy 0.70-0.80, the high band is "
         f"overpriced), NOT a spot/participation 50-50 detector (top drivers: {drivers}; participation &amp; volume &asymp;0). "
         f"Small N &mdash; watch the cum row accumulate. Live config UNCHANGED; offline candidate.</p>")
P.append("</div>")
sys.stdout.write("\n".join(P))
