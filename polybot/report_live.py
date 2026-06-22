"""
Replay the markets RECORDED during a live run back through the offline backtester, and compare to
the live paper P&L. This is the live-vs-backtest parity check on fresh, real data — and the basis
for re-tuning the config to the current regime.

Run on the box after the live trader has recorded some windows:
    python -m polybot.report_live [polymarket.db]
"""
from __future__ import annotations
import sys
import json
import os


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "polymarket.db"
    if not os.path.exists(db_path):
        print(f"no DB at {db_path}"); return
    from polybot import backtester as bt
    from polybot.paper import paper_trade
    from polybot.database import Database

    markets = bt.load_markets(db_path)
    print(f"recorded windows in {db_path}: {len(markets)}")
    if not markets:
        print("  (none yet — let the live trader record some windows first)"); return

    cfg_path = "polybot/portfolio.json"
    cfg = json.load(open(cfg_path))
    r = paper_trade(markets, cfg, capital=1000.0)
    print("\n=== BACKTEST REPLAY of the recorded windows (favorites config) ===")
    print(f"  ROI {r.roi_pct:+.1f}%   win {r.win_rate_pct:.0f}%   maxDD {r.max_dd_pct:.1f}%   "
          f"over {len(markets)} windows")

    # live paper P&L as the trader actually booked it (sessions table)
    db = Database(db_path)
    try:
        rows = list(db.conn.execute(
            "SELECT total_pnl, total_cash FROM sessions ORDER BY ts"))
    except Exception:
        rows = []
    db.close()
    if rows:
        net = sum(x[0] for x in rows)
        print("\n=== LIVE paper P&L as booked (sessions table) ===")
        print(f"  net ${net:+.2f} over {len(rows)} rounds; last cash ${rows[-1][1]:.2f}")
        print("\n(If the backtest-replay win-rate ~ matches the live win-rate, live==backtest on "
              "fresh data — the edge is reproducing. Big gaps => regime drift worth re-tuning.)")
    else:
        print("\n(no live rounds logged in sessions table yet)")


if __name__ == "__main__":
    main()
