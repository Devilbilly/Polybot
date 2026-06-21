"""
Robustness testimony for a portfolio. A trading system's real risk is path-dependence:
the kill-switch and compounding depend on the ORDER markets arrive in. We compute the
order-independent per-market fractions ONCE, then re-compound thousands of random
orderings (cheap) to get the DISTRIBUTION of ROI / drawdown / kill-switch rate.
Also a cost-sensitivity sweep. This drives sizing decisions with evidence.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict
import random
import copy
import numpy as np

from .backtester import compute_fractions, _compound, run_parallel


@dataclass
class MonteCarloReport:
    runs: int
    roi_mean: float
    roi_p05: float
    roi_min: float
    dd_mean: float
    dd_p95: float
    dd_max: float
    kill_rate: float
    positive_rate: float

    def __str__(self):
        return (f"MONTE-CARLO ({self.runs} orderings)\n"
                f"  ROI  mean={self.roi_mean:+.0f}%  p05={self.roi_p05:+.0f}%  min={self.roi_min:+.0f}%  "
                f"positive={self.positive_rate*100:.0f}%\n"
                f"  DD   mean={self.dd_mean:.1f}%  p95={self.dd_p95:.1f}%  max={self.dd_max:.1f}%  "
                f"kill-switch hit={self.kill_rate*100:.0f}%")


def monte_carlo_ordering(markets: List[Dict[str, np.ndarray]], cfg: dict,
                         n_runs: int = 1000, seed: int = 0, capital: float = 1000.0) -> MonteCarloReport:
    fractions = compute_fractions(markets, cfg)
    rng = random.Random(seed)
    rois, dds, kills, pos = [], [], 0, 0
    for _ in range(n_runs):
        sh = fractions[:]
        rng.shuffle(sh)
        res = _compound(sh, cfg, capital)
        rois.append(res.roi_pct); dds.append(res.max_dd_pct)
        kills += 1 if res.killed else 0
        pos += 1 if res.roi_pct > 0 else 0
    rois_a, dds_a = np.array(rois), np.array(dds)
    return MonteCarloReport(
        runs=n_runs,
        roi_mean=float(rois_a.mean()), roi_p05=float(np.percentile(rois_a, 5)), roi_min=float(rois_a.min()),
        dd_mean=float(dds_a.mean()), dd_p95=float(np.percentile(dds_a, 95)), dd_max=float(dds_a.max()),
        kill_rate=kills / n_runs, positive_rate=pos / n_runs)


def _sharpe(x: np.ndarray) -> float:
    return float(x.mean() / x.std() * np.sqrt(len(x))) if len(x) > 1 and x.std() > 0 else 0.0


def per_strategy_fractions(markets: List[Dict[str, np.ndarray]],
                           cfgs: List[dict]) -> List[np.ndarray]:
    """For each SINGLE-strategy config, its per-market return-fraction array."""
    out = []
    for c in cfgs:
        s = c["strategies"][0]
        key = s.get("id") or f"{s['name']}#0"
        out.append(np.array([d[key] for d in compute_fractions(markets, c)]))
    return out


def diversification_report(markets: List[Dict[str, np.ndarray]], cfgs: List[dict]) -> dict:
    """Per-strategy Sharpe, the between-strategy return-correlation matrix, and the
    equal-weight combined Sharpe. combined > max(individual) => genuine decorrelation gain."""
    fr = per_strategy_fractions(markets, cfgs)
    corr = np.corrcoef(fr) if len(fr) > 1 else np.array([[1.0]])
    combined = np.mean(fr, axis=0)
    return {"sharpe": [_sharpe(x) for x in fr],
            "corr": corr,
            "combined_sharpe": _sharpe(combined)}


def cost_sensitivity(markets: List[Dict[str, np.ndarray]], cfg: dict,
                     grid=((0.001, 0.002), (0.002, 0.004), (0.002, 0.010))) -> List[dict]:
    out = []
    for fee, slip in grid:
        c = copy.deepcopy(cfg)
        c.setdefault("engine", {}).update({"fee": fee, "slippage": slip})
        r = run_parallel(markets, c)
        out.append({"fee": fee, "slippage": slip, "roi_pct": r.roi_pct,
                    "max_dd_pct": r.max_dd_pct, "killed": r.killed})
    return out
