"""
Can GRADUAL de-risking (soft_dd) let us safely run 3% sizing — capturing its growth while
keeping the worst-case drawdown away from the 25% kill? soft_dd shrinks bet size linearly as
the MTM drawdown grows from soft_dd toward kill_switch_dd. This is equity-path-dependent, so
(unlike Monte-Carlo on bankroll-independent fractions) it MUST be tested in the sequential
PaperTrader, across SHUFFLED orderings to see the tail.

Compares, over K shuffles: median ROI and WORST-CASE (max) drawdown. A win = a config whose
median ROI beats 2.5%-no-derisk AND whose worst-case DD stays <= ~21% (the 2.5% tail).
"""
import sys, json, copy, random
import numpy as np
from polybot import backtester as bt
from polybot.paper import paper_trade

DATA = sys.argv[1] if len(sys.argv) > 1 else "market_data"
K = int(sys.argv[2]) if len(sys.argv) > 2 else 20


def cfgify(base, pct, soft_dd=None):
    c = copy.deepcopy(base)
    for s in c["strategies"]:
        s["params"]["bullet_pct"] = pct
    if soft_dd is not None:
        c.setdefault("risk", {})["soft_dd"] = soft_dd
    return c


def shuffled_stats(markets, cfg, k, label="", seed0=100):
    rois, dds, kills = [], [], 0
    idx = list(range(len(markets)))
    for s in range(k):
        rng = random.Random(seed0 + s)
        rng.shuffle(idx)
        shuffled = [markets[i] for i in idx]
        r = paper_trade(shuffled, cfg, capital=1000.0)
        rois.append(r.roi_pct); dds.append(r.max_dd_pct)
        kills += 1 if r.max_dd_pct >= 25.0 else 0
        print(f"    .. {label} shuffle {s+1}/{k}: ROI {r.roi_pct:+.0f}% DD {r.max_dd_pct:.1f}%", flush=True)
    return (float(np.median(rois)), float(np.percentile(rois, 10)),
            float(np.median(dds)), float(np.max(dds)), kills)


def main():
    markets = bt.load_markets(DATA)
    base = json.load(open("polybot/portfolio.json"))
    print(f"{len(markets)} markets, {K} shuffles each\n")
    configs = [
        ("2.5% (deployed)",      cfgify(base, 0.025)),
        ("3.0% + soft_dd=0.15",  cfgify(base, 0.030, 0.15)),
    ]
    print(f"  {'config':<22} {'ROI med':>9} {'ROI p10':>9} {'DD med':>7} {'DD worst':>9} {'>=25%':>6}")
    base_med = None
    rows = []
    for label, cfg in configs:
        med, p10, ddm, ddw, kills = shuffled_stats(markets, cfg, K, label=label)
        if base_med is None:
            base_med = med
        rows.append((label, med, ddw, kills))
        print(f"  {label:<22} {med:>+8.0f}% {p10:>+8.0f}% {ddm:>6.1f}% {ddw:>8.1f}% {kills:>5}")
    print("\n  VERDICT:")
    safe_better = [r for r in rows[1:] if r[1] > base_med and r[2] <= 21.5 and r[3] == 0]
    if safe_better:
        best = max(safe_better, key=lambda r: r[1])
        print(f"    {best[0]} beats 2.5% median ROI with worst-DD {best[2]:.1f}% (<=21.5) and 0 kills.")
        print("    => gradual de-risking lets 3% run safely. Worth adopting.")
    else:
        print("    No 3% variant safely beats 2.5% (worst-DD>21.5 or kills or lower ROI).")
        print("    => keep 2.5%; de-risking doesn't buy safe extra growth here. Honest null.")


if __name__ == "__main__":
    main()
