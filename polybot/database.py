"""
SQLite store for recorded market data (replaces brittle log-parsing).
Also the single CSV loader shared by the backtester and the importer, so historical
CSVs and live-recorded data flow through identical code.
"""
from __future__ import annotations
import os
import glob
import sqlite3
from typing import Iterator, Optional, List, Dict
import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    market_id  TEXT PRIMARY KEY,
    slug       TEXT,
    token_id   TEXT,
    start_ts   INTEGER,
    end_ts     INTEGER,
    winner     TEXT,            -- 'YES' | 'NO' | NULL (unresolved)
    n_ticks    INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ticks (
    market_id TEXT NOT NULL,
    seq       INTEGER NOT NULL,
    rem       REAL,
    ws_bid    REAL, ws_ask REAL,
    bid_p1 REAL, bid_s1 REAL, bid_p2 REAL, bid_s2 REAL, bid_p3 REAL, bid_s3 REAL,
    ask_p1 REAL, ask_s1 REAL, ask_p2 REAL, ask_s2 REAL, ask_p3 REAL, ask_s3 REAL,
    PRIMARY KEY (market_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_ticks_market ON ticks(market_id);
"""

L2_COLS = ["bid_p2", "bid_s2", "ask_p2", "ask_s2", "bid_p3", "bid_s3", "ask_p3", "ask_s3"]
TICK_COLS = ["rem", "ws_bid", "ws_ask",
             "bid_p1", "bid_s1", "bid_p2", "bid_s2", "bid_p3", "bid_s3",
             "ask_p1", "ask_s1", "ask_p2", "ask_s2", "ask_p3", "ask_s3"]


def determine_winner(ws_bid: np.ndarray) -> Optional[str]:
    """Winner from the settled price. Returns None if the market is unresolved in the data."""
    if len(ws_bid) < 5:
        return None
    final = float(np.median(ws_bid[-5:]))
    if 0.15 < final < 0.85:
        return None
    return "YES" if final > 0.5 else "NO"


def csv_to_arrays(path: str) -> Optional[Dict[str, np.ndarray]]:
    """Load one market CSV into named float arrays + winner. The ONE shared loader.

    Returns dict with keys rem, ws_bid, ws_ask, bid_p1..3, bid_s1..3, ask_p1..3, ask_s1..3,
    winner ('YES'/'NO'), or None if the file is too short / unreadable / has no valid prices.
    """
    import pandas as pd
    try:
        df = pd.read_csv(path).fillna(0.0)
    except Exception:
        return None
    if len(df) < 50 or "WS_Bid" not in df.columns or "WS_Ask" not in df.columns:
        return None
    # Probability sanity: WS_Bid must look like a 0..1 prob (guards against corrupted logs).
    wb = df["WS_Bid"].values.astype(np.float64)
    if np.nanmedian(wb) > 1.5:
        return None
    out: Dict[str, np.ndarray] = {}
    out["ws_bid"] = wb
    out["ws_ask"] = df["WS_Ask"].values.astype(np.float64)
    out["rem"] = (df["Rem_Float"].values.astype(np.float64) if "Rem_Float" in df.columns
                  else np.full(len(df), -1.0))
    for lvl, src in [("bid_p1", "Bid_P1"), ("bid_s1", "Bid_S1"), ("ask_p1", "Ask_P1"), ("ask_s1", "Ask_S1"),
                     ("bid_p2", "Bid_P2"), ("bid_s2", "Bid_S2"), ("ask_p2", "Ask_P2"), ("ask_s2", "Ask_S2"),
                     ("bid_p3", "Bid_P3"), ("bid_s3", "Bid_S3"), ("ask_p3", "Ask_P3"), ("ask_s3", "Ask_S3")]:
        out[lvl] = (df[src].values.astype(np.float64) if src in df.columns
                    else np.zeros(len(df), dtype=np.float64))
    # Keep only the FIRST contiguous window: recorded `rem` is non-increasing within a market;
    # a jump UP means the file concatenates a later window (old log-reconstruction artifact).
    # Truncating here makes every market chronological -> the look-ahead time gate stays strict.
    rem = out["rem"]
    if len(rem) > 1 and rem[0] >= 0:
        jumps = np.where(np.diff(rem) > 1.0)[0]
        if len(jumps):
            cut = int(jumps[0]) + 1
            if cut < 50:
                return None
            out = {k: (v[:cut] if hasattr(v, "__len__") else v) for k, v in out.items()}
    win = determine_winner(out["ws_bid"])
    if win is None:
        return None
    out["winner"] = win  # type: ignore
    return out


class Database:
    def __init__(self, path: str = "polymarket.db"):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()

    # ---------- writes (used by recorder) ----------
    def upsert_market(self, market_id, slug=None, token_id=None, start_ts=None,
                      end_ts=None, winner=None, n_ticks=0):
        self.conn.execute(
            """INSERT INTO markets(market_id,slug,token_id,start_ts,end_ts,winner,n_ticks)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(market_id) DO UPDATE SET
                 slug=COALESCE(excluded.slug,slug), token_id=COALESCE(excluded.token_id,token_id),
                 start_ts=COALESCE(excluded.start_ts,start_ts), end_ts=COALESCE(excluded.end_ts,end_ts),
                 winner=COALESCE(excluded.winner,winner), n_ticks=excluded.n_ticks""",
            (market_id, slug, token_id, start_ts, end_ts, winner, n_ticks))
        self.conn.commit()

    def insert_tick(self, market_id, seq, values: dict):
        cols = ["market_id", "seq"] + TICK_COLS
        row = [market_id, seq] + [values.get(c, 0.0) for c in TICK_COLS]
        self.conn.execute(
            f"INSERT OR REPLACE INTO ticks({','.join(cols)}) VALUES({','.join('?'*len(cols))})", row)

    def set_winner(self, market_id, winner):
        self.conn.execute("UPDATE markets SET winner=? WHERE market_id=?", (winner, market_id))
        self.conn.commit()

    # ---------- reads (used by backtester) ----------
    def market_ids(self) -> List[str]:
        return [r[0] for r in self.conn.execute("SELECT market_id FROM markets ORDER BY market_id")]

    def load_market(self, market_id: str) -> Optional[Dict[str, np.ndarray]]:
        cur = self.conn.execute(
            f"SELECT {','.join(TICK_COLS)} FROM ticks WHERE market_id=? ORDER BY seq", (market_id,))
        rows = cur.fetchall()
        if len(rows) < 50:
            return None
        arr = np.array(rows, dtype=np.float64)
        out = {c: arr[:, i] for i, c in enumerate(TICK_COLS)}
        win = self.conn.execute("SELECT winner FROM markets WHERE market_id=?", (market_id,)).fetchone()
        out["winner"] = (win[0] if win and win[0] else determine_winner(out["ws_bid"]))  # type: ignore
        if out["winner"] is None:
            return None
        return out

    # ---------- migration ----------
    def import_csv_dir(self, data_dir: str) -> int:
        files = sorted(glob.glob(os.path.join(data_dir, "Token_*.csv")))
        imported = 0
        for f in files:
            m = csv_to_arrays(f)
            if m is None:
                continue
            mid = os.path.basename(f).split("_")[1].split(".")[0]
            n = len(m["ws_bid"])
            for seq in range(n):
                self.insert_tick(mid, seq, {c: float(m[c][seq]) for c in TICK_COLS})
            self.upsert_market(mid, token_id=mid, winner=m["winner"], n_ticks=n)  # type: ignore
            imported += 1
        self.conn.commit()
        return imported
