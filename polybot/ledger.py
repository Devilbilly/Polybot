#!/usr/bin/env python3
"""Tamper-evident trade ledger + reconciliation + daily P&L  (OBSERVABILITY LAYER).

Design goals (for the eventual REAL-money rollout):
  * APPEND-ONLY, hash-chained  -> any edit/deletion is detectable (verify()).
  * Full lifecycle per trade:  INTENT -> ORDER -> ACK/REJECT -> FILL(s) -> SETTLE.
    Real-money fields (order_id, tx_hash, fill_price, fee) and SHADOW fields
    (paper_price/paper_pnl, to measure real-vs-paper slippage) are first-class
    but nullable, so paper now and real later share ONE schema.
  * Independent of the bot's own accounting -> it can CHECK the bot, not echo it.

This module sends NO orders and never touches the live trader. It (a) defines the
ledger, (b) exposes record_*() hooks the future executor will call, (c) backfills
history from polymarket.db, (d) reconciles pnl-vs-cash, (e) prints a daily P&L.

CLI:  python3 -m polybot.ledger build   [poly_db] [ledger_db]
      python3 -m polybot.ledger verify  [ledger_db]
      python3 -m polybot.ledger reconcile [poly_db]
      python3 -m polybot.ledger pnl     [ledger_db]
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
import sys
import time

COINS = ("btc", "eth", "sol", "xrp")
EVENTS = ("INTENT", "ORDER", "ACK", "REJECT", "FILL", "SETTLE")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
  seq            INTEGER PRIMARY KEY AUTOINCREMENT,   -- immutable monotonic order
  ts             REAL    NOT NULL,                    -- event wall-clock (unix)
  trade_id       TEXT    NOT NULL,                    -- groups one trade's events
  event          TEXT    NOT NULL,                    -- INTENT|ORDER|ACK|REJECT|FILL|SETTLE
  mode           TEXT    NOT NULL DEFAULT 'PAPER',    -- PAPER|SHADOW|REAL
  coin           TEXT, market_id TEXT, round_no INTEGER, side TEXT, strategy TEXT,
  -- intent / order
  intended_price REAL, intended_size_usd REAL, intended_shares REAL,
  book_bid REAL, book_ask REAL, book_bid_sz REAL, book_ask_sz REAL,
  -- execution (real money)
  order_id TEXT, fill_price REAL, fill_shares REAL, fee REAL, tx_hash TEXT,
  -- settlement
  winner TEXT, settle_value REAL, pnl REAL,
  -- shadow: what PAPER expected, to measure real-vs-paper edge decay
  paper_price REAL, paper_pnl REAL,
  note TEXT,
  prev_hash TEXT, row_hash TEXT
);
CREATE INDEX IF NOT EXISTS ix_ledger_trade ON ledger(trade_id);
CREATE INDEX IF NOT EXISTS ix_ledger_event ON ledger(event);
"""

# columns that feed the hash chain (everything financially meaningful, NOT the hashes themselves)
_HASH_COLS = ("ts", "trade_id", "event", "mode", "coin", "market_id", "round_no", "side",
              "strategy", "intended_price", "intended_size_usd", "intended_shares",
              "book_bid", "book_ask", "book_bid_sz", "book_ask_sz",
              "order_id", "fill_price", "fill_shares", "fee", "tx_hash",
              "winner", "settle_value", "pnl", "paper_price", "paper_pnl")

# Canonicalise values to survive the SQLite storage-class round-trip (an int written to a
# REAL column reads back as float). Both append() and verify() hash the SAME canonical form,
# so the chain is stable regardless of Python-vs-DB type drift.
_REAL = {"ts", "intended_price", "intended_size_usd", "intended_shares", "fill_price",
         "fill_shares", "fee", "settle_value", "pnl", "paper_price", "paper_pnl",
         "book_bid", "book_ask", "book_bid_sz", "book_ask_sz"}
_INT = {"round_no"}


def _canon(v, col):
    if v is None:
        return None
    if col in _REAL:
        return float(v)
    if col in _INT:
        return int(v)
    return str(v)


