#!/usr/bin/env python3
"""Answer: how many rounds, and WHY does higher f lose?

Decompose the optimum by turning the two amplifiers on/off:
  A  correlated rounds + kill-switch ON   = the deployed reality
  B  correlated rounds + kill-switch OFF  = isolates the kill-switch's cost
  C  decorrelated      + kill-switch OFF  = pure single-bet Kelly (no amplifiers)
If the growth-optimal f moves UP as we remove each amplifier, that amplifier is
why high f loses in our setup.
"""
import sqlite3
from collections import defaultdict

DB = "/home/palacedeforsaken/Polybot/polymarket.db"
START, F_LIVE, KILL_DD, MIN_CAP = 1000.0, 0.025, 0.25, 50.0
COINS = ("btc", "eth", "sol", "xrp")
N = 1000

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
per_coin = defaultdict(list)
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
        per_coin[coin].append(r)
        cash += pnl

groups = [per_ts[ts] for ts in sorted(per_ts)]
G = len(groups)
total_bets = sum(len(g) for g in groups)
print(f"ROUND COUNT: {total_bets} coin-bets across {G} distinct 5-min round-windows")
print(f"  per coin: " + "  ".join(f"{co}={len(per_coin[co])}" for co in COINS))
print(f"  (~{G} independent time-rounds is the real sample size for sizing)\n")

seed = 88172645463325252
def rnd():
    global seed
    seed ^= (seed << 13) & 0xFFFFFFFFFFFFFFFF
    seed ^= seed >> 7
    seed ^= (seed << 17) & 0xFFFFFFFFFFFFFFFF
    return (seed & 0xFFFFFFFFFFFFFFFF) / 0xFFFFFFFFFFFFFFFF


def med_corr(f, kill):
    finals = []
    for _ in range(N):
        cash = {co: START for co in COINS}; peak = dict(cash); dead = {co: False for co in COINS}
        for _ in range(G):
            for coin, r in groups[int(rnd() * G)]:
                if dead[coin] or cash[coin] < MIN_CAP:
                    continue
                cash[coin] *= (1 + f * r); peak[coin] = max(peak[coin], cash[coin])
                if kill and (cash[coin] < (1 - KILL_DD) * peak[coin] or cash[coin] < MIN_CAP):
                    dead[coin] = True
        finals.append(sum(cash.values()))
    finals.sort()
    return 100 * (finals[N // 2] - 4 * START) / (4 * START)


def med_decorr(f):
    finals = []
    for _ in range(N):
        tot = 0.0
        for co in COINS:
            pool = per_coin[co]; m = len(pool); cash = START
            for _ in range(m):
                cash *= (1 + f * pool[int(rnd() * m)])
                if cash < MIN_CAP:
                    cash = 0; break
            tot += cash
        finals.append(tot)
    finals.sort()
    return 100 * (finals[N // 2] - 4 * START) / (4 * START)


print("   f      A:median%   B:median%   C:median%")
print("        (corr+kill)  (corr,nokill) (indep,nokill)")
print("   " + "-" * 50)
for f in (0.025, 0.05, 0.075, 0.10, 0.15, 0.20, 0.25, 0.30):
    a = med_corr(f, True); b = med_corr(f, False); cc = med_decorr(f)
    print(f"   {f:5.3f}   {a:+8.0f}    {b:+8.0f}    {cc:+8.0f}")
print("   " + "-" * 50)
print("   A = what we actually run.  Optimum f rises A -> B -> C as amplifiers are removed.")
