#!/usr/bin/env python3
"""Param optimization for fav_hold through the REAL engine (polybot.backtester),
replaying recorded ticks in market_data.db (~48h). Baseline parity check first,
then a small, disciplined sweep (price band + time_cutoff). Reports ROI/win/Sharpe.
"""
import copy
from polybot import backtester as bt

BASE = {
    "strategies": [{"id": "fav_hold", "name": "fav_convergence", "weight": 1.0,
                    "params": {"buy_p": 0.70, "sell_p": 0.93, "time_cutoff": 0.50,
                               "stop_p": 0.0, "max_buy": 1, "bullet_pct": 0.025}}],
    "engine": {"fee": 0.001, "slippage": 0.002, "cap_fills": True},
    "risk": {"kill_switch_dd": 0.25, "soft_dd": 0.15, "round_loss_limit": 0.08, "min_capital": 50.0},
}

print("loading markets from market_data.db ...")
markets = bt.load_markets("market_data.db")
print(f"  loaded {len(markets)} markets\n")

base = bt.run_parallel(markets, BASE)
print("BASELINE (current live params):")
print("  ", base, "\n")


def cfg_with(**params):
    c = copy.deepcopy(BASE)
    c["strategies"][0]["params"].update(params)
    return c


print("PRICE-BAND sweep (time_cutoff=0.5, stop_p=0):")
print(f"   {'buy_p':>6}{'sell_p':>8}{'ROI%':>8}{'win%':>7}{'sharpe':>8}{'maxDD%':>8}{'markets':>8}")
results = []
for buy_p in (0.65, 0.70, 0.75, 0.80):
    for sell_p in (0.88, 0.90, 0.93, 0.96):
        if sell_p <= buy_p:
            continue
        r = bt.run_parallel(markets, cfg_with(buy_p=buy_p, sell_p=sell_p))
        results.append((buy_p, sell_p, r))
        print(f"   {buy_p:>6.2f}{sell_p:>8.2f}{r.roi_pct:>+8.1f}{r.win_rate_pct:>7.1f}"
              f"{r.sharpe:>8.3f}{r.max_dd_pct:>8.1f}{r.n_markets:>8}")

print("\nTIME_CUTOFF sweep (buy_p=0.70, sell_p=0.93):")
print(f"   {'cutoff':>7}{'ROI%':>8}{'win%':>7}{'sharpe':>8}{'maxDD%':>8}")
for tc in (0.3, 0.4, 0.5, 0.6, 0.7, 0.85):
    r = bt.run_parallel(markets, cfg_with(time_cutoff=tc))
    print(f"   {tc:>7.2f}{r.roi_pct:>+8.1f}{r.win_rate_pct:>7.1f}{r.sharpe:>8.3f}{r.max_dd_pct:>8.1f}")

best = max(results, key=lambda x: x[2].sharpe)
print(f"\nbest price-band by Sharpe: buy_p={best[0]} sell_p={best[1]} -> {best[2].sharpe:.3f} sharpe, "
      f"ROI {best[2].roi_pct:+.1f}%")
print("CAVEAT: ~48h of ticks only; treat as 'recent regime', verify out-of-sample before deploying.")
