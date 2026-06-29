#!/usr/bin/env python3
"""Flag EXTRAORDINARY per-coin hourly pnl for the Polybot trader, for auto-study.

An (hour, coin) is 'extraordinary' when |hourly pnl| >= FLOOR ($50 default). Prints one
TAB-separated line per flag, newest last:
    <YYYY-MM-DD-HH-00>\t<coin>\t<loss|win>\t<pnl>\t<win%>\t<n>\t<fire%>
The caller spawns an analysis agent per line and names the study file
    date/<YYYY-MM-DD-HH-00>.<coin>.<loss|win>.study
(skip if that file already exists -> no duplicate studies across hourly runs).
Times CST (UTC+8); coin = session_id suffix; pnl==0 = no fire.
Usage: polybot-outliers.py [hours] [floor]      # default 6h, $50
"""
import sqlite3
import sys
import time
from collections import defaultdict

DB = "/home/palacedeforsaken/Polybot/polymarket.db"
HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
FLOOR = float(sys.argv[2]) if len(sys.argv) > 2 else 50.0
COINS = ["btc", "eth", "sol", "xrp"]

c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
c.execute("PRAGMA busy_timeout=20000")
rows = c.execute(
    "SELECT s.ts, ss.session_id, ss.pnl FROM session_strategy ss "
    "JOIN sessions s ON ss.session_id=s.session_id AND ss.round_no=s.round_no"
).fetchall()
c.close()

agg = defaultdict(lambda: [0.0, 0, 0, 0])   # (hr,coin) -> [pnl,fires,wins,opps]
for ts, sid, p in rows:
    coin = sid.rsplit("-", 1)[-1]
    if coin not in COINS:
        continue
    hr = time.strftime("%Y-%m-%d-%H-00", time.gmtime(ts + 8 * 3600))
    b = agg[(hr, coin)]
    b[3] += 1
    if p != 0:
        b[0] += p
        b[1] += 1
        if p > 0:
            b[2] += 1


def pct(a, b):
    return f"{round(100*a/b)}%" if b else "--"


for hr in sorted({h for (h, _) in agg})[-HOURS:]:
    for coin in COINS:
        if (hr, coin) not in agg:
            continue
        pnl, fires, wins, opps = agg[(hr, coin)]
        if fires > 0 and abs(pnl) >= FLOOR:
            kind = "loss" if pnl < 0 else "win"
            print(f"{hr}\t{coin}\t{kind}\t{pnl:+.0f}\t{pct(wins, fires)}\t{fires}\t{pct(fires, opps)}")
