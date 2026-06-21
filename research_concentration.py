"""
Is the compounded return BROAD-BASED or driven by a few lucky markets? The per-market EDGE is
already shown broad (iter 56: bounded per-market values, t=3.36). But compounded ROI amplifies,
and Monte-Carlo (100% positive orderings) can't detect outlier-dominance (an ever-present big
winner stays positive under every shuffle). This is the missing test: a drop-top-K jackknife.

log(final/init) = sum_i log(1 + f_i), so each market contributes log(1+f_i) to total log-growth.
We report: the top-K share of total POSITIVE log-growth, and the recompounded ROI after removing
the top-K return markets (and, for symmetry, the bottom-K). If dropping the top 1% still leaves a
large positive ROI, the edge is broad-based and robust; if it collapses, it's fragile/outlier-led.
"""
import sys
import numpy as np
from polybot import backtester as bt

DATA = sys.argv[1] if len(sys.argv) > 1 else "market_data"
CFG_PATH = "polybot/portfolio.json"


def main():
    import json
    markets = bt.load_markets(DATA)
    cfg = json.load(open(CFG_PATH))
    fr = bt.compute_fractions(markets, cfg)               # per-market total return fraction
    f = np.array([sum(d.values()) for d in fr])
    n = len(f)
    g = np.log1p(f)                                       # per-market log-growth contribution
    total_log = float(g.sum())
    full_roi = (np.expm1(total_log)) * 100

    order = np.argsort(g)[::-1]                           # markets sorted by log-growth desc
    pos_sum = float(g[g > 0].sum())

    print(f"{n} markets;  full compounded ROI {full_roi:+.0f}%  "
          f"({(f > 0).mean()*100:.0f}% of markets profitable)\n")
    print("=" * 60)
    print("CONCENTRATION — top-K share of positive log-growth + ROI without them")
    print("=" * 60)
    print(f"  {'drop top K':>12} {'K share of +growth':>20} {'ROI w/o top-K':>15}")
    for K in (1, 5, 10, max(1, n // 100), max(1, n // 20)):   # 1, 5, 10, ~1%, ~5%
        topK = order[:K]
        share = float(g[topK][g[topK] > 0].sum()) / pos_sum * 100 if pos_sum > 0 else 0.0
        roi_wo = np.expm1(total_log - float(g[topK].sum())) * 100
        tag = f"{K} (~{K/n*100:.0f}%)"
        print(f"  {tag:>12} {share:>19.1f}% {roi_wo:>+14.0f}%")

    # downside symmetry: drop the worst K (does a few disasters dominate the risk?)
    print(f"\n  {'drop bottom K':>12} {'ROI w/o bottom-K':>20}")
    for K in (1, 5, 10, max(1, n // 100)):
        botK = order[-K:]
        roi_wo = np.expm1(total_log - float(g[botK].sum())) * 100
        print(f"  {f'{K}':>12} {roi_wo:>+19.0f}%")

    # broad-base verdict: median market still positive? mean vs median growth?
    print(f"\n  per-market growth: mean {g.mean()*100:+.3f}%  median {np.median(g)*100:+.3f}%  "
          f"(median>0 => the TYPICAL market wins, not just the tail)")
    half = n // 2
    half_roi = np.expm1(float(np.sort(g)[:half].sum())) * 100   # ROI from the WORST half only
    print(f"  even the WORST HALF of markets alone compounds to {half_roi:+.0f}% "
          f"=> {'broad-based' if half_roi > 0 else 'tail-dependent'}")


if __name__ == "__main__":
    main()
