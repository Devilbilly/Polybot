"""On the SAME trades (each real order mirrors a paper entry), compare paper P&L vs real P&L."""
import sqlite3
c = sqlite3.connect("file:/home/palacedeforsaken/Polybot/ledger.db?mode=ro", uri=True)
rows = c.execute(
    "SELECT f.side, f.fill_price, f.fill_shares, s.winner, s.pnl "
    "FROM ledger f LEFT JOIN ledger s ON s.trade_id=f.trade_id AND s.event='SETTLE' "
    "WHERE f.event='FILL' AND f.mode='LIVE'").fetchall()

n = nset = 0
real_pnl = paper_pnl = real_stake = 0.0
paper_stakes = []
for side, fp, sh, win, ppnl in rows:
    fp = float(fp or 0); sh = float(sh or 0)
    if fp <= 0 or sh <= 0:
        continue                       # phantom (failed order)
    n += 1
    real_stake += fp * sh
    if win:                            # settled
        nset += 1
        real_pnl += (sh * 1.0 if side == win else 0.0) - fp * sh
        paper_pnl += float(ppnl or 0)  # paper round P&L on the paper bullet (~$25), same trade

avg_real = real_stake / n if n else 0
print("clean real trades: %d  (settled %d)" % (n, nset))
print("REAL  realized: %+8.2f   avg stake $%.2f/trade" % (real_pnl, avg_real))
print("PAPER realized: %+8.2f   (same trades, paper ~$25 bullet, compounding)" % paper_pnl)
if real_pnl:
    print("paper / real ratio: %.1fx" % (paper_pnl / real_pnl))
print("stake ratio (paper$25 / real$%.2f): %.0fx" % (avg_real, 25.0 / avg_real if avg_real else 0))
