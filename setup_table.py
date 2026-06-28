"""Compare the 4 candidate setups over the master archive (>1000 markets), split into 4 chronological
ROUNDS (quarters); compute each metric per round then AVERAGE the 4 rounds (robust + shows stability).
Setups: 0.76 trade-all (current), 0.76+flat, 0.65+flat, 0.55+flat. flat = favorite price stable in the
last 10 ticks at entry. win-edge (win% - price) is leverage-free (trust it); EV/$1 inflates at low floors."""
import sys
import numpy as np
from polybot import database

DB = sys.argv[1] if len(sys.argv) > 1 else "archive/master_polybot.db"
CEIL, TCUT = 0.95, 0.50
SETUPS = [("0.76 trade-all (current)", 0.76, False),
          ("0.76 + flat", 0.76, True),
          ("0.65 + flat", 0.65, True),
          ("0.55 + flat", 0.55, True)]
FLOORS = sorted({s[1] for s in SETUPS})


def mono(m):
    rem = m.get("rem")
    return True if rem is None or len(rem) < 2 else not bool(np.any(np.diff(rem) > 1.0))


db = database.Database(DB)
rows = db.conn.execute("SELECT market_id,end_ts FROM markets WHERE end_ts IS NOT NULL ORDER BY end_ts").fetchall()
mk = []   # (end_ts, {floor: (price, win, flat)})
for mid, e in rows:
    m = db.load_market(mid)
    if m is None or not mono(m):
        continue
    w = m.get("winner")
    if w not in ("YES", "NO"):
        continue
    rem, bp, ap = m["rem"], m["bid_p1"], m["ask_p1"]
    midp = (bp + ap) / 2.0
    ent = {}
    for F in FLOORS:
        for i in range(len(rem)):
            if rem[i] > 300 * TCUT:
                continue
            yp = midp[i]
            if not (0 < yp < 1):
                continue
            fav = "YES" if yp >= 0.5 else "NO"
            fmid = yp if fav == "YES" else 1 - yp
            if F <= fmid <= CEIL:
                fser = midp if fav == "YES" else (1 - midp)
                pay = ap[i] if fav == "YES" else (1 - bp[i])
                flat = abs(fser[i] - fser[max(0, i - 10)]) <= 0.01
                ent[F] = (float(pay), 1 if fav == w else 0, bool(flat))
                break
    mk.append((e, ent))
db.close()

mk.sort(key=lambda x: x[0])
N = len(mk)
q = [mk[r * N // 4:(r + 1) * N // 4] for r in range(4)]
print("usable markets = %d  (4 chronological rounds of ~%d each)\n" % (N, N // 4))
print("%-26s %7s %7s %7s %8s %14s %9s" % ("setup", "avg n", "fire%", "win%", "price%", "win-edge pp", "EV/$1"))
print("-" * 84)
for label, F, flatreq in SETUPS:
    per = {k: [] for k in ("n", "fire", "win", "price", "edge", "ev")}
    for rnd in q:
        sel = [ent[F] for _, ent in rnd if F in ent and (not flatreq or ent[F][2])]
        if len(sel) < 5:
            continue
        pr = np.array([x[0] for x in sel]); yw = np.array([x[1] for x in sel], float)
        per["n"].append(len(sel))
        per["fire"].append(100 * len(sel) / len(rnd))
        per["win"].append(100 * yw.mean())
        per["price"].append(100 * pr.mean())
        per["edge"].append(100 * (yw.mean() - pr.mean()))
        per["ev"].append(100 * np.where(yw > 0, (1 - pr) / pr, -1.0).mean())
    a = {k: np.mean(v) for k, v in per.items()}
    estd = np.std(per["edge"])
    print("%-26s %7.0f %6.0f%% %6.1f %7.1f %+7.1f(±%2.0f) %+8.2f%%"
          % (label, a["n"], a["fire"], a["win"], a["price"], a["edge"], estd, a["ev"]))
print("\nwin-edge = win%% - price paid (leverage-FREE; the honest edge). EV/$1 is inflated at low floors.")
print("fire%% = share of that round's markets the setup actually trades. ±std = spread across the 4 rounds.")
