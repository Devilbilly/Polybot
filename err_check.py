import sqlite3
from collections import Counter
c = sqlite3.connect("file:/home/palacedeforsaken/Polybot/ledger.db?mode=ro", uri=True)
print("=== distinct ERR / REJECT notes on LIVE fills (count) ===")
notes = Counter()
q = ("SELECT note FROM ledger WHERE event='FILL' AND mode='LIVE' "
     "AND (note LIKE '%ERR%' OR note LIKE '%REJECT%')")
for (note,) in c.execute(q):
    notes[(note or "")[:170]] += 1
for n, ct in notes.most_common():
    print(" %3d  %s" % (ct, n))
tot = c.execute("SELECT COUNT(*) FROM ledger WHERE event='FILL' AND mode='LIVE'").fetchone()[0]
fail = c.execute("SELECT COUNT(*) FROM ledger WHERE event='FILL' AND mode='LIVE' "
                 "AND (note LIKE '%ERR%' OR note LIKE '%REJECT%')").fetchone()[0]
print("\ntotal LIVE fills: %d  failed: %d  (%.1f%%)" % (tot, fail, 100 * fail / tot if tot else 0))
