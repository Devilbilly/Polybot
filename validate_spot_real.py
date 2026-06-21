"""
Validate the BTC-spot-divergence edge on REAL Binance price paths (not GBM).
Fetches recent 1s BTCUSDT closes, slices into 5-min windows, builds efficient-but-lagging
prediction markets, and reports the spot strategy's edge vs market lag. The lag=0 row is the
fair-test control (an efficient market -> the spot strategy must make ~no money).

Run:  python validate_spot_real.py [n_seconds]
"""
import sys
from polybot import binance as B
from polybot.synth import market_from_path
from polybot import backtester as bt

ENG = {"fee": 0.001, "slippage": 0.002, "cap_fills": True}
RISK = {"kill_switch_dd": 0.25, "round_loss_limit": 0.50, "min_capital": 50.0}


def cfg(name, params):
    return {"strategies": [{"id": "s", "name": name, "weight": 1.0, "params": params}],
            "engine": ENG, "risk": RISK}


def _spot_roi(windows, lag):
    import numpy as np
    if not windows:
        return float("nan")
    v = float(np.median([B.estimate_vol(w) for w in windows]))
    mk = [market_from_path(w, vol=v, lag=lag, spread=0.01) for w in windows]
    return bt.run_parallel(mk, cfg("btc_spot_divergence",
                                   {"vol": v, "edge": 0.04, "window": 300, "time_cutoff": 0.0, "max_buy": 1})).roi_pct


def main(n_seconds=36000):
    import numpy as np
    print(f"[*] Fetching {n_seconds} real BTCUSDT 1s closes from Binance ...")
    closes = [c for _, c in B.fetch_klines("BTCUSDT", "1s", total=n_seconds)]
    windows = [w for w in B.closes_to_windows(closes, 300) if len(w) == 300]
    vols = np.array([B.estimate_vol(w) for w in windows])
    med = float(np.median(vols))
    print(f"[*] {len(windows)} real 5-min windows | median per-sec vol={med:.6f} | "
          f"range ${min(closes):,.0f}-${max(closes):,.0f}\n")

    print("=== Spot edge by market lag (lag0 = fair-test control) ===")
    print(f"{'lag(s)':>6} | {'spot ROI%':>10}")
    for lag in (0, 2, 5, 10):
        print(f"{lag:>6} | {_spot_roi(windows, lag):>+10.1f}")

    print("\n=== By VOLATILITY regime (deployment guidance) ===")
    lo = [w for w, v in zip(windows, vols) if v <= med]
    hi = [w for w, v in zip(windows, vols) if v > med]
    print(f"{'regime':16s} {'n':>4} | {'lag5 ROI%':>9} {'lag10 ROI%':>10}")
    for label, ws in [("LOW vol (calm)", lo), ("HIGH vol (choppy)", hi)]:
        print(f"{label:16s} {len(ws):>4} | {_spot_roi(ws, 5):>+8.0f} {_spot_roi(ws, 10):>+9.0f}")
    print("\n[note] spot edge is stronger in CALM regimes (less reversal -> spot lead more "
          "predictive); sample regimes are mild, true high-vol untested until live.")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 12000)
