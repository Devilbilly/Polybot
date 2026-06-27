import sqlite3, time, os
src = sqlite3.connect("file:/home/palacedeforsaken/Polybot/polymarket.db?mode=ro", uri=True, timeout=30)
dst = sqlite3.connect("/tmp/poly_snap.db")
with dst:
    src.backup(dst)            # consistent online snapshot
dst.close()
c = sqlite3.connect("/tmp/poly_snap.db")
n = c.execute("select count(*) from session_strategy where strategy='fav_hold' and pnl!=0").fetchone()[0]
pnl = c.execute("select coalesce(sum(pnl),0) from session_strategy where strategy='fav_hold'").fetchone()[0]
t = c.execute("select max(ts) from sessions").fetchone()[0]
sz = os.path.getsize("/tmp/poly_snap.db")
print(f"SNAP ok: {sz/1e6:.0f}MB  fav_hold rounds={n}  cum_pnl={pnl:+.1f}  "
      f"last_round_CST={time.strftime('%m-%d %H:%M', time.gmtime(t+8*3600))}")