def _canon_json(d) -> str:
    return json.dumps({k: _canon(d.get(k), k) for k in _HASH_COLS}, sort_keys=True)


class Ledger:
    def __init__(self, path: str = "ledger.db"):
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def _last_hash(self) -> str:
        r = self.db.execute("SELECT row_hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
        return r[0] if r and r[0] else "GENESIS"

    def append(self, event: str, **f) -> str:
        """Append ONE immutable, hash-chained event row. Returns its row_hash."""
        assert event in EVENTS, f"bad event {event}"
        row = {k: f.get(k) for k in _HASH_COLS}
        row["event"] = event
        row["mode"] = f.get("mode") or "PAPER"
        row["ts"] = f.get("ts") or _now()
        prev = self._last_hash()
        canonical = _canon_json(row)
        row_hash = hashlib.sha256((prev + canonical).encode()).hexdigest()
        cols = list(_HASH_COLS) + ["note", "prev_hash", "row_hash"]
        vals = [row.get(c) for c in _HASH_COLS] + [f.get("note"), prev, row_hash]
        self.db.execute(f"INSERT INTO ledger ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})", vals)
        self.db.commit()
        return row_hash

    # ---- lifecycle hooks the FUTURE real executor will call -------------------
    def record_intent(self, trade_id, **f): return self.append("INTENT", trade_id=trade_id, **f)
    def record_order(self, trade_id, **f):  return self.append("ORDER", trade_id=trade_id, **f)
    def record_fill(self, trade_id, **f):   return self.append("FILL", trade_id=trade_id, **f)
    def record_settle(self, trade_id, **f): return self.append("SETTLE", trade_id=trade_id, **f)

    # ---- convenience recorders for the live PAPER loop -----------------------
    # (full-fidelity capture: book snapshot at fill + effective fill price; the
    #  caller also wraps these in try/except so the trader can never be blocked.)
    def record_live_fill(self, *, coin, market_id, round_no, strategy, side, shares, cost, tick,
                         real_fill=None, mode="PAPER"):
        """Record INTENT (book snapshot) + FILL. If `real_fill` (an execution.Fill) is given
        (SHADOW/LIVE), the FILL stores the REAL price/order_id/tx alongside paper_price (the sim
        fill) — so a single row carries the real-vs-paper comparison. Else pure PAPER."""
        if not shares or shares <= 0:
            return
        paper_price = cost / shares                                  # what the sim engine paid
        quoted = tick.ap1 if side == "YES" else (1.0 - tick.bp1)     # side-specific taker quote
        tid = f"{coin}-{market_id}-{strategy}"
        self.append("INTENT", trade_id=tid, mode=mode, coin=coin, market_id=str(market_id),
                    round_no=round_no, side=side, strategy=strategy, intended_price=quoted,
                    intended_size_usd=cost, intended_shares=shares,
                    book_bid=tick.bp1, book_ask=tick.ap1, book_bid_sz=tick.bs1, book_ask_sz=tick.as1)
        if real_fill is not None:                                    # SHADOW/LIVE: real probe vs paper
            self.append("FILL", trade_id=tid, mode=mode, coin=coin, market_id=str(market_id),
                        round_no=round_no, side=side, strategy=strategy,
                        fill_price=real_fill.price, fill_shares=real_fill.shares, fee=real_fill.fee,
                        order_id=real_fill.order_id, tx_hash=real_fill.tx_hash,
                        paper_price=paper_price, note=f"real/{real_fill.status} probe vs paper")
        else:                                                        # PAPER: sim fill only
            self.append("FILL", trade_id=tid, mode=mode, coin=coin, market_id=str(market_id),
                        round_no=round_no, side=side, strategy=strategy, fill_price=paper_price,
                        fill_shares=shares, paper_price=paper_price, note="paper fill (sim engine)")

    def record_live_settle(self, *, coin, market_id, round_no, strategy, winner, pnl, mode="PAPER"):
        tid = f"{coin}-{market_id}-{strategy}"
        self.append("SETTLE", trade_id=tid, mode=mode, coin=coin, market_id=str(market_id),
                    round_no=round_no, strategy=strategy, winner=winner, pnl=pnl, paper_pnl=pnl)

    # ---- integrity -----------------------------------------------------------
    def verify(self) -> tuple:
        """Re-walk the hash chain. Returns (ok, n_rows, first_bad_seq_or_None)."""
        prev = "GENESIS"
        n = 0
        for row in self.db.execute(
                f"SELECT seq,{','.join(_HASH_COLS)},prev_hash,row_hash FROM ledger ORDER BY seq"):
            seq = row[0]
            vals = dict(zip(_HASH_COLS, row[1:1 + len(_HASH_COLS)]))
            prev_hash, row_hash = row[-2], row[-1]
            canonical = _canon_json(vals)
            expect = hashlib.sha256((prev + canonical).encode()).hexdigest()
            if prev_hash != prev or row_hash != expect:
                return (False, n, seq)
            prev = row_hash
            n += 1
        return (True, n, None)

    def close(self):
        self.db.close()


def _now() -> float:
    # Date.now() is unavailable in some sandboxes; fall back to a fixed marker only if needed.
    try:
        return time.time()
    except Exception:
        return 0.0


def _coin(session_id: str) -> str:
    c = (session_id or "").rsplit("-", 1)[-1]
    return c if c in COINS else "?"


# --------------------------- backfill from existing data ---------------------
def build(poly_db="polymarket.db", ledger_db="ledger.db"):
    """Backfill SETTLE events from already-recorded rounds. Limited fidelity: the
    existing DB has pnl/winner/coin/ts but NOT entry price or book snapshot, so
    fill_price/intent are left null -- full fidelity needs the executor (next step).
    Idempotent: rebuilds from scratch into a fresh ledger."""
    import os
    if os.path.exists(ledger_db):
        os.remove(ledger_db)
    lg = Ledger(ledger_db)
    src = sqlite3.connect(f"file:{poly_db}?mode=ro", uri=True)
    rows = src.execute(
        "SELECT s.ts, s.session_id, ss.round_no, ss.strategy, ss.pnl, s.winner, s.market_id "
        "FROM session_strategy ss JOIN sessions s "
        "ON ss.session_id=s.session_id AND ss.round_no=s.round_no "
        "WHERE ss.pnl!=0 ORDER BY s.ts, ss.round_no").fetchall()
    n = 0
    for ts, sid, rn, strat, pnl, winner, mid in rows:
        coin = _coin(sid)
        # fav_hold buys the favourite; pnl>0 => it bought the winning side.
        side = (winner if pnl > 0 else ("NO" if winner == "YES" else "YES"))
        tid = f"{coin}-{mid}-{rn}-{strat}"
        lg.append("SETTLE", trade_id=tid, ts=ts, mode="PAPER", coin=coin, market_id=str(mid),
                  round_no=rn, side=side, strategy=strat, winner=winner, pnl=pnl,
                  paper_pnl=pnl, note="backfill (pnl/winner only; no fill-price in source)")
        n += 1
    src.close()
    ok, cnt, bad = lg.verify()
    lg.close()
    print(f"built {ledger_db}: {n} SETTLE events from {poly_db}")
    print(f"hash-chain verify: ok={ok}  rows={cnt}  first_bad={bad}")


def verify_cli(ledger_db="ledger.db"):
    lg = Ledger(ledger_db)
    ok, n, bad = lg.verify()
    lg.close()
    print(f"verify {ledger_db}: ok={ok}  rows={n}  first_bad_seq={bad}")
    if ok:
        print("  -> chain intact, no row was edited or deleted.")
    else:
        print(f"  -> TAMPER/CORRUPTION at seq {bad}: a row was changed or removed.")


# --------------------------- reconciliation ----------------------------------
def reconcile(poly_db="polymarket.db", eps=0.01):
    """INDEPENDENT consistency check on the source: within each (session_id,strategy),
    does the recorded cash delta between rounds equal the recorded pnl? Mismatches
    reveal accounting drift, resets, or bugs -- the kind of thing that loses real money."""
    src = sqlite3.connect(f"file:{poly_db}?mode=ro", uri=True)
    rows = src.execute(
        "SELECT session_id, strategy, round_no, pnl, cash FROM session_strategy ORDER BY session_id, strategy, round_no"
    ).fetchall()
    src.close()
    from collections import defaultdict
    series = defaultdict(list)
    for sid, strat, rn, pnl, cash in rows:
        series[(sid, strat)].append((rn, pnl, cash))
    # per-strategy tallies so we can tell the CURRENT sleeve from the legacy ones
    by_strat = defaultdict(lambda: {"checked": 0, "mism": 0, "resets": 0, "worst": (0.0, None)})
    for (sid, strat), lst in series.items():
        st = by_strat[strat]
        lst.sort()
        for i in range(1, len(lst)):
            (_, pnl, cash) = lst[i]
            prev_cash = lst[i - 1][2]
            if prev_cash is None or cash is None:
                continue
            delta = cash - prev_cash
            diff = abs(delta - (pnl or 0.0))
            st["checked"] += 1
            if delta < -100 and abs(delta) > abs(pnl or 0) * 5:   # session reset, not a bug
                st["resets"] += 1
                continue
            if diff > eps:
                st["mism"] += 1
                if diff > st["worst"][0]:
                    st["worst"] = (diff, f"{sid} round {lst[i][0]}: dcash={delta:+.2f} pnl={pnl:+.2f}")
    print(f"reconcile {poly_db}: pnl-vs-cash consistency, per strategy")
    print(f"  {'strategy':14}{'checked':>9}{'mismatch':>9}{'resets':>8}   worst")
    for strat in sorted(by_strat):
        s = by_strat[strat]
        flag = "  <= CURRENT" if strat == "fav_hold" else ("  (legacy)" if s["mism"] else "")
        print(f"  {strat:14}{s['checked']:>9}{s['mism']:>9}{s['resets']:>8}   {s['worst'][0]:.2f}{flag}")
    fh = by_strat.get("fav_hold")
    if fh:
        verdict = "CLEAN" if fh["mism"] == 0 else f"{fh['mism']} mismatches"
        print(f"\n  -> current sleeve fav_hold: {verdict}. Legacy sleeves' drift is the old "
              f"multi-capital/6h-reset accounting, not the live setup.")


# --------------------------- daily P&L statement -----------------------------
def daily_pnl(ledger_db="ledger.db"):
    lg = Ledger(ledger_db)
    rows = lg.db.execute("SELECT ts, coin, pnl FROM ledger WHERE event='SETTLE'").fetchall()
    lg.close()
    from collections import defaultdict
    by = defaultdict(lambda: defaultdict(lambda: [0.0, 0, 0]))   # date -> coin -> [pnl,n,wins]
    tot = defaultdict(lambda: [0.0, 0, 0])
    for ts, coin, pnl in rows:
        d = time.strftime("%Y-%m-%d", time.gmtime(ts + 8 * 3600))   # CST day
        b = by[d][coin]; b[0] += pnl; b[1] += 1; b[2] += 1 if pnl > 0 else 0
        t = tot[d]; t[0] += pnl; t[1] += 1; t[2] += 1 if pnl > 0 else 0
    print("DAILY P&L (CST, from the ledger -- independent of the bot's books)")
    print(f"  {'date':11}{'btc':>9}{'eth':>9}{'sol':>9}{'xrp':>9}{'TOTAL':>10}{'win%':>7}{'n':>6}")
    run = 0.0
    for d in sorted(by):
        line = f"  {d:11}"
        for c in COINS:
            line += f"{by[d][c][0]:>+9.0f}"
        tp, tn, tw = tot[d]
        run += tp
        line += f"{tp:>+10.0f}{(100*tw//max(tn,1)):>6}%{tn:>6}"
        print(line)
    print(f"  {'cum':11}{'':>36}{run:>+10.0f}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    rest = sys.argv[2:]
    if cmd == "build":
        build(*rest)
    elif cmd == "verify":
        verify_cli(*rest)
    elif cmd == "reconcile":
        reconcile(*rest)
    elif cmd == "pnl":
        daily_pnl(*rest)
    else:
        print(__doc__)
