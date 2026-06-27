#!/usr/bin/env python3
"""Position-sizing backtest for fav_hold.

The live bot stakes `cash * bullet_pct` each round (compounding fraction of bankroll).
So sizing is a Kelly question. We recover each round's SIZE-INDEPENDENT fractional
return r (= pnl / stake) from the recorded history, then replay the SAME real rounds
at different bet fractions f, keeping each coin's $1000 book independent (as live) and
processing rounds in time order so correlated upsets hit the combined curve together.

  per round:  new_cash = cash * (1 + f * r)        r = +(1-p)/p on a win, ~ -1 on a loss
  kill-switch: halt a book once its realized cash < 0.75 * its realized peak (dd 25%)
"""
import sqlite3
from collections import defaultdict

DB = "/home/palacedeforsaken/Polybot/polymarket.db"
START = 1000.0
F_LIVE = 0.025
KILL_DD = 0.25
MIN_CAP = 50.0
COINS = ("btc", "eth", "sol", "xrp")

c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
c.execute("PRAGMA busy_timeout=20000")
rows = c.execute(
    "SELECT ss.session_id, ss.round_no, ss.pnl, s.ts "
    "FROM session_strategy ss JOIN sessions s "
    "ON ss.session_id=s.session_id AND ss.round_no=s.round_no "
    "WHERE ss.strategy='fav_hold' AND ss.pnl!=0"
).fetchall()

# group by session_id (a continuous compounding run; resets start a new session_id)
by_sess = defaultdict(list)
for sid, rn, pnl, ts in rows:
    by_sess[sid].append((rn, pnl, ts))

# Recover r_i per round by walking each session's cash path from $1000.
# events: (ts, coin, r)
events = []
bad = 0
for sid, lst in by_sess.items():
    coin = sid.rsplit("-", 1)[-1]
    if coin not in COINS:
        continue
    lst.sort(key=lambda x: x[0])
    cash = START
    for rn, pnl, ts in lst:
        stake = cash * F_LIVE
        if stake <= 0:
            bad += 1
            continue
        r = pnl / stake
        # clamp tiny numerical overshoot past total loss
        if r < -1.01:
            r = -1.0
        events.append((ts, coin, r))
        cash += pnl
events.sort(key=lambda e: e[0])

# sanity on recovered returns
wins = [r for _, _, r in events if r > 0]
loss = [r for _, _, r in events if r < 0]
print(f"recovered {len(events)} rounds  (skipped {bad})")
print(f"  WIN  n={len(wins):4}  r in [{min(wins):.3f},{max(wins):.3f}]  mean {sum(wins)/len(wins):.3f}  "
      f"(=> implied fav price ~{1/(1+sum(wins)/len(wins)):.2f})")
print(f"  LOSE n={len(loss):4}  r in [{min(loss):.3f},{max(loss):.3f}]  mean {sum(loss)/len(loss):.3f}")
print(f"  base win rate {100*len(wins)/len(events):.1f}%")

# correlation check: how often do multiple coins lose in the SAME round timestamp?
per_ts = defaultdict(list)
for ts, coin, r in events:
    per_ts[ts].append(r)
allneg = sum(1 for ts, rs in per_ts.items() if len(rs) >= 3 and all(x < 0 for x in rs))
multi = sum(1 for ts, rs in per_ts.items() if len(rs) >= 3)
print(f"  rounds with >=3 coins live: {multi};  of those ALL lost together: {allneg} "
      f"({100*allneg/max(multi,1):.1f}%)  <- the correlated tail")


def simulate(f):
    """Replay events at bet fraction f. Each coin independent $1000 book + kill-switch."""
    cash = {co: START for co in COINS}
    peak = {co: START for co in COINS}
    dead = {co: False for co in COINS}
    trades = {co: 0 for co in COINS}
    killed = []
    comb_curve = []
    # combined equity sampled after each timestamp group, for drawdown
    last_ts = None
    for ts, coin, r in events:
        if not dead[coin] and cash[coin] >= MIN_CAP:
            cash[coin] *= (1 + f * r)
            trades[coin] += 1
            peak[coin] = max(peak[coin], cash[coin])
            if cash[coin] < (1 - KILL_DD) * peak[coin] or cash[coin] < MIN_CAP:
                dead[coin] = True
                killed.append(coin)
        if ts != last_ts:
            comb_curve.append(sum(cash.values()))
            last_ts = ts
    comb_curve.append(sum(cash.values()))
    # max drawdown of combined curve
    pk = comb_curve[0]; mdd = 0.0
    for v in comb_curve:
        pk = max(pk, v)
        mdd = max(mdd, (pk - v) / pk)
    final = sum(cash.values())
    return final, mdd, killed, cash


print("\n  f       final$   ret%     maxDD    kills            per-coin final")
print("  " + "-" * 78)
best_f, best_final = None, -1
for f in (0.01, 0.025, 0.05, 0.075, 0.10, 0.125, 0.15, 0.20, 0.25):
    final, mdd, killed, cash = simulate(f)
    ret = 100 * (final - 4 * START) / (4 * START)
    pc = " ".join(f"{co}:{cash[co]:.0f}" for co in COINS)
    tag = "  <= LIVE" if abs(f - F_LIVE) < 1e-9 else ""
    k = ",".join(killed) if killed else "-"
    print(f"  {f:5.3f}  {final:8.0f}  {ret:+6.0f}  {100*mdd:5.1f}%  {k:14}  {pc}{tag}")
    if final > best_final:
        best_final, best_f = final, f
print("  " + "-" * 78)
print(f"  growth-optimal fraction in this sweep: f={best_f}  (final ${best_final:.0f})")
print("  NOTE: terminal wealth is ONE realized path; high-f wins here are inflated by")
print("  this specific lucky ordering. Look at maxDD + kills for the risk side.")
