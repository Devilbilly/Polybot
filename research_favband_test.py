"""
OOS test of ONE principled reweight suggested by the band map (research_favband):
the late STRONG-favorite band (0.90-0.96) has the best risk-adjusted edge (Sharpe ~0.33),
while fav_main's 0.70-0.80 portion is weak mid-window. Hypothesis: tilting weight from
fav_main toward the late high-favorite sleeves improves OOS risk-adjusted return.

Discipline against curve-fitting: ONE hypothesis, ONE reweight (not a sweep), TRAIN/TEST
split. Confirm the band ranking on TRAIN, then compare deployed-vs-reweight on the held-out
TEST set only. Claim improvement ONLY if it holds OOS on both ROI and per-market Sharpe.
"""
import sys, json, copy
import numpy as np
from polybot import backtester as bt
from polybot.paper import paper_trade

DATA = sys.argv[1] if len(sys.argv) > 1 else "market_data"
SPLIT = 0.70


def per_market_sharpe(markets, cfg):
    """Per-market return-fraction Sharpe (mean/std) on the combined portfolio."""
    fr = bt.compute_fractions(markets, cfg)
    key_total = lambda d: sum(d.values())
    arr = np.array([key_total(d) for d in fr])
    return float(arr.mean() / arr.std()) if len(arr) > 1 and arr.std() > 0 else 0.0


def summarize(name, markets, cfg):
    r = paper_trade(markets, cfg, capital=1000.0)
    sh = per_market_sharpe(markets, cfg)
    print(f"    {name:<22} ROI {r.roi_pct:>+8.1f}%   DD {r.max_dd_pct:>5.1f}%   "
          f"win {r.win_rate_pct:>4.0f}%   Sharpe/mkt {sh:+.3f}")
    return r.roi_pct, r.max_dd_pct, sh


def main():
    markets = bt.load_markets(DATA)
    n = len(markets)
    cut = int(n * SPLIT)
    train, test = markets[:cut], markets[cut:]
    print(f"{n} markets -> train {len(train)} / test {len(test)}\n")

    deployed = json.load(open("polybot/portfolio.json"))

    # ONE principled reweight: move 0.15 from fav_main (weak 0.70-0.80 mid band) to the
    # late high-favorite sleeves (best Sharpe band). Weights stay normalised to 1.0.
    reweight = copy.deepcopy(deployed)
    new_w = {"fav_early": 0.40, "fav_main": 0.25, "fav_late": 0.20, "fav_final": 0.15}
    for s in reweight["strategies"]:
        s["weight"] = new_w[s["id"]]

    print("DEPLOYED weights :", {s["id"]: s["weight"] for s in deployed["strategies"]})
    print("REWEIGHT weights :", {s["id"]: s["weight"] for s in reweight["strategies"]})

    print("\n[TRAIN] (in-sample — for reference only)")
    summarize("deployed", train, deployed)
    summarize("reweight(late-tilt)", train, reweight)

    print("\n[TEST] (out-of-sample — the verdict)")
    d_roi, d_dd, d_sh = summarize("deployed", test, deployed)
    r_roi, r_dd, r_sh = summarize("reweight(late-tilt)", test, reweight)

    print("\n  VERDICT (out-of-sample):")
    better_roi = r_roi > d_roi
    better_sh = r_sh > d_sh
    print(f"    ROI:        reweight {'>' if better_roi else '<='} deployed  "
          f"({r_roi:+.1f}% vs {d_roi:+.1f}%)")
    print(f"    Sharpe/mkt: reweight {'>' if better_sh else '<='} deployed  "
          f"({r_sh:+.3f} vs {d_sh:+.3f})")
    if better_roi and better_sh:
        print("    => reweight improves BOTH OOS. Genuine portfolio improvement.")
    elif better_sh:
        print("    => reweight improves OOS Sharpe (risk-adjusted) but not raw ROI. Marginal.")
    else:
        print("    => reweight does NOT beat deployed OOS. Keep deployed; band edge already captured")
        print("       (momentum filter + existing weights). Honest null — no change warranted.")


if __name__ == "__main__":
    main()
