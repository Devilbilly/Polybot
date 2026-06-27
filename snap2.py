import sqlite3, os, time
def snap(src_path, dst_path):
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True, timeout=60)
    dst = sqlite3.connect(dst_path)
    with dst:
        src.backup(dst)
    dst.close(); src.close()
    return os.path.getsize(dst_path)

s1 = snap("/home/palacedeforsaken/Polybot/polymarket.db", "/tmp/poly_snap.db")
s2 = snap("/home/palacedeforsaken/Polybot/market_data.db", "/tmp/md_snap.db")
c = sqlite3.connect("/tmp/poly_snap.db")
n = c.execute("select count(*) from session_strategy where strategy='fav_hold' and pnl!=0").fetchone()[0]
p = c.execute("select coalesce(sum(pnl),0) from session_strategy where strategy='fav_hold'").fetchone()[0]
t0, t1 = c.execute("select min(ts), max(ts) from sessions").fetchone()
# market_data span
m = sqlite3.connect("/tmp/md_snap.db")
try:
    mt0, mt1 = m.execute("select min(ts), max(ts) from ticks").fetchone()
    md_span = f"{(mt1-mt0)/3600:.0f}h of ticks"
except Exception as e:
    md_span = f"(ticks table? {e})"
print(f"poly_snap {s1/1e6:.0f}MB  fav_hold={n} rounds  cum={p:+.1f}  "
      f"round-span {(t1-t0)/86400:.1f}d  last_CST={time.strftime('%m-%d %H:%M', time.gmtime(t1+8*3600))}")
print(f"md_snap   {s2/1e6:.0f}MB  {md_span}")
