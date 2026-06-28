"""Is the live 0.76 floor actually enforced? Split LIVE fills at the restart (buy_p 0.70->0.76,
~03:20:40 UTC = ts 1782616840). Post-restart fills should be >=0.76 (favorite, either side)."""
import sqlite3

LED = "archive/ledger.db"
R = 1782616840
c = sqlite3.connect("file:%s?mode=ro" % LED, uri=True)
cols = [r[1] for r in c.execute("PRAGMA table_info(ledger)")]
has_side = "side" in cols
for label, lo, hi in (("PRE-restart (0.70 era)", 0, R), ("POST-restart (0.76 era)", R, 9_999_999_999)):
    print("\n--", label)
    rows = c.execute(
        "SELECT coin, COUNT(*), AVG(fill_price), MIN(fill_price), "
        "SUM(CASE WHEN fill_price < 0.76 THEN 1 ELSE 0 END) "
        "FROM ledger WHERE event='FILL' AND mode='LIVE' AND fill_price > 0 "
        "AND ts >= ? AND ts < ? GROUP BY coin ORDER BY coin", (lo, hi)).fetchall()
    if not rows:
        print("   (no fills)")
    for coin, n, avg, mn, below in rows:
        print("   %-4s n=%-4d avg=%.3f min=%.3f  below0.76=%d (%.0f%%)"
              % (coin, n, avg or 0, mn or 0, below, 100 * below / n if n else 0))
    # a few sample post-restart sub-0.76 fills to see if they're NO-side / real
    if label.startswith("POST") and has_side:
        ex = c.execute("SELECT coin, side, fill_price, ts FROM ledger WHERE event='FILL' AND mode='LIVE' "
                       "AND fill_price > 0 AND fill_price < 0.76 AND ts >= ? ORDER BY ts LIMIT 8", (R,)).fetchall()
        if ex:
            print("   sample post-restart sub-0.76 fills (coin, side, price):")
            for coin, side, fp, ts in ex:
                print("      %s %s %.3f" % (coin, side, fp))
c.close()
