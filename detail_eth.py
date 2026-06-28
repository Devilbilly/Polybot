"""Zoom into the eth loss CLUSTER: chronological eth trades + 30-min win-rate per coin
(was the eth-bad window eth-only, or market-wide?). Read-only."""
import sqlite3
import time
from collections import defaultdict

c = sqlite3.connect("file:/home/palacedeforsaken/Polybot/ledger.db?mode=ro", uri=True)
rows = c.execute(
    "SELECT f.coin, f.ts, f.side, f.fill_price, s.winner "
    "FROM ledger f LEFT JOIN ledger s ON s.trade_id=f.trade_id AND s.event='SETTLE' "
    "WHERE f.event='FILL' AND f.mode='LIVE' AND f.fill_price>0 ORDER BY f.ts").fetchall()

buckets = defaultdict(lambda: defaultdict(lambda: [0, 0]))   # bucket -> coin -> [settled, wins]
eth = []
for coin, ts, side, fp, win in rows:
    if not win:
        continue
    b = time.strftime("%H:%M", time.gmtime((int(ts) // 1800) * 1800))   # 30-min bucket (UTC)
    buckets[b][coin][0] += 1
    if side == win:
        buckets[b][coin][1] += 1
    if coin == "eth":
        eth.append((ts, side, fp, win, side == win))

print("=== eth trades chronological (UTC HH:MM:SS) ===")
for ts, side, fp, win, won in eth:
    print("  %s  %-3s  entry=%.3f  winner=%-3s  %s"
          % (time.strftime("%H:%M:%S", time.gmtime(ts)), side, fp, win, "win" if won else "LOSS <<<"))

print("\n=== 30-min buckets — settled win-rate per coin (is the eth-bad window eth-only or market-wide?) ===")
print("  %-7s %11s %11s %11s %11s" % ("UTC", "btc", "eth", "sol", "xrp"))
for b in sorted(buckets):
    line = "  %-7s" % b
    for coin in ("btc", "eth", "sol", "xrp"):
        s, w = buckets[b][coin]
        line += " %11s" % (("%d/%d=%2.0f%%" % (w, s, 100 * w / s)) if s else "-")
    print(line)
