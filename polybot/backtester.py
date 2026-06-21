"""
Parallel backtester. Each market is independent, so per-market simulation runs across
processes; the bankroll-independent per-market RETURN FRACTIONS are then compounded
sequentially (cheap). Uses the SAME core engine + strategies as live -> no parity drift.

Provides run_parallel() and run_sequential(); they must agree (asserted in tests).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import glob, os
import numpy as np

from .core import Tick, Portfolio, ExecutionEngine, RiskGovernor
from .strategies import get_strategy
from . import database

REF_CAPITAL = 1000.0   # reference capital for per-market sims (fills/min-order behave realistically)


# ----------------------------- data plumbing -----------------------------
def ticks_from_arrays(a: Dict[str, np.ndarray]):
    """Yield Tick objects from a loaded market dict (look-ahead-free time)."""
    n = len(a["ws_bid"])
    rem = a["rem"]
    win = float(a.get("window", 300.0))      # default 300 for real data; synth carries its own
    for i in range(n):
        wb = a["ws_bid"][i]; wa = a["ws_ask"][i]
        if wb <= 0.0 or wa <= 0.0:
            continue
        r = rem[i]
        tp = (1.0 - r / win) if r >= 0.0 else (i / n)
        tp = 0.0 if tp < 0.0 else (1.0 if tp > 1.0 else tp)
        yield Tick(
            ts=str(i), time_progress=tp, ws_bid=wb, ws_ask=wa,
            bid_p=(a["bid_p1"][i], a["bid_p2"][i], a["bid_p3"][i]),
            bid_s=(a["bid_s1"][i], a["bid_s2"][i], a["bid_s3"][i]),
            ask_p=(a["ask_p1"][i], a["ask_p2"][i], a["ask_p3"][i]),
            ask_s=(a["ask_s1"][i], a["ask_s2"][i], a["ask_s3"][i]),
            spot=(a["spot"][i] if "spot" in a else 0.0),
            strike=(a["strike"][i] if "strike" in a else 0.0),
        )


def _entry_id(s: dict, i: int) -> str:
    """Stable unique id for a portfolio slot (lets two slots reuse the same strategy class)."""
    return s.get("id") or f"{s['name']}#{i}"


def _build_portfolio(cfg: dict, capital: float) -> Portfolio:
    strats = []
    for i, s in enumerate(cfg["strategies"]):
        st = get_strategy(s["name"], s.get("params", {}))
        st.name = _entry_id(s, i)
        strats.append(st)
    weights = [s["weight"] for s in cfg["strategies"]]
    engine = ExecutionEngine(**cfg.get("engine", {}))
    risk = RiskGovernor(capital, **cfg.get("risk", {}))
    return Portfolio(strats, weights, total_capital=capital, engine=engine, risk=risk)


# ----------------------------- per-market worker -----------------------------
def simulate_market(a: Dict[str, np.ndarray], cfg: dict) -> Dict[str, float]:
    """Run ONE market through the shared engine at REF_CAPITAL. Return per-strategy
    return fraction (pnl / strategy_start_cash) — bankroll-independent. Pure & picklable."""
    pf = _build_portfolio(cfg, REF_CAPITAL)
    pf.new_market()
    last = None
    for tick in ticks_from_arrays(a):
        pf.process_tick(tick)
        last = tick
    fractions = {s.name: 0.0 for s in pf.strategies}
    if last is not None:
        res = pf.settle(a["winner"] == "YES")
        # per-strategy return fraction = pnl / that strategy's starting capital (REF_CAPITAL*weight)
        for i, s in enumerate(cfg["strategies"]):
            sid = _entry_id(s, i)
            fractions[sid] = res.per_strategy[sid]["pnl"] / (REF_CAPITAL * s["weight"])
    return fractions


def _worker(args):
    a, cfg = args
    return simulate_market(a, cfg)


# ----------------------------- metrics -----------------------------
@dataclass
class BacktestResult:
    n_markets: int
    roi_pct: float
    final: float
    max_dd_pct: float
    sharpe: float
    win_rate_pct: float
    per_strategy_roi: Dict[str, float] = field(default_factory=dict)
    killed: bool = False

    def __str__(self):
        ps = "  ".join(f"{k}:{v:+.0f}%" for k, v in self.per_strategy_roi.items())
        return (f"markets={self.n_markets}  ROI={self.roi_pct:+.1f}%  final=${self.final:.2f}  "
                f"maxDD={self.max_dd_pct:.1f}%  sharpe/mkt={self.sharpe:.3f}  win={self.win_rate_pct:.1f}%\n"
                f"  per-strategy: {ps}")


def _compound(fractions_per_market: List[Dict[str, float]], cfg: dict,
              capital: float = 1000.0) -> BacktestResult:
    names = [_entry_id(s, i) for i, s in enumerate(cfg["strategies"])]
    banks = {_entry_id(s, i): capital * s["weight"] for i, s in enumerate(cfg["strategies"])}
    start_banks = dict(banks)
    kill_dd = cfg.get("risk", {}).get("kill_switch_dd", 0.25)
    peak = capital; maxdd = 0.0; killed = False
    prev = capital; rets = []; wins = 0; traded = 0
    for fr in fractions_per_market:
        if not killed:
            for nm in names:
                banks[nm] *= (1.0 + fr.get(nm, 0.0))
        tot = sum(banks.values())
        d = tot - prev
        if abs(d) > 1e-9:
            traded += 1; wins += 1 if d > 0 else 0; rets.append(d / prev)
        peak = max(peak, tot); maxdd = max(maxdd, (peak - tot) / peak if peak > 0 else 0)
        if maxdd > kill_dd:
            killed = True
        prev = tot
    final = sum(banks.values())
    # PER-MARKET Sharpe (information ratio): mean/std of per-market returns. NOT multiplied by
    # sqrt(N) -- that would be the t-statistic, which grows with dataset size and isn't a Sharpe.
    sharpe = (np.mean(rets) / np.std(rets)) if len(rets) > 1 and np.std(rets) > 0 else 0.0
    per_roi = {nm: (banks[nm] / start_banks[nm] - 1) * 100 for nm in names}
    return BacktestResult(
        n_markets=len(fractions_per_market), roi_pct=(final / capital - 1) * 100, final=final,
        max_dd_pct=maxdd * 100, sharpe=float(sharpe),
        win_rate_pct=(wins / traded * 100 if traded else 0.0), per_strategy_roi=per_roi,
        killed=killed)


# ----------------------------- public API -----------------------------
def load_markets(source: str) -> List[Dict[str, np.ndarray]]:
    """source = a directory of Token_*.csv, or a path to a .db file."""
    if source.endswith(".db"):
        db = database.Database(source)
        out = [db.load_market(mid) for mid in db.market_ids()]
        db.close()
        return [m for m in out if m is not None]
    files = sorted(glob.glob(os.path.join(source, "Token_*.csv")))
    out = [database.csv_to_arrays(f) for f in files]
    return [m for m in out if m is not None]


def compute_fractions(markets: List[Dict[str, np.ndarray]], cfg: dict,
                      n_jobs: Optional[int] = None) -> List[Dict[str, float]]:
    """Per-market return fractions (computed at REF_CAPITAL), in parallel. Order-independent —
    reused by run_parallel and the Monte-Carlo ordering test.

    APPROXIMATION: fractions are bankroll-independent ONLY while fills aren't capped — but the
    book-depth fill cap and the 1-token minimum are ABSOLUTE, not bankroll-scaled. So run_parallel
    OVERSTATES ROI at capital >> REF_CAPITAL (real bets hit thin-book caps the reference sim
    doesn't). For capital-dependent / realistic numbers use the stateful PaperTrader (real fill
    caps) and robustness.capacity_curve(); run_parallel is exact only near REF_CAPITAL."""
    import multiprocessing as mp
    n_jobs = n_jobs or max(1, (os.cpu_count() or 2) - 1)
    payload = [(m, cfg) for m in markets]
    if n_jobs == 1 or len(markets) < 8:
        return [_worker(p) for p in payload]
    with mp.Pool(n_jobs) as pool:
        return pool.map(_worker, payload)


def run_parallel(markets: List[Dict[str, np.ndarray]], cfg: dict,
                 n_jobs: Optional[int] = None, capital: float = 1000.0) -> BacktestResult:
    return _compound(compute_fractions(markets, cfg, n_jobs), cfg, capital)


def run_sequential(markets: List[Dict[str, np.ndarray]], cfg: dict,
                   capital: float = 1000.0) -> BacktestResult:
    """Exact single-process compounding (real bankroll each market). For verification."""
    fractions = [simulate_market(m, cfg) for m in markets]
    return _compound(fractions, cfg, capital)
