import sqlite3
from collections import defaultdict

LED = "/home/palacedeforsaken/Polybot/ledger.db"
PM = "/home/palacedeforsaken/Polybot/polymarket.db"

pc = sqlite3.connect(f"file:{PM}?mode=ro", uri=True)
winners = {mid: w for mid, w in pc.execute(
    "select market_id,winner from sessions where winner in ('YES','NO')")}
pc.close()

lc = sqlite3.connect(f"file:{LED}?mode=ro", uri=True)
per = defaultdict(lambda: [0, 0, 0.0, 0.0])     # coin -> [n, wins, gross, fees]
tot = [0, 0, 0.0, 0.0]
unknown = 0
totfee_all = lc.execute("select coalesce(sum(fee),0) from ledger where event='FILL' and mode='LIVE'").fetchone()[0]
for coin, mid, side, fp, sh, fee in lc.execute(
        "select coin,market_id,side,fill_price,fill_shares,fee from ledger "
        "where event='FILL' and mode='LIVE' and fill_shares>0"):
    w = winners.get(mid)
    if w is None:
        unknown += 1
        continue
    fp = float(fp); sh = float(sh); fee = float(fee or 0)
    gross = sh * (1 - fp) if side == w else -sh * fp
    a = per[coin]; a[0] += 1; a[1] += (1 if side == w else 0); a[2] += gross; a[3] += fee
    for i, v in enumerate((1, (1 if side == w else 0), gross, fee)):
        tot[i] += v
lc.close()

print(f"total fee column (all LIVE fills): ${totfee_all:.2f}")
print(f"unknown-winner fills skipped: {unknown}\n")
print("coin   n    win%   gross$   fees$    NET$")
for coin in ("btc", "eth", "sol", "xrp"):
    n, wn, g, fe = per[coin]
    print(f"  {coin}  {n:4}  {100*wn/n if n else 0:4.0f}%  {g:+7.2f}  {fe:6.2f}  {g-fe:+8.2f}")
n, wn, g, fe = tot
print(f"  TOT  {n:4}  {100*wn/n if n else 0:4.0f}%  {g:+7.2f}  {fe:6.2f}  {g-fe:+8.2f}")
print(f"\naccount move = -$61.60 ; reconstructed NET = {g-fe:+.2f} ; residual = {(-61.60)-(g-fe):+.2f}")
