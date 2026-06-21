"""
IMPROVE the existing favorite edge (not a new algorithm): map the favorite-longshot edge by
ENTRY-PRICE BAND on real data, to see WHERE capital earns the most risk-adjusted edge.

For each market: at the first tick past `tp_entry`, identify the favorite (price>0.5), record
the price we'd PAY (YES->ws_ask; NO->1-ws_bid) and whether the favorite ultimately won
(final price>0.5, the validated settlement proxy). Bucket by buy-price band; per band report:

  n, mean_buy_price p, win_rate w, edge (w-p), per-trade ROI (w/p-1),
  per-trade Sharpe (w-p)/sqrt(w(1-w))   [= edge normalised by binomial risk]

Theory (favorite-longshot bias) predicts edge is positive and largest at extreme favorites.
The band with the best per-trade Sharpe is where capital should concentrate. If that disagrees
with the deployed 0.60-0.93 weighting, that's a concrete portfolio improvement.
"""
import sys
import numpy as np
from polybot import backtester as bt

DATA = sys.argv[1] if len(sys.argv) > 1 else "market_data"
TP_ENTRIES = (0.4, 0.6, 0.8)        # representative entry times (early / main / late)
BANDS = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 0.96), (0.96, 1.00)]


def favorite_entry(m, tp_entry):
    """At the first tick with time_progress>=tp_entry, return (buy_price, fav_won) or None."""
    wb, wa, rem = m["ws_bid"], m["ws_ask"], m["rem"]
    n = len(wb)
    win = float(m.get("window", np.array([300.0]))[0]) if "window" in m else 300.0
    # final settlement proxy: last valid mid > 0.5 => YES won
    last = None
    for k in range(n - 1, -1, -1):
        if wb[k] > 0 and wa[k] > 0:
            last = 0.5 * (wb[k] + wa[k]); break
    if last is None:
        return None
    yes_won = last > 0.5
    for i in range(n):
        tp = 1.0 - rem[i] / win
        if tp < tp_entry or wb[i] <= 0 or wa[i] <= 0:
            continue
        mid = 0.5 * (wb[i] + wa[i])
        if abs(mid - 0.5) < 1e-9:
            continue
        if mid > 0.5:                      # YES is favorite -> buy YES at ask
            return (wa[i], yes_won)
        else:                              # NO is favorite -> buy NO at (1 - YES_bid)
            return (1.0 - wb[i], not yes_won)
    return None


def band_of(p):
    for lo, hi in BANDS:
        if lo <= p < hi:
            return (lo, hi)
    return None


def analyze(markets, tp_entry):
    rows = {b: {"p": [], "w": []} for b in BANDS}
    for m in markets:
        r = favorite_entry(m, tp_entry)
        if r is None:
            continue
        price, won = r
        b = band_of(price)
        if b is None:
            continue
        rows[b]["p"].append(price)
        rows[b]["w"].append(1.0 if won else 0.0)
    print(f"\n  ENTRY tp>={tp_entry:.1f}")
    print(f"    {'band':>12} {'n':>5} {'price':>6} {'win':>6} {'edge':>7} {'ROI/trade':>10} {'Sharpe/trade':>12}")
    for b in BANDS:
        p = np.array(rows[b]["p"]); w = np.array(rows[b]["w"])
        if len(p) < 30:
            print(f"    {f'{b[0]:.2f}-{b[1]:.2f}':>12} {len(p):>5}   (too few)")
            continue
        mp, wr = float(p.mean()), float(w.mean())
        edge = wr - mp
        roi = wr / mp - 1.0
        binom = np.sqrt(wr * (1 - wr)) if 0 < wr < 1 else 0.0
        sharpe = edge / binom if binom > 0 else 0.0
        print(f"    {f'{b[0]:.2f}-{b[1]:.2f}':>12} {len(p):>5} {mp:>6.3f} {wr:>6.3f} "
              f"{edge:>+7.3f} {roi:>+9.1%} {sharpe:>+12.3f}")


def main():
    print(f"Loading real markets from {DATA} ...")
    markets = bt.load_markets(DATA)
    print(f"  {len(markets)} markets")
    print("=" * 70)
    print("FAVORITE EDGE BY ENTRY-PRICE BAND  (edge=win-price; >0 confirms favorite-longshot)")
    print("=" * 70)
    for tp in TP_ENTRIES:
        analyze(markets, tp)
    print("\nRead: positive 'edge' confirms favorites are underpriced. 'Sharpe/trade' = edge")
    print("normalised by binomial risk -> the band to concentrate capital in.")


if __name__ == "__main__":
    main()
