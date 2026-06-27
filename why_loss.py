import sqlite3, time
from collections import defaultdict
c = sqlite3.connect("file:/home/palacedeforsaken/Polybot/polymarket.db?mode=ro", uri=True, timeout=20)
rows = c.execute("SELECT s.ts, ss.pnl FROM session_strategy ss JOIN sessions s "
                 "ON ss.session_id=s.session_id AND ss.round_no=s.round_no "
                 "WHERE ss.strategy='fav_hold' AND ss.pnl!=0").fetchall()

h = defaultdict(lambda: [0, 0, 0.0])   # CST hour-of-day -> [n, wins, pnl]
for ts, p in rows:
    hr = int(time.strftime("%H", time.gmtime(ts + 8 * 3600)))
    b = h[hr]; b[0] += 1; b[1] += 1 if p > 0 else 0; b[2] += p

print("CST hour | n  | win% |  pnl   | avg/trade")
for hr in sorted(h):
    n, w, pnl = h[hr]
    print(f"   {hr:02d}:00 | {n:3} | {100*w//max(n,1):3}% | {pnl:+7.1f} | {pnl/max(n,1):+5.2f}")


def agg(g):
    n = sum(x[0] for x in g); w = sum(x[1] for x in g); pnl = sum(x[2] for x in g)
    return f"n={n}, win={100*w//max(n,1)}%, pnl={pnl:+.1f}, avg={pnl/max(n,1):+.2f}"


day = [(n, w, pnl) for hr, (n, w, pnl) in h.items() if 9 <= hr <= 21]
night = [(n, w, pnl) for hr, (n, w, pnl) in h.items() if not (9 <= hr <= 21)]
print("\nDAY 09-21 :", agg(day))
print("NIGHT     :", agg(night))

worst = sorted(rows, key=lambda x: x[1])[:6]
print("\n6 worst single rounds:")
for t, p in worst:
    print("  ", time.strftime("%m-%d %H:%M", time.gmtime(t + 8 * 3600)), f"{p:+.1f}")
