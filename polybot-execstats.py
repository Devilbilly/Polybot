#!/usr/bin/env python3
"""執行統計:平均買點 / 最高成交價 / >0.90 成交數 / 各類 reject(從 ledger.note)。"""
import sqlite3, time, re
from collections import Counter
c = sqlite3.connect("file:/home/palacedeforsaken/Polybot/ledger.db?mode=ro", uri=True, timeout=20)
def stats(hours):
    cut = time.time() - hours*3600
    rows = c.execute("select fill_price, fill_shares, note from ledger where event='FILL' and mode='LIVE' and ts>?", (cut,)).fetchall()
    fills = [float(fp) for fp,sh,note in rows if (sh or 0)>0 and fp]
    rej = Counter()
    for fp,sh,note in rows:
        if (sh or 0)>0: continue
        note = note or ""
        m = re.search(r"REJECTED:(\w+)", note)
        if m: rej[m.group(1)] += 1
        elif "ERR" in note: rej["ERR"] += 1
        else: rej["other"] += 1
    return fills, rej
print("執行統計(平均買點 / 最高 / >0.90成交 / rejects):")
for h,lab in [(1,"1h"),(6,"6h"),(99999,"all")]:
    f,rej = stats(h)
    n=len(f); avg=sum(f)/n if n else 0; mx=max(f) if f else 0; o90=sum(1 for p in f if p>0.90)
    rj = " ".join(f"{k}:{v}" for k,v in sorted(rej.items())) or "-"
    print(f"  {lab:5}: 成交={n:4}  平均買點={avg:.3f}  最高={mx:.3f}  >0.90成交={o90}  | reject: {rj}")
c.close()
