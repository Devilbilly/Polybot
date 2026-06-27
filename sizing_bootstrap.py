#!/usr/bin/env python3
"""Bootstrap the sizing sweep: resample whole ROUNDS (timestamp groups) with
replacement so within-round cross-coin correlation is preserved, simulate the
4 independent $1000 books + kill-switch, repeat N times -> distribution of
outcomes per bet fraction f. This removes the single-lucky-ordering artifact.

Deterministic LCG (no Math.random dependency); reports median / 5th pct / kill prob.
"""
import sqlite3
from collections import defaultdict

DB = "/home/palacedeforsaken/Polybot/polymarket.db"
START, F_LIVE, KILL_DD, MIN_CAP = 1000.0, 0.025, 0.25, 50.0
COINS = ("btc", "eth", "sol", "xrp")
N = 1500

c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
c.execute("PRAGMA busy_timeout=20000")
rows = c.execute(
    "SELECT ss.session_id, ss.round_no, ss.pnl, s.ts FROM session_strategy ss "
    "JOIN sessions s ON ss.session_id=s.session_id AND ss.round_no=s.round_no "
    "WHERE ss.strategy='fav_hold' AND ss.pnl!=0").fetchall()

by_sess = defaultdict(list)
for sid, rn, pnl, ts in rows:
    by_sess[sid].append((rn, pnl, ts))

# recover size-independent r per (ts, coin)
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

groups = [per_ts[ts] for ts in sorted(per_ts)]   # each group = one round's coin returns
G = len(groups)


def sim_sequence(seq, f):
    cash = {co: START for co in COINS}
    peak = {co: START for co in COINS}
    dead = {co: False for co in COINS}
    any_kill = False
    for grp in seq:
        for coin, r in grp:
            if dead[coin] or cash[coin] < MIN_CAP:
                continue
            cash[coin] *= (1 + f * r)
            peak[coin] = max(peak[coin], cash[coin])
            if cash[coin] < (1 - KILL_DD) * peak[coin] or cash[coin] < MIN_CAP:
                dead[coin] = True
                any_kill = True
    return sum(cash.values()), any_kill


# deterministic LCG for reproducible resampling
seed = 88172645463325252


def rnd():
    global seed
    seed ^= (seed << 13) & 0xFFFFFFFFFFFFFFFF
    seed ^= seed >> 7
    seed ^= (seed << 17) & 0xFFFFFFFFFFFFFFFF
    return (seed & 0xFFFFFFFFFFFFFFFF) / 0xFFFFFFFFFFFFFFFF


print(f"rounds(groups)={G}  bootstrap N={N}  (4 books x $1000 = $4000 start)\n")
print("   f      median%   5th%    95th%   P(kill)   median final$")
print("   " + "-" * 62)
for f in (0.025, 0.05, 0.075, 0.10, 0.125, 0.15, 0.20):
    finals = []
    kills = 0
    for _ in range(N):
        seq = [groups[int(rnd() * G)] for _ in range(G)]
        fin, k = sim_sequence(seq, f)
        finals.append(fin)
        kills += k
    finals.sort()
    med = finals[N // 2]
    p05 = finals[int(0.05 * N)]
    p95 = finals[int(0.95 * N)]
    r = lambda v: 100 * (v - 4 * START) / (4 * START)
    tag = "  <= LIVE" if abs(f - F_LIVE) < 1e-9 else ""
    print(f"   {f:5.3f}  {r(med):+7.0f}  {r(p05):+6.0f}  {r(p95):+6.0f}   {100*kills/N:5.1f}%   {med:8.0f}{tag}")
print("   " + "-" * 62)
print("   median% = typical outcome; 5th% = bad-luck downside; P(kill)=chance a book")
print("   hit the 25% kill-switch. Growth-optimal = highest median that keeps 5th% sane.")
