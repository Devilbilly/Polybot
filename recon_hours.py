"""Why do paper and real diverge in some hours? Per CST hour: paper trades (polymarket.db) vs real
FILLED (ledger) vs real FAILED (ledger). If counts differ, paper/real are different sets that hour."""
import sqlite3
import time
from collections import defaultdict

POLY = "/home/palacedeforsaken/Polybot/polymarket.db"
LED = "/home/palacedeforsaken/Polybot/ledger.db"


def hk(ts):
    return time.strftime("%m-%d %H", time.gmtime(int(ts) + 8 * 3600))


# paper from sessions
pc = sqlite3.connect("file:%s?mode=ro" % POLY, uri=True)
paper = defaultdict(lambda: [0, 0, 0.0])   # hour -> n, wins, pnl
for ts, pnl in pc.execute("SELECT ts,total_pnl FROM sessions"):
    if ts is None or pnl is None or abs(pnl) < 1e-9:
        continue
    b = paper[hk(ts)]; b[0] += 1; b[1] += 1 if pnl > 0 else 0; b[2] += pnl
pc.close()

# real from ledger
lc = sqlite3.connect("file:%s?mode=ro" % LED, uri=True)
rfill = defaultdict(lambda: [0, 0, 0, 0.0])   # hour -> filled, settled, wins, realized
rfail = defaultdict(int)
for side, fp, shv, ts, win, note in lc.execute(
        "SELECT f.side,f.fill_price,f.fill_shares,f.ts,s.winner,f.note FROM ledger f "
        "LEFT JOIN ledger s ON s.trade_id=f.trade_id AND s.event='SETTLE' "
        "WHERE f.event='FILL' AND f.mode='LIVE'"):
    if ts is None:
        continue
    h = hk(ts)
    fp = float(fp or 0); shv = float(shv or 0)
    if fp <= 0 or shv <= 0:
        rfail[h] += 1
        continue
    b = rfill[h]; b[0] += 1
    if win:
        b[1] += 1; b[2] += 1 if side == win else 0; b[3] += (shv if side == win else 0.0) - fp * shv
lc.close()

allh = sorted(set(paper) | set(rfill) | set(rfail))
print("%-9s | %18s | %22s | %5s" % ("CST hour", "PAPER (n win% $)", "REAL filled (n win% $)", "Rfail"))
for h in allh[-14:]:
    p = paper.get(h, [0, 0, 0.0])
    r = rfill.get(h, [0, 0, 0, 0.0])
    pw = "%d/%d=%d%%" % (p[1], p[0], round(100 * p[1] / p[0])) if p[0] else "-"
    rw = "%d/%d=%d%%" % (r[2], r[1], round(100 * r[2] / r[1])) if r[1] else "-"
    star = "  <<<" if h.endswith(" 00") or h.endswith(" 05") else ""
    print("%-9s | %-9s %+7.0f | %-13s %+7.2f | %4d%s" % (h, pw, p[2], rw, r[3], rfail.get(h, 0), star))
