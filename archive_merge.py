#!/usr/bin/env python3
"""Merge many Polybot SQLite DBs into ONE permanent master archive (dedup, never prune).

The box prunes market_data.db to 48h; this accumulates every market/tick we ever capture so
backtests keep full history (incl. the L2/L3 depth the Polymarket API does NOT archive).

    python3 archive_merge.py MASTER.db SRC1.db [SRC2.db ...]

Dedup is by primary key via INSERT OR IGNORE:
  markets(market_id) · ticks(market_id,seq) · sessions(session_id,round_no) ·
  session_strategy(session_id,round_no,strategy)
So re-running is idempotent and merging overlapping snapshots is safe. Per source we
intersect its columns with the target's, so older DBs missing spot/strike still merge.
"""
import sqlite3
import sys

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    market_id TEXT PRIMARY KEY, slug TEXT, token_id TEXT,
    start_ts INTEGER, end_ts INTEGER, winner TEXT, n_ticks INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS ticks (
    market_id TEXT NOT NULL, seq INTEGER NOT NULL, rem REAL,
    ws_bid REAL, ws_ask REAL,
    bid_p1 REAL, bid_s1 REAL, bid_p2 REAL, bid_s2 REAL, bid_p3 REAL, bid_s3 REAL,
    ask_p1 REAL, ask_s1 REAL, ask_p2 REAL, ask_s2 REAL, ask_p3 REAL, ask_s3 REAL,
    spot REAL DEFAULT 0.0, strike REAL DEFAULT 0.0,
    PRIMARY KEY (market_id, seq));
CREATE INDEX IF NOT EXISTS idx_ticks_market ON ticks(market_id);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT NOT NULL, round_no INTEGER NOT NULL, market_id TEXT, winner TEXT,
    total_pnl REAL, total_cash REAL, ts INTEGER, PRIMARY KEY (session_id, round_no));
CREATE TABLE IF NOT EXISTS session_strategy (
    session_id TEXT NOT NULL, round_no INTEGER NOT NULL, strategy TEXT NOT NULL,
    pnl REAL, cash REAL, PRIMARY KEY (session_id, round_no, strategy));
"""

TARGET_COLS = {
    "markets": ["market_id", "slug", "token_id", "start_ts", "end_ts", "winner", "n_ticks"],
    "ticks": ["market_id", "seq", "rem", "ws_bid", "ws_ask",
              "bid_p1", "bid_s1", "bid_p2", "bid_s2", "bid_p3", "bid_s3",
              "ask_p1", "ask_s1", "ask_p2", "ask_s2", "ask_p3", "ask_s3", "spot", "strike"],
    "sessions": ["session_id", "round_no", "market_id", "winner", "total_pnl", "total_cash", "ts"],
    "session_strategy": ["session_id", "round_no", "strategy", "pnl", "cash"],
}


def counts(con):
    out = {}
    for t in TARGET_COLS:
        out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return out


def main():
    master_path, *sources = sys.argv[1:]
    con = sqlite3.connect(f"file:{master_path}?mode=rwc", uri=True)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    before = counts(con)

    for src in sources:
        try:
            con.execute(f"ATTACH DATABASE 'file:{src}?mode=ro' AS src")
        except Exception as e:
            print(f"  SKIP {src}: cannot attach ({e})")
            continue
        src_tables = {r[0] for r in con.execute(
            "SELECT name FROM src.sqlite_master WHERE type='table'")}
        added = {}
        for t, tcols in TARGET_COLS.items():
            if t not in src_tables:
                continue
            present = {r[1] for r in con.execute(f"PRAGMA src.table_info({t})")}
            cols = [c for c in tcols if c in present]
            if not cols:
                continue
            b = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            con.execute(f"INSERT OR IGNORE INTO {t}({','.join(cols)}) "
                        f"SELECT {','.join(cols)} FROM src.{t}")
            added[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] - b
        con.commit()
        con.execute("DETACH src")
        print(f"  + {src}: " + ", ".join(f"{t}+{n}" for t, n in added.items() if n) or
              f"  + {src}: (no new rows)")

    after = counts(con)
    # provenance: time span of ticks via markets.end_ts
    span = con.execute("SELECT MIN(end_ts), MAX(end_ts) FROM markets WHERE end_ts IS NOT NULL").fetchone()
    con.commit()
    con.close()
    print("\n=== master totals ===")
    for t in TARGET_COLS:
        print(f"  {t:18} {after[t]:>9}  (+{after[t]-before[t]} this run)")
    if span and span[0]:
        hrs = (span[1] - span[0]) / 3600.0
        print(f"  market end_ts span: {span[0]}..{span[1]}  =  {hrs:.1f} h  ({hrs/24:.1f} d)")


if __name__ == "__main__":
    main()
