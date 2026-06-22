"""
DB maintenance — show stats and prune old tick data to reclaim VM disk AFTER you've pulled it.

    python -m polybot.dbtool <db>                      # stats only
    python -m polybot.dbtool <db> --prune-hours 48     # drop ticks for windows that ended >48h ago, VACUUM
    python -m polybot.dbtool <db> --prune-before 1782000000   # drop ticks for windows ending before a unix ts

Prune removes the per-tick DETAIL for old windows but KEEPS the market summary rows (winner,
n_ticks). Run it only after the data is safely downloaded — the deletion is permanent (then it
VACUUMs to actually shrink the file).
"""
import sys
import os
import sqlite3
import time


def _stats(c, db):
    nt = c.execute("SELECT count(*) FROM ticks").fetchone()[0]
    nm = c.execute("SELECT count(*) FROM markets").fetchone()[0]
    lo, hi = c.execute("SELECT min(end_ts), max(end_ts) FROM markets").fetchone()
    mb = os.path.getsize(db) / 1e6
    span = f"{lo}..{hi}" if lo else "n/a"
    print(f"  {db}: {mb:.1f} MB | {nt} ticks | {nm} markets | end_ts {span}")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); return
    db = args[0]
    if not os.path.exists(db):
        print(f"no db at {db}"); return
    c = sqlite3.connect(db)
    print("before:"); _stats(c, db)

    cutoff = None
    if "--prune-hours" in args:
        cutoff = int(time.time()) - int(float(args[args.index("--prune-hours") + 1]) * 3600)
    elif "--prune-before" in args:
        cutoff = int(args[args.index("--prune-before") + 1])

    if cutoff is not None:
        old = c.execute("SELECT count(*) FROM markets WHERE end_ts>0 AND end_ts < ?", (cutoff,)).fetchone()[0]
        print(f"pruning per-tick detail for {old} windows ending before unix {cutoff} "
              f"({time.strftime('%F %T', time.gmtime(cutoff))} UTC) …")
        c.execute("DELETE FROM ticks WHERE market_id IN "
                  "(SELECT market_id FROM markets WHERE end_ts>0 AND end_ts < ?)", (cutoff,))
        c.commit()
        c.execute("VACUUM")          # actually shrink the file on disk
        c.commit()
        print("after:"); _stats(c, db)
    c.close()


if __name__ == "__main__":
    main()
