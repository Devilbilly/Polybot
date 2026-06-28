"""Why is eth's real win-rate low? Per-coin: win%, avg entry price, side split, win% by entry band."""
import sqlite3
from collections import defaultdict
c = sqlite3.connect("file:/home/palacedeforsaken/Polybot/ledger.db?mode=ro", uri=True)
rows = c.execute(
    "SELECT f.coin, f.side, f.fill_price, f.fill_shares, s.winner "
    "FROM ledger f LEFT JOIN ledger s ON s.trade_id=f.trade_id AND s.event='SETTLE' "
    "WHERE f.event='FILL' AND f.mode='LIVE'").fetchall()


def band(p):
    return "0.50-0.70" if p < 0.70 else ("0.70-0.85" if p < 0.85 else "0.85-1.0")


d = defaultdict(lambda: {"n": 0, "win": 0, "set": 0, "sumpx": 0.0, "yes": 0, "no": 0, "ywin": 0, "yset": 0,
                          "nwin": 0, "nset": 0, "bands": defaultdict(lambda: [0, 0])})
for coin, side, fp, sh, win in rows:
    fp = float(fp or 0); sh = float(sh or 0)
    if fp <= 0 or sh <= 0:
        continue
    x = d[coin]; x["n"] += 1; x["sumpx"] += fp
    x["yes" if side == "YES" else "no"] += 1
    if win:
        x["set"] += 1; w = 1 if side == win else 0; x["win"] += w
        x["bands"][band(fp)][0] += 1; x["bands"][band(fp)][1] += w
        if side == "YES":
            x["yset"] += 1; x["ywin"] += w
        else:
            x["nset"] += 1; x["nwin"] += w

for coin in ("btc", "eth", "sol", "xrp"):
    x = d.get(coin)
    if not x:
        continue
    wr = 100 * x["win"] / x["set"] if x["set"] else 0
    yw = 100 * x["ywin"] / x["yset"] if x["yset"] else 0
    nw = 100 * x["nwin"] / x["nset"] if x["nset"] else 0
    print("%s: settled=%d win%%=%.1f  avg_entry=%.3f  | YES %d (win%%%.0f)  NO %d (win%%%.0f)"
          % (coin, x["set"], wr, x["sumpx"] / x["n"], x["yset"], yw, x["nset"], nw))
    for b in ("0.50-0.70", "0.70-0.85", "0.85-1.0"):
        tot, wn = x["bands"][b]
        print("     %s: %2d trades  win%% %.0f" % (b, tot, 100 * wn / tot if tot else 0))
