"""For POST-restart LIVE trades: did the DECISION (INTENT.intended_price) respect the 0.76 floor but
the FILL drift below it (execution issue), or did the GATE itself admit sub-0.76 entries (logic bug)?
Join INTENT->FILL by trade_id. Also check if sub-floor fills win less (the real-PnL drag)."""
import sqlite3

c = sqlite3.connect("file:archive/ledger.db?mode=ro", uri=True)
R = 1782616840
# map trade_id -> intended_price (INTENT), side, coin
intent = {tid: (ip, side, coin) for tid, ip, side, coin in c.execute(
    "SELECT trade_id, intended_price, side, coin FROM ledger WHERE event='INTENT'")}
# map trade_id -> winner (SETTLE)
winner = {tid: w for tid, w in c.execute("SELECT trade_id, winner FROM ledger WHERE event='SETTLE'")}
rows = c.execute("SELECT trade_id, coin, side, fill_price, ts FROM ledger "
                 "WHERE event='FILL' AND mode='LIVE' AND fill_price>0 AND ts>=?", (R,)).fetchall()
c.close()

n = below = 0
intent_below = fill_drift = 0
subwins = subn = okwins = okn = 0
for tid, coin, side, fp, ts in rows:
    n += 1
    ip = intent.get(tid, (None, None, None))[0]
    w = winner.get(tid)
    won = 1 if (w is not None and side == w) else 0
    if fp < 0.76:
        below += 1
        if ip is not None and ip < 0.76:
            intent_below += 1            # the DECISION itself was sub-floor -> gate logic admitted it
        elif ip is not None and ip >= 0.76:
            fill_drift += 1              # decision ok, fill drifted below
        if w is not None:
            subn += 1; subwins += won
    else:
        if w is not None:
            okn += 1; okwins += won

print("POST-restart LIVE fills: n=%d, below 0.76 = %d (%.0f%%)" % (n, below, 100 * below / n))
print("  of those sub-0.76 fills: DECISION(intent) also <0.76 = %d (gate admitted it),  "
      "decision>=0.76 but fill drifted = %d" % (intent_below, fill_drift))
print("  intent price available for %d/%d sub-floor fills" % (intent_below + fill_drift, below))
if subn and okn:
    print("\nWin-rate: sub-0.76 fills %d/%d = %.0f%%   vs   >=0.76 fills %d/%d = %.0f%%"
          % (subwins, subn, 100 * subwins / subn, okwins, okn, 100 * okwins / okn))
    print("  -> sub-floor entries win %s than floor-respecting ones"
          % ("LESS" if subwins / subn < okwins / okn else "MORE"))
# distribution of intended_price post-restart (is the gate decision itself >=0.76?)
ipost = [intent[tid][0] for tid, *_ in rows if tid in intent and intent[tid][0] is not None]
if ipost:
    ipost.sort()
    below_dec = sum(1 for x in ipost if x < 0.76)
    print("\nDECISION price (intended_price) post-restart: n=%d, <0.76 = %d (%.0f%%), median=%.3f"
          % (len(ipost), below_dec, 100 * below_dec / len(ipost), ipost[len(ipost) // 2]))
