#!/usr/bin/env python3
"""A more reasonable sizing experiment.

Instead of compounding a lucky 167-round sample into giant totals, report the
HORIZON-INDEPENDENT per-round log-growth rate g(f) = E[log(1 + f*r)] -- the actual
Kelly objective -- and then STRESS the edge, because the real risk is not variance,
it's that the true win-rate is lower than the in-sample 83.5%.

g>0 means the bankroll grows; the f that maximises g is full Kelly; we want a
FRACTION of that which still grows even if the edge is weaker than we measured.
"""
import math
import sqlite3
from collections import defaultdict

DB = "/home/palacedeforsaken/Polybot/polymarket.db"
START, F_LIVE = 1000.0, 0.025
COINS = ("btc", "eth", "sol", "xrp")

c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
c.execute("PRAGMA busy_timeout=20000")
rows = c.execute(
    "SELECT ss.session_id, ss.round_no, ss.pnl, s.ts FROM session_strategy ss "
    "JOIN sessions s ON ss.session_id=s.session_id AND ss.round_no=s.round_no "
    "WHERE ss.strategy='fav_hold' AND ss.pnl!=0").fetchall()

by_sess = defaultdict(list)
ts_all = []
for sid, rn, pnl, ts in rows:
    by_sess[sid].append((rn, pnl, ts))
    ts_all.append(ts)

R = []                      # all recovered per-round fractional returns
rw, rl = [], []             # win / loss returns
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
        R.append(r)
        (rw if r > 0 else rl).append(r)
        cash += pnl

q_emp = len(rw) / len(R)
mw, ml = sum(rw) / len(rw), sum(rl) / len(rl)
span_days = (max(ts_all) - min(ts_all)) / 86400
rounds_per_day_per_coin = (len(R) / len(COINS)) / max(span_days, 1e-9)
print(f"in-sample: {len(R)} rounds, win-rate {100*q_emp:.1f}%, mean win {mw:+.3f}, mean loss {ml:+.3f}")
print(f"span {span_days:.1f} days -> ~{rounds_per_day_per_coin:.0f} fired rounds/day/coin\n")


def g_emp(f):
    return sum(math.log(1 + f * r) for r in R) / len(R)


def g_param(f, q):
    """parametric growth at assumed win-rate q (win=mw, loss=ml)."""
    a, b = 1 + f * mw, 1 + f * ml
    if a <= 0 or b <= 0:
        return -9.99
    return q * math.log(a) + (1 - q) * math.log(b)


FS = [0.025, 0.05, 0.075, 0.10, 0.125, 0.15, 0.20]
print("PER-ROUND growth g(f)  (x10000 = bps/round)  and the same compounded per DAY")
print("   f      g(bps/round)   ~%/day      note")
print("   " + "-" * 52)
bestf, bestg = None, -9
for f in FS:
    g = g_emp(f)
    day = (math.exp(g * rounds_per_day_per_coin) - 1) * 100
    if g > bestg:
        bestg, bestf = g, f
    print(f"   {f:5.3f}   {g*1e4:+8.1f}      {day:+6.1f}%")
print("   " + "-" * 52)
print(f"   in-sample growth-optimal (full Kelly): f={bestf}\n")

print("EDGE STRESS -- per-round g(f) if the TRUE win-rate is lower than 83.5%:")
print("   win-rate:  " + "".join(f"{q:>9.0%}" for q in (0.835, 0.81, 0.79, 0.77, 0.75)))
print("   " + "-" * 64)
for f in FS:
    cells = "".join(f"{g_param(f, q)*1e4:>+9.1f}" for q in (0.835, 0.81, 0.79, 0.77, 0.75))
    print(f"   f={f:5.3f}  {cells}")
print("   " + "-" * 64)
print("   (units bps/round; negative = bankroll SHRINKS at that f and win-rate)")

print("\nBREAKEVEN: the lowest win-rate that still grows (g=0) at each f:")
print("   f       breakeven win-rate    cushion below 83.5%")
for f in FS:
    lo, hi = 0.5, 0.95
    for _ in range(50):
        mid = (lo + hi) / 2
        if g_param(f, mid) > 0:
            hi = mid
        else:
            lo = mid
    be = (lo + hi) / 2
    print(f"   {f:5.3f}     {be:6.1%}              {100*(q_emp-be):+.1f} pts")
print("\n  Bigger f => higher breakeven win-rate => thinner safety cushion if the edge is")
print("  weaker than the lucky sample. Fractional Kelly (~1/4) keeps a fat cushion.")
