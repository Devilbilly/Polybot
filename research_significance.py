"""
Is the favorite-longshot bias STATISTICALLY REAL on the real data — independent of the strategy
machinery (sizing, momentum filter, compounding)? Everything else (CV, OOS, Monte-Carlo) tests
the assembled STRATEGY; this tests the raw CLAIM underneath it: favorites are underpriced.

For each cleanly-resolved market, at the first tick past tp_entry take the favorite (mid>0.5),
its MID (the market's fair estimate) and its ASK (what we'd pay), and whether it WON (clean
0/1 settlement via determine_winner — ambiguous markets excluded). Then two one-sided tests over
INDEPENDENT markets:

  (1) BIAS      : mean(won - mid) > 0  -> the market's own fair value is biased (favorites cheap)
  (2) TRADEABLE : mean(won - ask) > 0  -> still +EV AFTER crossing the spread (real profit)

Reports the mean edge, its standard error, the t-statistic and one-sided p (normal approx), plus
a calibration table (realized win-rate vs price by bucket with a binomial 95% CI). A bucket whose
CI lies ABOVE the diagonal is individually-significant evidence of underpricing.
"""
import sys, math
import numpy as np
from polybot import backtester as bt
from polybot.database import determine_winner

DATA = sys.argv[1] if len(sys.argv) > 1 else "market_data"
TP_ENTRY = 0.5
BUCKETS = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 0.97)]


def favorite_obs(m, tp_entry=TP_ENTRY):
    """(mid, ask, won) for the favorite at the first tick past tp_entry, or None."""
    wb, wa, rem = m["ws_bid"], m["ws_ask"], m["rem"]
    win = determine_winner(wb)            # clean 0/1 settlement; None => ambiguous, skip
    if win is None:
        return None
    yes_won = (win == "YES")
    for i in range(len(wb)):
        if rem[i] < 0 or wb[i] <= 0 or wa[i] <= 0:
            continue
        if (1.0 - rem[i] / 300.0) < tp_entry:
            continue
        mid = 0.5 * (wb[i] + wa[i])
        if abs(mid - 0.5) < 1e-9:
            continue
        if mid > 0.5:                     # YES favorite: pay ask, fair=mid
            return (mid, wa[i], 1.0 if yes_won else 0.0)
        return (1.0 - mid, 1.0 - wb[i], 1.0 if not yes_won else 0.0)   # NO favorite
    return None


def one_sided(edge_arr):
    """mean, SE, t, one-sided p(mean>0) via normal approx."""
    n = len(edge_arr); mu = float(edge_arr.mean()); sd = float(edge_arr.std(ddof=1))
    se = sd / math.sqrt(n)
    t = mu / se if se > 0 else 0.0
    p = 0.5 * math.erfc(t / math.sqrt(2))     # P(Z > t)
    return mu, se, t, p, n


def main():
    markets = bt.load_markets(DATA)
    mids, asks, wins = [], [], []
    for m in markets:
        o = favorite_obs(m)
        if o is None:
            continue
        mids.append(o[0]); asks.append(o[1]); wins.append(o[2])
    mid = np.array(mids); ask = np.array(asks); won = np.array(wins)
    print(f"Resolved favorite markets: {len(won)} (of {len(markets)})\n")

    print("=" * 64)
    print("FAVORITE-LONGSHOT BIAS — significance over INDEPENDENT markets")
    print("=" * 64)
    for label, edge in [("(1) BIAS     won - MID", won - mid),
                        ("(2) TRADEABLE won - ASK", won - ask)]:
        mu, se, t, p, n = one_sided(edge)
        star = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "n.s."
        print(f"  {label}:  edge {mu:+.4f}  SE {se:.4f}  t={t:+.2f}  one-sided p={p:.2e}  {star}")

    print("\n  CALIBRATION (realized win-rate vs MID price, with binomial 95% CI):")
    print(f"    {'bucket':>11} {'n':>5} {'mid':>6} {'win':>6} {'95% CI':>16} {'underpriced?':>13}")
    for lo, hi in BUCKETS:
        sel = (mid >= lo) & (mid < hi)
        nb = int(sel.sum())
        if nb < 30:
            print(f"    {f'{lo:.2f}-{hi:.2f}':>11} {nb:>5}   (too few)"); continue
        w = float(won[sel].mean()); mp = float(mid[sel].mean())
        half = 1.96 * math.sqrt(w * (1 - w) / nb)
        lo_ci, hi_ci = w - half, w + half
        verdict = "YES (CI>mid)" if lo_ci > mp else ("no" if hi_ci < mp else "~fair")
        print(f"    {f'{lo:.2f}-{hi:.2f}':>11} {nb:>5} {mp:>6.3f} {w:>6.3f} "
              f"[{lo_ci:.3f},{hi_ci:.3f}] {verdict:>13}")
    print("\nBIAS p<0.05 => favorites genuinely underpriced (not a strategy artifact).")
    print("TRADEABLE p<0.05 => the bias survives the spread you must cross to capture it.")


if __name__ == "__main__":
    main()
