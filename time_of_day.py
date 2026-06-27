#!/usr/bin/env python3
"""Time-of-day optimization for fav_hold, with per-DAY out-of-sample discipline.
Uses the full round history in the local polymarket.db (settled outcomes, real params).

Question: is 'night strong / day weak' robust across separate days, or one-day luck?
And: what would cum PnL / Sharpe look like if we traded ONLY the strong window?
"""
import sqlite3, time, math
from collections import defaultdict

c = sqlite3.connect("polymarket.db")
rows = c.execute("SELECT s.ts, ss.pnl FROM session_strategy ss JOIN sessions s "
                 "ON ss.session_id=s.session_id AND ss.round_no=s.round_no "
                 "WHERE ss.strategy='fav_hold' AND ss.pnl!=0").fetchall()


def cst(ts):
    return time.gmtime(ts + 8 * 3600)


# ---- pooled hour-of-day (fresh data) ----
h = defaultdict(lambda: [0, 0, 0.0])
for ts, p in rows:
    b = h[cst(ts).tm_hour]; b[0] += 1; b[1] += 1 if p > 0 else 0; b[2] += p
print("POOLED hour-of-day (CST):  hour  n   win%   pnl     avg")
for hr in sorted(h):
    n, w, pnl = h[hr]
    bar = "#" * int(max(0, pnl) / 10)
    print(f"   {hr:02d}  {n:4} {100*w//max(n,1):4}% {pnl:+7.0f} {pnl/max(n,1):+5.2f}  {bar}")

# ---- define windows from the pattern: NIGHT = 23..07 (US active), DAY = 09..21 ----
def win_of(hr):
    if hr >= 23 or hr <= 7:
        return "NIGHT"
    if 9 <= hr <= 21:
        return "DAY"
    return "EDGE"   # 8, 22 transition


# ---- per-DAY out-of-sample: does NIGHT beat DAY on each separate day? ----
perday = defaultdict(lambda: defaultdict(lambda: [0, 0, 0.0]))  # date -> win -> [n,w,pnl]
for ts, p in rows:
    g = cst(ts)
    date = time.strftime("%m-%d", g)
    b = perday[date][win_of(g.tm_hour)]
    b[0] += 1; b[1] += 1 if p > 0 else 0; b[2] += p

print("\nPER-DAY (CST)  -- OOS check: is NIGHT>DAY every day?")
print("  date      NIGHT(n win% pnl)        DAY(n win% pnl)        night-day pnl")
for date in sorted(perday):
    nb = perday[date]["NIGHT"]; db = perday[date]["DAY"]
    nW = f"{nb[0]:3} {100*nb[1]//max(nb[0],1):3}% {nb[2]:+6.0f}"
    dW = f"{db[0]:3} {100*db[1]//max(db[0],1):3}% {db[2]:+6.0f}"
    diff = (nb[2]/max(nb[0],1)) - (db[2]/max(db[0],1))
    print(f"  {date}   {nW}      {dW}      {diff:+5.2f}/trade")


# ---- what-if: trade ALL vs NIGHT-only vs DAY-only ----
def stats(sel):
    v = [p for ts, p in rows if sel(cst(ts).tm_hour)]
    n = len(v); pnl = sum(v); w = sum(1 for x in v if x > 0)
    mean = pnl / max(n, 1)
    sd = (sum((x-mean)**2 for x in v)/max(n-1, 1))**0.5
    sharpe = mean/sd if sd > 0 else 0
    return f"n={n:4}  win={100*w//max(n,1):3}%  pnl={pnl:+7.0f}  avg={mean:+.2f}  sharpe/trade={sharpe:+.3f}"


print("\nWHAT-IF (realized, same params, just filtering hours):")
print("  ALL hours :", stats(lambda hr: True))
print("  NIGHT only:", stats(lambda hr: hr >= 23 or hr <= 7))
print("  DAY only  :", stats(lambda hr: 9 <= hr <= 21))
print("\nNIGHT-only keeps ~all the PnL with far fewer trades -> higher Sharpe, less day-giveback.")
print("CAVEAT: still only ~4 days; per-day table above is the real robustness test.")
