"""
Operational monitoring over persisted paper/live sessions (database `sessions` table).
Turns an accumulating track record into metrics (equity curve, drawdown, win rate, Sharpe,
per-strategy attribution, losing streak) and ALERTS (kill-switch breach, drawdown warning,
losing streak) — what a real deployment watches to know the edge is still working live.
"""
from __future__ import annotations
from typing import List, Optional
import numpy as np


def _rounds(db, session_id):
    return db.conn.execute(
        "SELECT round_no,total_pnl,total_cash FROM sessions WHERE session_id=? ORDER BY round_no",
        (session_id,)).fetchall()


def equity_curve(db, session_id) -> List[float]:
    rows = _rounds(db, session_id)
    if not rows:
        return []
    start = rows[0][2] - rows[0][1]               # cash after round1 minus round1 pnl = starting capital
    return [start] + [r[2] for r in rows]


def session_metrics(db, session_id) -> Optional[dict]:
    rows = _rounds(db, session_id)
    if not rows:
        return None
    curve = equity_curve(db, session_id)
    start, final = curve[0], curve[-1]
    peak, maxdd, cur_peak = curve[0], 0.0, curve[0]
    for v in curve:
        cur_peak = max(cur_peak, v)
        maxdd = max(maxdd, (cur_peak - v) / cur_peak if cur_peak > 0 else 0.0)
    current_dd = (cur_peak - final) / cur_peak if cur_peak > 0 else 0.0
    pnls = np.array([r[1] for r in rows])
    traded = pnls[np.abs(pnls) > 1e-9]
    rets = np.diff(curve) / np.array(curve[:-1])
    rets = rets[np.abs(rets) > 1e-12]
    sharpe = float(rets.mean() / rets.std() * np.sqrt(len(rets))) if len(rets) > 1 and rets.std() > 0 else 0.0
    # longest losing streak
    streak = worst = 0
    for p in pnls:
        if p < -1e-9:
            streak += 1; worst = max(worst, streak)
        elif p > 1e-9:
            streak = 0
    attr = {r[0]: r[1] for r in db.conn.execute(
        "SELECT strategy, SUM(pnl) FROM session_strategy WHERE session_id=? GROUP BY strategy",
        (session_id,)).fetchall()}
    return {
        "session_id": session_id, "rounds": len(rows),
        "start": start, "final": final, "roi_pct": (final / start - 1) * 100 if start else 0.0,
        "max_dd_pct": maxdd * 100, "current_dd_pct": current_dd * 100,
        "win_rate_pct": (np.sum(traded > 0) / len(traded) * 100) if len(traded) else 0.0,
        "sharpe": sharpe, "longest_losing_streak": worst,
        "per_strategy_pnl": attr,
    }


def session_alerts(db, session_id, kill_dd=0.25, warn_dd=0.15, streak_limit=8) -> List[str]:
    m = session_metrics(db, session_id)
    if m is None:
        return ["no such session"]
    alerts = []
    if m["max_dd_pct"] >= kill_dd * 100:
        alerts.append(f"KILL-SWITCH: max drawdown {m['max_dd_pct']:.1f}% >= {kill_dd*100:.0f}%")
    if m["current_dd_pct"] >= warn_dd * 100:
        alerts.append(f"DRAWDOWN WARNING: currently {m['current_dd_pct']:.1f}% off peak")
    if m["longest_losing_streak"] >= streak_limit:
        alerts.append(f"LOSING STREAK: {m['longest_losing_streak']} consecutive losing rounds")
    if m["roi_pct"] < 0:
        alerts.append(f"UNDERWATER: session ROI {m['roi_pct']:+.1f}%")
    return alerts
