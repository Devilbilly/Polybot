#!/usr/bin/env python3
"""xrp PAPER eval stats (isolated DB; current 0.78+chop config). Decides whether to re-add xrp to REAL."""
import sqlite3, time
from collections import defaultdict
c = sqlite3.connect("file:/home/palacedeforsaken/Polybot/polymarket_xrp.db?mode=ro", uri=True, timeout=20)
rows = c.execute("SELECT s.ts, ss.pnl FROM session_strategy ss "
                 "JOIN sessions s ON ss.session_id=s.session_id AND ss.round_no=s.round_no").fetchall()
c.close()
n = fires = wins = 0; pnl = 0.0
hourly = defaultdict(lambda: [0.0, 0, 0])
for ts, p in rows:
    n += 1
    if p != 0:
        fires += 1; pnl += p; wins += (1 if p > 0 else 0)
        h = time.strftime("%m-%d %H", time.gmtime(ts + 8 * 3600))
        b = hourly[h]; b[0] += p; b[1] += 1; b[2] += (1 if p > 0 else 0)
print("xrp PAPER (0.78+chop, isolated, NO real money):")
print("  rounds=%d fires=%d fire=%.0f%% win=%.1f%% pnl=%+.1f $/trade=%+.3f"
      % (n, fires, 100*fires/max(n, 1), 100*wins/max(fires, 1), pnl, pnl/max(fires, 1)))
if hourly:
    print("  recent hours (CST):")
    for h in sorted(hourly)[-6:]:
        pn, f, w = hourly[h]
        print("   %s  pnl=%+6.1f  win=%3.0f%%  n=%d" % (h, pn, 100*w/max(f, 1), f))
