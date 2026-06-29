"""Plot every game we ORDERED in the last ~hour: x = minutes into the 5-min window (0-5), y = price of
the side we bought; black dot = where we placed the order (entry/fill price); green line = won (price ->
target 1.0), red = lost (-> 0.0). Dotted line = price paid. Recorder trajectory + ledger entries."""
import sqlite3
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from polybot import database

LED, MD, WINDOW = "archive/ledger.db", "archive/recent_market_data.db", 300.0
lc = sqlite3.connect("file:%s?mode=ro" % LED, uri=True)
mx = lc.execute("SELECT MAX(ts) FROM ledger").fetchone()[0]
cut = mx - 3900
fills = {}
for mid, coin, side, fp, ts in lc.execute(
        "SELECT market_id,coin,side,fill_price,ts FROM ledger WHERE event='FILL' AND mode='LIVE' "
        "AND fill_price>0 AND ts>=? ORDER BY ts", (cut,)):
    fills.setdefault(mid, (coin, side, float(fp), int(ts)))   # first fill per market
lc.close()

db = database.Database(MD)
ends = dict(db.conn.execute("SELECT market_id,end_ts FROM markets WHERE end_ts IS NOT NULL").fetchall())
items = []
for mid, (coin, side, fp, ts) in fills.items():
    m = db.load_market(mid)
    if m is None or mid not in ends:
        continue
    rem = np.asarray(m["rem"], float)
    yesmid = (np.asarray(m["bid_p1"], float) + np.asarray(m["ask_p1"], float)) / 2.0
    price = yesmid if side == "YES" else (1.0 - yesmid)        # the side WE bought
    winner = m.get("winner")
    won = 1 if side == winner else 0
    elapsed = (WINDOW - rem) / 60.0
    entry_min = (WINDOW - (ends[mid] - ts)) / 60.0
    items.append((coin, side, fp, won, elapsed, price, entry_min))
db.close()

items.sort(key=lambda x: (x[0], -x[3]))
n = len(items)
cols = 3
rows = max(1, (n + cols - 1) // cols)
fig, axes = plt.subplots(rows, cols, figsize=(cols * 4.2, rows * 3.0))
axes = np.atleast_1d(axes).ravel()
nwin = sum(x[3] for x in items)
for i, (coin, side, fp, won, elapsed, price, entry_min) in enumerate(items):
    ax = axes[i]
    c = "#0a7d27" if won else "#c0271a"
    ax.plot(elapsed, price, color=c, lw=1.4)
    ax.axhline(fp, ls=":", color="#777", lw=0.9)                # price paid
    ax.axhline(1.0, ls="--", color="#0a7d27", lw=0.7, alpha=0.35)   # target = win
    ax.axhline(0.0, ls="--", color="#c0271a", lw=0.7, alpha=0.35)
    ax.scatter([entry_min], [fp], color="black", s=55, zorder=6, edgecolor="white", linewidth=0.8)
    ax.annotate("ORDER\n%.2f" % fp, (entry_min, fp), fontsize=6.5, xytext=(3, -14),
                textcoords="offset points", color="#222")
    ax.set_xlim(0, 5)
    ax.set_ylim(-0.03, 1.03)
    ax.set_title("%s %s  →  %s" % (coin.upper(), side, "WIN" if won else "LOSS"), fontsize=9, color=c, fontweight="bold")
    ax.set_xlabel("min into window", fontsize=7)
    ax.set_ylabel("bought-side price", fontsize=7)
    ax.tick_params(labelsize=6)
    ax.grid(alpha=0.18)
for j in range(n, len(axes)):
    axes[j].axis("off")
fig.suptitle("flat_favorite — last hour: %d games, %d win / %d loss  (dot=order, dashed=target 1.0)"
             % (n, nwin, n - nwin), fontsize=11, fontweight="bold")
fig.tight_layout(rect=[0, 0, 1, 0.95])
fig.savefig("lasthour_plot.png", dpi=115)
print("saved lasthour_plot.png : %d games (%d win / %d loss)" % (n, nwin, n - nwin))
