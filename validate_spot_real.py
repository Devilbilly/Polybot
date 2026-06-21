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


def main(n_seconds=12000):
    print(f"[*] Fetching {n_seconds} real BTCUSDT 1s closes from Binance ...")
    closes = [c for _, c in B.fetch_klines("BTCUSDT", "1s", total=n_seconds)]
    vol = B.estimate_vol(closes)
    windows = [w for w in B.closes_to_windows(closes, 300) if len(w) == 300]
    print(f"[*] {len(windows)} real 5-min windows | per-second vol={vol:.6f} | "
          f"range ${min(closes):,.0f}-${max(closes):,.0f}\n")
    spot_p = {"vol": vol, "edge": 0.04, "window": 300, "time_cutoff": 0.0, "max_buy": 1, "bullet_pct": 0.02}
    fav_p = {"buy_p": 0.70, "sell_p": 0.93, "time_cutoff": 0.50, "stop_p": 0.50, "max_buy": 1, "bullet_pct": 0.02}
    print(f"{'lag(s)':>6} | {'spot ROI%':>10} {'win%':>6} | {'fav ROI%':>9}   (lag0 = control)")
    for lag in (0, 2, 5, 15):
        mk = [market_from_path(w, vol=vol, lag=lag, spread=0.01) for w in windows]
        rs = bt.run_parallel(mk, cfg("btc_spot_divergence", spot_p))
        rf = bt.run_parallel(mk, cfg("fav_convergence", fav_p))
        print(f"{lag:>6} | {rs.roi_pct:>+10.1f} {rs.win_rate_pct:>5.0f}% | {rf.roi_pct:>+9.1f}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 12000)
