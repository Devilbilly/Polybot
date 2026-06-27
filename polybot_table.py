#!/usr/bin/env python3
"""Mobile-friendly, ALIGNED Polybot performance report.

Sections:
  COINS    -> current per-coin cash this session (btc/eth/sol/xrp vs $1000 start)
  SLEEVES  -> cumulative all-time per strategy (pnl / win% / trades)
  HOURLY   -> per sleeve, recent hours: pnl / win% / n  and running cum  (CST times)

Round-level pnl is RESET-INDEPENDENT (summed across all trader sessions).
Usage:  python3 polybot_table.py [hours]      # default 8
"""
import re
import sqlite3
import sys
import time
from collections import defaultdict

DB = "/home/palacedeforsaken/Polybot/polymarket.db"
LOG = "/home/palacedeforsaken/live_overnight.log"
CFG = "/home/palacedeforsaken/Polybot/polybot/portfolio.json"
HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 8


def _sleeves():
    try:
        import json
        return [(s["id"], s["id"].replace("fav_", "")) for s in json.load(open(CFG))["strategies"]]
    except Exception:
        return [("fav_hold", "hold")]


SLEEVES = _sleeves()


def latest_coins():
    pat = re.compile(r"btc=\$([\d.]+).*?eth=\$([\d.]+).*?sol=\$([\d.]+).*?xrp=\$([\d.]+)")
    last = None
    try:
        with open(LOG, errors="ignore") as f:
            for ln in f:
                m = pat.search(ln)
                if m:
                    last = [float(x) for x in m.groups()]
    except OSError:
        return None
    return last


def main():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
    c.execute("PRAGMA busy_timeout=20000")
    rows = c.execute(
        "SELECT s.ts, ss.strategy, ss.pnl FROM session_strategy ss "
        "JOIN sessions s ON ss.session_id=s.session_id AND ss.round_no=s.round_no"
    ).fetchall()

    # ---------- COINS (current session) ----------
    coins = latest_coins()
    print("COINS now (this session, $ vs 1000 start):")
    if coins:
        names = ["btc", "eth", "sol", "xrp"]
        worst = min(range(4), key=lambda i: coins[i])
        for i, nm in enumerate(names):
            d = coins[i] - 1000.0
            flag = "  <= laggard" if i == worst and d < -5 else ""
            print(f"   {nm}  {coins[i]:6.0f}   ({d:+.0f}){flag}")
    else:
        print("   (no coin line in log yet)")

    if not rows:
        print("\n(no trader rounds yet)")
        return

    # ---------- aggregate ----------
    hourly = defaultdict(lambda: defaultdict(lambda: [0.0, 0, 0]))   # hr -> sleeve -> [pnl,fired,wins]
    cumtot = defaultdict(lambda: [0.0, 0, 0])
    for ts, st, p in rows:
        if p == 0:
            continue
        cumtot[st][0] += p; cumtot[st][1] += 1; cumtot[st][2] += 1 if p > 0 else 0
        hr = time.strftime("%m-%d %H", time.gmtime(ts + 8 * 3600))
        b = hourly[hr][st]; b[0] += p; b[1] += 1; b[2] += 1 if p > 0 else 0

    def wr(f, w):
        return f"{round(100*w/f)}%" if f else "--"

    # ---------- SLEEVES cumulative ----------
    print("\nSLEEVES cumulative (all-time):")
    print(f"   {'':6}{'pnl':>6}{'win':>6}{'trades':>8}")
    gt = 0.0
    for key, short in SLEEVES:
        p, f, w = cumtot[key]; gt += p
        flag = "  <= drag" if p < -5 else ""
        print(f"   {short:6}{p:+6.0f}{wr(f, w):>6}{f:>8}{flag}")
    print(f"   {'TOTAL':6}{gt:+6.0f}")

    # ---------- HOURLY per sleeve (running cum) ----------
    all_hours = sorted(hourly)
    run = defaultdict(lambda: [0.0, 0, 0]); cum_at = {}
    for hr in all_hours:
        for key, _ in SLEEVES:
            b = hourly[hr][key]; r = run[key]
            r[0] += b[0]; r[1] += b[1]; r[2] += b[2]
        cum_at[hr] = {k: list(run[k]) for k, _ in SLEEVES}
    show = all_hours[-HOURS:]

    print(f"\nHOURLY per sleeve (last {len(show)}h, CST):")
    for key, short in SLEEVES:
        print(f"  {short.upper()}")
        print(f"   {'date  h':8}{'pnl':>6}{'win':>6}{'n':>4}{'Σcum':>7}")
        for hr in show:
            p, f, w = hourly[hr][key]
            cp = cum_at[hr][key][0]
            if f:
                print(f"   {hr:8}{p:+6.0f}{wr(f, w):>6}{f:>4}{cp:+7.0f}")
            else:
                print(f"   {hr:8}{'·':>6}{'':>6}{'':>4}{cp:+7.0f}")


if __name__ == "__main__":
    main()
