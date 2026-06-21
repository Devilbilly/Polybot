"""
Paper trading: run the portfolio with fake money, round by round, exactly as live would —
but driven by RECORDED data (DB or CSVs) so we can 'testify' without live market access.

Unlike backtester.run_parallel (fast aggregate metrics via per-market fractions), this is a
single STATEFUL session: one Portfolio whose cash compounds and whose RiskGovernor persists
across markets, with real fill caps at the real (growing) bankroll. It is the offline twin of
live.py and the most faithful pre-live validation.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Union, Dict
import logging
import numpy as np

from .core import Portfolio, RoundResult
from .live import build_portfolio
from .backtester import ticks_from_arrays, load_markets

log = logging.getLogger("polybot.paper")


@dataclass
class PaperReport:
    rounds: int
    start: float
    final: float
    roi_pct: float
    max_dd_pct: float
    win_rate_pct: float
    killed: bool
    equity_curve: List[float] = field(default_factory=list)

    def __str__(self):
        return (f"PAPER SESSION  rounds={self.rounds}  ${self.start:.0f} -> ${self.final:.2f}  "
                f"ROI={self.roi_pct:+.1f}%  maxDD={self.max_dd_pct:.1f}%  "
                f"win={self.win_rate_pct:.1f}%  killed={self.killed}")


class PaperTrader:
    """Stateful paper-trading session over a sequence of markets."""
    def __init__(self, cfg: dict, capital: float = 1000.0):
        self.cfg = cfg
        self.capital = capital
        self.pf: Portfolio = build_portfolio(cfg, capital)
        self.log: List[RoundResult] = []

    def run_market(self, arrays: Dict[str, np.ndarray]) -> Optional[RoundResult]:
        """Feed one market's ticks, settle at the end, return the round result."""
        self.pf.new_market()
        last = None
        for tick in ticks_from_arrays(arrays):
            self.pf.process_tick(tick)
            last = tick
        if last is None:
            return None
        res = self.pf.settle(arrays["winner"] == "YES")
        self.log.append(res)
        return res

    def run(self, markets: List[Dict[str, np.ndarray]], verbose: bool = False) -> PaperReport:
        for m in markets:
            r = self.run_market(m)
            if verbose and r is not None:
                log.info("[PAPER] round %d  winner=%s  pnl=$%+.2f  cash=$%.2f",
                         r.round_no, r.winner, r.total_pnl, r.total_cash)
        return self.report()

    def report(self) -> PaperReport:
        curve = [self.capital]
        peak = self.capital; maxdd = 0.0; wins = traded = 0
        for r in self.log:
            curve.append(r.total_cash)
            if abs(r.total_pnl) > 1e-9:
                traded += 1; wins += 1 if r.total_pnl > 0 else 0
            peak = max(peak, r.total_cash)
            maxdd = max(maxdd, (peak - r.total_cash) / peak if peak > 0 else 0.0)
        final = self.pf.total_cash()
        return PaperReport(
            rounds=len(self.log), start=self.capital, final=final,
            roi_pct=(final / self.capital - 1) * 100, max_dd_pct=maxdd * 100,
            win_rate_pct=(wins / traded * 100 if traded else 0.0),
            killed=self.pf.risk.killed, equity_curve=curve)


def paper_trade(source: Union[str, list], cfg: dict, capital: float = 1000.0,
                verbose: bool = False) -> PaperReport:
    """Convenience: paper-trade a config over recorded data (CSV dir, .db path, or market list)."""
    markets = load_markets(source) if isinstance(source, str) else source
    return PaperTrader(cfg, capital).run(markets, verbose=verbose)


if __name__ == "__main__":  # pragma: no cover
    import sys, json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    src = sys.argv[1] if len(sys.argv) > 1 else "market_data"
    cfg = json.load(open(sys.argv[2] if len(sys.argv) > 2 else "polybot/portfolio.json"))
    print(paper_trade(src, cfg, verbose=False))
