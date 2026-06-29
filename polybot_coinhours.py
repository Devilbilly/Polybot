#!/usr/bin/env python3
"""Per-COIN x hour breakdown of the Polybot trader: pnl / win% / n(trades) / fire% / running cum.

The coin is encoded in the session_id suffix (...-btc/-eth/-sol/-xrp). For each round of each
coin, session_strategy.pnl is the realized pnl; pnl==0 means the round did NOT fire (no trade),
pnl>0 is a win. So per (hour, coin):
    n (trades) = rounds that fired (pnl != 0)
    fire%      = fired / opportunities (all rounds that hour for that coin)
    win%       = wins / fired
    pnl        = sum of round pnl
    Σcum       = that coin's all-time running pnl up to and including that hour
Times are CST (UTC+8). Reset-independent (sums across all trader sessions).
Usage: polybot-coinhours.py [hours]      # default 6
"""
import sqlite3
import sys
import time
from collections import defaultdict

DB = "/home/palacedeforsaken/Polybot/polymarket.db"
HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
COINS = ["btc", "eth", "sol", "xrp"]

c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
c.execute("PRAGMA busy_timeout=20000")
rows = c.execute(
    "SELECT s.ts, ss.session_id, ss.pnl FROM session_strategy ss "
    "JOIN sessions s ON ss.session_id=s.session_id AND ss.round_no=s.round_no"
).fetchall()
c.close()

# (hour, coin) -> [pnl, fires, wins, opportunities]
agg = defaultdict(lambda: [0.0, 0, 0, 0])
for ts, sid, p in rows:
    coin = sid.rsplit("-", 1)[-1]
    if coin not in COINS:
        continue
    hr = time.strftime("%m-%d %H", time.gmtime(ts + 8 * 3600))
    b = agg[(hr, coin)]
    b[3] += 1                       # opportunity: a round happened for this coin this hour
    if p != 0:                      # fired (a trade was taken)
        b[0] += p
        b[1] += 1
        if p > 0:
            b[2] += 1               # win

all_hours = sorted({h for (h, _) in agg})
show = set(all_hours[-HOURS:])


def pct(a, b):
    return f"{round(100*a/b)}%" if b else "--"


print(f"PER-COIN x HOUR (last {len(all_hours[-HOURS:])}h, CST)  —  pnl / win% / n=trades / fire%")
for coin in COINS:
    if not any((h, coin) in agg for h in all_hours):
        continue
    cum = 0.0
    print(f"\n  {coin.upper()}")
    print(f"   {'date  h':8}{'pnl':>6}{'win':>6}{'n':>4}{'fire':>6}{'Σcum':>7}")
    for h in all_hours:                       # walk every hour so Σcum is the true all-time cum
        if (h, coin) not in agg:
            continue
        pnl, fires, wins, opps = agg[(h, coin)]
        cum += pnl
        if h in show:
            print(f"   {h:8}{pnl:+6.0f}{pct(wins, fires):>6}{fires:>4}{pct(fires, opps):>6}{cum:+7.0f}")
