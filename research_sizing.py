"""
IMPROVE the portfolio via SIZING. The deployed config bets bullet_pct=0.02 (2%/bullet) and
realises only ~4.4% drawdown against a 25% kill-switch — a lot of unused risk budget. Kelly
theory: for a +edge bet there's a geometric-growth-optimal size; below it you under-compound,
above it growth falls and ruin-risk rises. Question: is 2% below the growth-optimal point for
the REAL multi-sleeve engine (with capacity caps + risk governor), and would sizing up improve
OOS compounding while keeping DD safely under the kill-switch?

Honest design: sweep bullet_pct on the DEPLOYED config via the capacity-honest PaperTrader
(real fill caps -> thin books naturally cap the benefit of sizing up). 70/30 split: pick the
growth-optimal size on TRAIN under a DD<15% guard, then report it OOS on TEST vs the deployed
2%. Capacity + kill-switch make this nonlinear, so Sharpe is NOT size-invariant here.
"""
import sys, json, copy
import numpy as np
from polybot import backtester as bt
from polybot.paper import paper_trade

DATA = sys.argv[1] if len(sys.argv) > 1 else "market_data"
SPLIT = 0.70
SIZES = (0.02, 0.03, 0.04, 0.06, 0.08, 0.10, 0.14)
DD_GUARD = 15.0          # don't pick a size whose TRAIN drawdown exceeds this (margin vs 25% kill)


def resize(cfg, pct):
    c = copy.deepcopy(cfg)
    for s in c["strategies"]:
        s["params"]["bullet_pct"] = pct
    return c


def run(markets, cfg, cap=1000.0):
    r = paper_trade(markets, cfg, capital=cap)
    return r.roi_pct, r.max_dd_pct, r.win_rate_pct


def sweep(label, markets, base):
    print(f"\n  [{label}]  ({len(markets)} markets)")
    print(f"    {'bullet%':>8} {'ROI%':>10} {'maxDD%':>8} {'win%':>6} {'geo-growth/mkt':>15}")
    rows = []
    for pct in SIZES:
        roi, dd, win = run(markets, resize(base, pct))
        # geometric growth per market = (final/init)^(1/N) - 1, monotone proxy for compounding
        g = (1.0 + roi / 100.0) ** (1.0 / max(1, len(markets))) - 1.0
        rows.append((pct, roi, dd, win, g))
        print(f"    {pct:>8.2f} {roi:>+10.1f} {dd:>8.1f} {win:>6.0f} {g*100:>14.3f}%")
    return rows


def main():
    markets = bt.load_markets(DATA)
    n = len(markets); cut = int(n * SPLIT)
    train, test = markets[:cut], markets[cut:]
    base = json.load(open("polybot/portfolio.json"))
    print(f"{n} markets -> train {len(train)} / test {len(test)};  DD guard {DD_GUARD}%")

    tr = sweep("TRAIN (in-sample, for selection only)", train, base)
    # growth-optimal size on TRAIN subject to DD guard
    safe = [r for r in tr if r[2] <= DD_GUARD]
    pick = max(safe, key=lambda r: r[1])[0] if safe else 0.02
    print(f"\n  -> TRAIN growth-optimal size under DD<{DD_GUARD}%: bullet_pct = {pick:.2f}")

    te = sweep("TEST (out-of-sample, the verdict)", test, base)
    te_map = {r[0]: r for r in te}
    d = te_map[0.02]; p = te_map[pick]
    print("\n  VERDICT (out-of-sample TEST):")
    print(f"    deployed 2%     : ROI {d[1]:+.1f}%  DD {d[2]:.1f}%")
    print(f"    picked {pick*100:.0f}%       : ROI {p[1]:+.1f}%  DD {p[2]:.1f}%")
    if pick != 0.02 and p[1] > d[1] and p[2] <= 25.0:
        print(f"    => sizing up to {pick*100:.0f}% improves OOS compounding (+{p[1]-d[1]:.0f}pp ROI), "
              f"DD {p[2]:.1f}% still under the 25% kill. Genuine improvement — worth deploying.")
    elif pick == 0.02:
        print("    => 2% IS already the growth-optimal under the DD guard. Deployed sizing justified.")
    else:
        print("    => picked size does NOT beat 2% OOS (capacity caps / DD). Keep deployed. Honest null.")


if __name__ == "__main__":
    main()
