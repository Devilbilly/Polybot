"""
No-future-data gating checker.

Two layers:
  1. Runtime guard (in core.Portfolio): refuses ticks whose time goes backward.
  2. assert_causal(): the gold-standard truncation test. A strategy is causal iff its
     decision at tick i depends ONLY on ticks 0..i. We verify this by running the
     strategy on the full series and on truncated prefixes: the decisions at every
     tick before the cut MUST be identical. If removing future ticks changes an earlier
     decision, the strategy peeked ahead -> LookaheadError.

check_all_strategies_causal() gates the whole registry (used in tests).
"""
from __future__ import annotations
from typing import Callable, Dict, List
import numpy as np

from .core import Strategy, Position, ExecutionEngine, LookaheadError
from .backtester import ticks_from_arrays
from .strategies import get_strategy, available


def _decision_log(make_strategy: Callable[[Dict[str, np.ndarray]], Strategy],
                  market: Dict[str, np.ndarray]) -> List[tuple]:
    """Run one strategy through the engine; return the tuple of orders emitted at each tick.
    `make_strategy(market_view)` builds a fresh strategy (real strategies ignore the view;
    a cheating strategy that reads the view's future is exactly what we want to catch)."""
    s = make_strategy(market)
    s.reset()
    eng = ExecutionEngine()
    pos = Position(cash=1000.0); pos.reset_market()
    log: List[tuple] = []
    last_tp = -1.0
    for t in ticks_from_arrays(market):
        if t.time_progress + 1e-9 < last_tp:
            raise LookaheadError("non-chronological ticks")
        last_tp = t.time_progress
        orders = s.decide(t, pos)
        log.append(tuple((o.side, o.kind, round(o.usd, 4)) for o in orders))
        for o in orders:
            eng.execute(o, t, pos)
    return log


def _truncate(market: Dict[str, np.ndarray], cut: int) -> Dict[str, np.ndarray]:
    return {k: (v[:cut] if hasattr(v, "__len__") and k != "winner" else v)
            for k, v in market.items()}


def assert_causal(make_strategy: Callable[[Dict[str, np.ndarray]], Strategy],
                  market: Dict[str, np.ndarray], cuts=(0.3, 0.5, 0.7, 0.9)) -> bool:
    """Raise LookaheadError if the strategy's earlier decisions change when future ticks
    are removed. Returns True if causal."""
    full = _decision_log(make_strategy, market)
    n = len(market["ws_bid"])
    for c in cuts:
        cut = max(2, int(n * c))
        pre = _decision_log(make_strategy, _truncate(market, cut))
        m = min(len(pre), len(full))
        for i in range(m):
            if pre[i] != full[i]:
                raise LookaheadError(
                    f"look-ahead: decision at tick {i} changed when truncating at {cut} "
                    f"(prefix={pre[i]} vs full={full[i]})")
    return True


def check_all_strategies_causal(market: Dict[str, np.ndarray], params: dict = None) -> Dict[str, bool]:
    """Gate every registered strategy. Real strategies ignore the market view -> causal."""
    params = params or {"buy_p": 0.70, "sell_p": 0.93, "time_cutoff": 0.50, "stop_p": 0.50,
                        "max_buy": 2, "add_gap": 0.04, "lookback": 20, "min_rise": 0.0}
    out = {}
    for name in available():
        out[name] = assert_causal(lambda mv, n=name: get_strategy(n, params), market)
    return out
