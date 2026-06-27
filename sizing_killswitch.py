#!/usr/bin/env python3
"""Joint sweep: bet fraction f  x  kill-switch width dd.

For each (f, dd) cell, bootstrap (correlated rounds preserved) and report
  median%  = typical outcome
  p05%     = bad-luck downside (5th percentile final return)
  P(loss)  = chance you end below your starting $4000
Wider dd lifts the median at high f but worsens p05 -- that's the variance trade.
(The model-error protection of the kill-switch is OUT of this model -- see note.)
"""
import sqlite3
from collections import defaultdict

DB = "/home/palacedeforsaken/Polybot/polymarket.db"
START, F_LIVE, MIN_CAP = 1000.0, 0.025, 50.0
COINS = ("btc", "eth", "sol", "xrp")
N = 1200

c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
c.execute("PRAGMA busy_timeout=20000")
rows = c.execute(
    "SELECT ss.session_id, ss.round_no, ss.pnl, s.ts FROM session_strategy ss "
    "JOIN sessions s ON ss.session_id=s.session_id AND ss.round_no=s.round_no "
    "WHERE ss.strategy='fav_hold' AND ss.pnl!=0").fetchall()

by_sess = defaultdict(list)
for sid, rn, pnl, ts in rows:
    by_sess[sid].append((rn, pnl, ts))
per_ts = defaultdict(list)
for sid, lst in by_sess.items():
    coin = sid.rsplit("-", 1)[-1]
    if coin not in COINS:
        continue
    lst.sort(key=lambda x: x[0])
    cash = START
    for rn, pnl, ts in lst:
        r = pnl / (cash * F_LIVE)
        if r < -1.01:
            r = -1.0
        per_ts[ts].append((coin, r))
        cash += pnl
groups = [per_ts[ts] for ts in sorted(per_ts)]
G = len(groups)

seed = 88172645463325252
def rnd():
    global seed
    seed ^= (seed << 13) & 0xFFFFFFFFFFFFFFFF
    seed ^= seed >> 7
    seed ^= (seed << 17) & 0xFFFFFFFFFFFFFFFF
    return (seed & 0xFFFFFFFFFFFFFFFF) / 0xFFFFFFFFFFFFFFFF


def run(f, dd):
    finals = []
    for _ in range(N):
        cash = {co: START for co in COINS}; peak = dict(cash); dead = {co: False for co in COINS}
        for _ in range(G):
            for coin, r in groups[int(rnd() * G)]:
                if dead[coin] or cash[coin] < MIN_CAP:
                    continue
                cash[coin] *= (1 + f * r); peak[coin] = max(peak[coin], cash[coin])
                if cash[coin] < (1 - dd) * peak[coin] or cash[coin] < MIN_CAP:
                    dead[coin] = True
        finals.append(sum(cash.values()))
    finals.sort()
    pr = lambda v: 100 * (v - 4 * START) / (4 * START)
    med = pr(finals[N // 2]); p05 = pr(finals[int(0.05 * N)])
    ploss = 100 * sum(1 for v in finals if v < 4 * START) / N
    return med, p05, ploss


DDS = [0.25, 0.35, 0.50, 1.00]   # 1.00 == effectively no kill-switch
FS = [0.025, 0.05, 0.075, 0.10, 0.15, 0.20]
print(f"rounds={G}  N={N}   dd=1.00 means kill-switch OFF\n")

print("MEDIAN return %   (rows=f, cols=kill-switch dd)")
print("   f \\ dd " + "".join(f"{d:>9.2f}" for d in DDS))
grid = {}
for f in FS:
    cells = [run(f, d) for d in DDS]
    grid[f] = cells
    print(f"   {f:5.3f} " + "".join(f"{m:>+9.0f}" for m, _, _ in cells))

print("\n5th-PCT (bad-luck) return %   (downside -- this is what wider dd costs you)")
print("   f \\ dd " + "".join(f"{d:>9.2f}" for d in DDS))
for f in FS:
    print(f"   {f:5.3f} " + "".join(f"{p:>+9.0f}" for _, p, _ in grid[f]))

print("\nP(end below start) %   (chance you lose money overall)")
print("   f \\ dd " + "".join(f"{d:>9.2f}" for d in DDS))
for f in FS:
    print(f"   {f:5.3f} " + "".join(f"{pl:>9.0f}" for _, _, pl in grid[f]))

print("\nSweet spot = highest median whose 5th-pct stays >= ~0 (don't lose money on a bad run).")
best = None
for f in FS:
    for (m, p, pl), d in zip(grid[f], DDS):
        if p >= -2 and (best is None or m > best[0]):
            best = (m, f, d, p, pl)
if best:
    print(f"  -> f={best[1]}, dd={best[2]}: median {best[0]:+.0f}%, 5th-pct {best[3]:+.0f}%, P(loss) {best[4]:.0f}%")
