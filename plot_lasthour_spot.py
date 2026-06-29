"""Last-hour games, ACTUAL coin spot price (Binance 1s). x = minutes into the 5-min window (0-5),
y = the coin's USD price. Dashed line = STRIKE (spot at window open = the up/down resolution line).
Black dot = where we ordered. Green = we won, red = we lost. The market resolves UP (YES) if the coin
ends above the strike, DOWN (NO) if below."""
import json
import sqlite3
import urllib.request
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from polybot import database

SYM = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}
LED, MD, WINDOW = "archive/ledger.db", "archive/recent_market_data.db", 300

lc = sqlite3.connect("file:%s?mode=ro" % LED, uri=True)
mx = lc.execute("SELECT MAX(ts) FROM ledger").fetchone()[0]
cut = mx - 3900
fills = {}
for mid, coin, side, fp, ts in lc.execute(
        "SELECT market_id,coin,side,fill_price,ts FROM ledger WHERE event='FILL' AND mode='LIVE' "
        "AND fill_price>0 AND ts>=? ORDER BY ts", (cut,)):
    fills.setdefault(mid, (coin, side, float(fp), int(ts)))
lc.close()

# authoritative winner = the LEDGER SETTLE row (the recorder's winner is missing for some markets)
lw = sqlite3.connect("file:%s?mode=ro" % LED, uri=True)
wmap = {mm: w for mm, w in lw.execute(
    "SELECT market_id, winner FROM ledger WHERE event='SETTLE' AND winner IS NOT NULL")}
lw.close()
db = database.Database(MD)
ends = dict(db.conn.execute("SELECT market_id,end_ts FROM markets WHERE end_ts IS NOT NULL").fetchall())
games = []
for mid, (coin, side, fp, ts) in fills.items():
    if mid not in ends or coin not in SYM:
        continue
    games.append((coin, side, fp, ts, int(ends[mid]), wmap.get(mid)))
db.close()


def fetch1s(sym, a_ms, b_ms):
    out, t = [], a_ms
    while t < b_ms:
        u = ("https://api.binance.com/api/v3/klines?symbol=%s&interval=1s&startTime=%d&endTime=%d&limit=1000"
             % (sym, t, b_ms))
        r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla"}), timeout=20))
        if not r:
            break
        out += r
        t = r[-1][0] + 1000
        if len(r) < 1000:
            break
    return np.array([[k[0] // 1000, float(k[4])] for k in out]) if out else np.empty((0, 2))


kl = {}
for coin in set(g[0] for g in games):
    starts = [g[4] - WINDOW for g in games if g[0] == coin]
    endz = [g[4] for g in games if g[0] == coin]
    kl[coin] = fetch1s(SYM[coin], (min(starts) - 5) * 1000, (max(endz) + 5) * 1000)

games.sort(key=lambda g: (g[0], g[3]))
n = len(games)
cols = 3
rows = max(1, (n + cols - 1) // cols)
fig, axes = plt.subplots(rows, cols, figsize=(cols * 4.3, rows * 3.0))
axes = np.atleast_1d(axes).ravel()
nwin = 0
for i, (coin, side, fp, ts, end, winner) in enumerate(games):
    ax = axes[i]
    arr = kl.get(coin, np.empty((0, 2)))
    a = end - WINDOW
    seg = arr[(arr[:, 0] >= a) & (arr[:, 0] <= end)] if len(arr) else arr
    if len(seg) < 2:
        ax.axis("off")
        continue
    t = (seg[:, 0] - a) / 60.0
    px = seg[:, 1]
    strike = seg[0, 1]
    if winner is None:
        c, res = "#999999", "UNSETTLED"
    else:
        won = 1 if side == winner else 0
        nwin += won
        c, res = ("#0a7d27" if won else "#c0271a"), ("WIN" if won else "LOSS")
    ax.plot(t, px, color=c, lw=1.2)
    ax.axhline(strike, ls="--", color="#333", lw=1.0)
    j = int(np.argmin(np.abs(seg[:, 0] - ts)))
    ax.scatter([(seg[j, 0] - a) / 60.0], [seg[j, 1]], color="black", s=55, zorder=6, edgecolor="white", linewidth=0.8)
    mv = 100 * (px[-1] - strike) / strike
    ax.set_title("%s  bet %s  | coin %+.3f%%  | %s" % (coin.upper(), "UP" if side == "YES" else "DOWN", mv,
                                                       res), fontsize=8, color=c, fontweight="bold")
    ax.set_xlabel("min into window", fontsize=7)
    ax.set_ylabel("%s  $" % coin.upper(), fontsize=7)
    ax.tick_params(labelsize=6)
    ax.set_xlim(0, 5)
    ax.grid(alpha=0.18)
    ax.ticklabel_format(useOffset=False, axis="y")
for j in range(n, len(axes)):
    axes[j].axis("off")
fig.suptitle("Last-hour games — ACTUAL coin spot (Binance 1s). dashed = strike (up/down line); dot = order. %d/%d win"
             % (nwin, n), fontsize=10.5, fontweight="bold")
fig.tight_layout(rect=[0, 0, 1, 0.95])
fig.savefig("lasthour_spot.png", dpi=115)
print("saved lasthour_spot.png : %d games, %d win" % (n, nwin))
