#!/usr/bin/env python3
"""Replay the REAL fav_hold history (actual time order, not bootstrap) with the
kill-switch + soft_dd ON vs fully OFF, at a few bet fractions. Answers:
  - has the kill-switch ever actually fired at our live size (f=0.025)?
  - how would PnL grow without it?
Saves an equity-curve plot.
"""
import csv
from collections import defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

START, F_LIVE, KILL_DD, SOFT_DD, MIN_CAP = 1000.0, 0.025, 0.25, 0.15, 50.0
COINS = ("btc", "eth", "sol", "xrp")

by_sess = defaultdict(list)
with open("realdata.csv") as f:
    for sid, rn, pnl, ts in csv.reader(f):
        by_sess[sid].append((int(rn), float(pnl), int(ts)))

# recover size-independent r per (ts, coin), in real time order
events = []
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
        events.append((ts, coin, r))
        cash += pnl
events.sort(key=lambda e: e[0])


def soft_mult(cash, peak):
    if peak <= 0:
        return 1.0
    dd = (peak - cash) / peak
    if dd <= SOFT_DD:
        return 1.0
    if dd >= KILL_DD:
        return 0.0
    return 1.0 - (dd - SOFT_DD) / (KILL_DD - SOFT_DD)


def replay(f, brakes):
    cash = {c: START for c in COINS}
    peak = {c: START for c in COINS}
    dead = {c: False for c in COINS}
    fired = []
    curve = []
    last_ts = None
    for ts, coin, r in events:
        if not dead[coin] and cash[coin] >= MIN_CAP:
            mult = soft_mult(cash[coin], peak[coin]) if brakes else 1.0
            cash[coin] *= (1 + f * mult * r)
            peak[coin] = max(peak[coin], cash[coin])
            if brakes and (cash[coin] < (1 - KILL_DD) * peak[coin] or cash[coin] < MIN_CAP):
                dead[coin] = True
                fired.append((coin, ts))
        if ts != last_ts:
            curve.append(sum(cash.values()))
            last_ts = ts
    curve.append(sum(cash.values()))
    pk = curve[0]; mdd = 0.0
    for v in curve:
        pk = max(pk, v); mdd = max(mdd, (pk - v) / pk)
    return sum(cash.values()), mdd, fired, curve


print(f"real rounds: {len(events)} coin-bets, {len(set(t for t,_,_ in events))} time-rounds\n")
print("   f       brakes      final$    ret%   maxDD   kill-switch fired?")
print("   " + "-" * 64)
curves = {}
for f in (0.025, 0.05, 0.10):
    for brakes in (True, False):
        fin, mdd, fired, curve = replay(f, brakes)
        curves[(f, brakes)] = curve
        ret = 100 * (fin - 4 * START) / (4 * START)
        fb = f"{len(fired)} ({','.join(c for c,_ in fired)})" if fired else "never"
        print(f"   {f:5.3f}   {'ON ' if brakes else 'OFF':6}   {fin:8.0f}  {ret:+6.0f}  {100*mdd:5.1f}%   {fb}")
print("   " + "-" * 64)

# plot
fig, ax = plt.subplots(figsize=(11, 6))
colors = {0.025: "#1f77b4", 0.05: "#ff7f0e", 0.10: "#d62728"}
for f in (0.025, 0.05, 0.10):
    on = curves[(f, True)]; off = curves[(f, False)]
    x = range(len(on))
    ax.plot(x, on, color=colors[f], lw=2.0, label=f"f={f:.3f}  brakes ON")
    ax.plot(range(len(off)), off, color=colors[f], lw=1.3, ls="--", alpha=0.8,
            label=f"f={f:.3f}  brakes OFF")
ax.axhline(4 * START, color="#888", lw=0.8, ls=":")
ax.set_xlabel("time-round #"); ax.set_ylabel("combined equity $ (4 books, $4000 start)")
ax.set_title("fav_hold real-history replay: kill-switch+soft_dd ON (solid) vs OFF (dashed)")
ax.legend(fontsize=8, ncol=3); ax.grid(alpha=0.3)
fig.tight_layout(); fig.savefig("killswitch_replay.png", dpi=110)
print("\nplot -> killswitch_replay.png")
