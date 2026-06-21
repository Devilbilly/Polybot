"""
One reproducible report that runs ALL the portfolio testimony:
  - favorite edge: out-of-sample paper trade, 5-fold CV, Monte-Carlo robustness, cost sweep
  - spot edge: synthetic lag control + edge (and real Binance paths if reachable)
  - two-edge decorrelation: correlation + combined Sharpe

Run:  python -m polybot.report [market_data_dir]
Each section returns a dict (testable); main() pretty-prints.
"""
from __future__ import annotations
from typing import Dict, List
import numpy as np

from . import backtester as bt
from .paper import paper_trade
from .robustness import (monte_carlo_ordering, cost_sensitivity, diversification_report,
                         capacity_curve)

ENG = {"fee": 0.001, "slippage": 0.002, "cap_fills": True}
RISK = {"kill_switch_dd": 0.25, "round_loss_limit": 0.08, "min_capital": 50.0}

FAV_CFG = {
    "strategies": [
        {"id": "m", "name": "fav_convergence", "weight": 0.6,
         "params": {"buy_p": 0.70, "sell_p": 0.93, "time_cutoff": 0.50, "stop_p": 0.50, "max_buy": 1}},
        {"id": "l", "name": "fav_convergence", "weight": 0.4,
         "params": {"buy_p": 0.85, "sell_p": 0.96, "time_cutoff": 0.70, "stop_p": 0.50, "max_buy": 1}},
    ], "engine": ENG, "risk": RISK,
}


def favorite_validation(markets: List[Dict[str, np.ndarray]], cfg: dict = None, folds: int = 5) -> dict:
    cfg = cfg or FAV_CFG
    test = markets[1::2]
    oos = paper_trade(test, cfg)
    cv = [bt.run_parallel(markets[k::folds], cfg).roi_pct for k in range(folds)]
    mc = monte_carlo_ordering(markets, cfg, n_runs=500)
    costs = cost_sensitivity(markets, cfg)
    cap = capacity_curve(markets, cfg, capitals=(1_000, 100_000))
    return {
        "oos_roi_pct": oos.roi_pct, "oos_dd_pct": oos.max_dd_pct, "oos_win_pct": oos.win_rate_pct,
        "cv_folds_roi": cv, "cv_all_positive": all(r > 0 for r in cv), "cv_min": min(cv),
        "mc_kill_rate": mc.kill_rate, "mc_dd_max": mc.dd_max, "mc_positive_rate": mc.positive_rate,
        "cost_roi": [(c["slippage"], c["roi_pct"]) for c in costs],
        "capacity": [(c["capital"], c["roi_pct"]) for c in cap],
    }


def spot_validation(count: int = 600, seed: int = 42) -> dict:
    from .synth import synth_dataset
    sp = {"vol": 0.0006, "edge": 0.04, "window": 300, "time_cutoff": 0.0, "max_buy": 1}
    cfg = {"strategies": [{"id": "s", "name": "btc_spot_divergence", "weight": 1.0, "params": sp}],
           "engine": ENG, "risk": {"kill_switch_dd": 0.25, "round_loss_limit": 0.5, "min_capital": 50.0}}
    out = {}
    for lag in (0, 5, 10):
        ds = synth_dataset(seed=seed, count=count, vol=0.0006, lag=lag)
        r = bt.run_parallel(ds, cfg)
        out[f"lag{lag}_roi_pct"] = r.roi_pct
        out[f"lag{lag}_win_pct"] = r.win_rate_pct
    out["control_passes"] = abs(out["lag0_roi_pct"]) < 1.0   # efficient market -> ~no edge
    out["edge_grows_with_lag"] = out["lag10_roi_pct"] > out["lag5_roi_pct"]
    return out


def decorrelation_validation(count: int = 800, seed: int = 2) -> dict:
    import random, math
    from .synth import market_from_path
    rng = random.Random(seed)
    def gbm(n=300, vol=0.0006, strike=100000.0):
        s = [strike]
        for _ in range(n - 1):
            s.append(s[-1] * math.exp(vol * rng.gauss(0, 1)))
        return s
    markets = [market_from_path(gbm(), vol=0.0006, lag=5, fav_bias=0.15) for _ in range(count)]
    fav = {"strategies": [{"id": "fav", "name": "fav_convergence", "weight": 1.0,
            "params": {"buy_p": 0.70, "sell_p": 0.93, "time_cutoff": 0.50, "stop_p": 0.50, "max_buy": 1}}],
           "engine": ENG, "risk": RISK}
    spot = {"strategies": [{"id": "spot", "name": "btc_spot_divergence", "weight": 1.0,
             "params": {"vol": 0.0006, "edge": 0.04, "window": 300, "time_cutoff": 0.0, "max_buy": 1}}],
            "engine": ENG, "risk": RISK}
    rep = diversification_report(markets, [fav, spot])
    return {"correlation": float(rep["corr"][0, 1]), "sharpe_individual": rep["sharpe"],
            "sharpe_combined": rep["combined_sharpe"],
            "diversification_gain": rep["combined_sharpe"] > max(rep["sharpe"])}


def main(data_dir: str = "market_data"):  # pragma: no cover (slow, needs data)
    print("=" * 64)
    print("POLYBOT PORTFOLIO REPORT")
    print("=" * 64)
    markets = bt.load_markets(data_dir)
    print(f"\n[1] FAVORITE EDGE  ({len(markets)} real markets)")
    f = favorite_validation(markets)
    print(f"    OOS paper trade : ROI {f['oos_roi_pct']:+.0f}%  DD {f['oos_dd_pct']:.1f}%  win {f['oos_win_pct']:.0f}%")
    print(f"    5-fold CV       : all positive={f['cv_all_positive']}  worst fold {f['cv_min']:+.0f}%")
    print(f"    Monte-Carlo     : kill-rate {f['mc_kill_rate']*100:.0f}%  worst DD {f['mc_dd_max']:.1f}%  positive {f['mc_positive_rate']*100:.0f}%")
    print(f"    Cost (slip→ROI) : " + "  ".join(f"{s:.3f}→{r:+.0f}%" for s, r in f["cost_roi"]))
    print(f"    Capacity ($→ROI): " + "  ".join(f"${c:,}→{r:+.0f}%" for c, r in f["capacity"])
          + "   (thin books -> small-capacity edge)")
    print("\n[2] SPOT EDGE  (synthetic; edge requires sufficient market lag)")
    s = spot_validation()
    print(f"    lag=0 (control) : ROI {s['lag0_roi_pct']:+.1f}%  -> control_passes={s['control_passes']}")
    print(f"    lag=5s          : ROI {s['lag5_roi_pct']:+.0f}%  win {s['lag5_win_pct']:.0f}%")
    print(f"    lag=10s         : ROI {s['lag10_roi_pct']:+.0f}%  win {s['lag10_win_pct']:.0f}%  (edge grows with lag={s['edge_grows_with_lag']})")
    print("\n[3] TWO-EDGE DECORRELATION  (synthetic, both edges)")
    d = decorrelation_validation()
    print(f"    correlation     : {d['correlation']:+.3f}")
    print(f"    Sharpe ind/comb : {[round(x,2) for x in d['sharpe_individual']]} -> {d['sharpe_combined']:.2f}  gain={d['diversification_gain']}")
    print("=" * 64)


if __name__ == "__main__":  # pragma: no cover
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "market_data")
