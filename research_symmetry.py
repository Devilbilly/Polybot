"""
Is the favorite-longshot bias SYMMETRIC across the up/down side? The significance test (iter 56)
assumed it's direction-agnostic (favorites underpriced regardless of which side). Test it: split
the favorite at entry into YES-favorites (betting BTC up) vs NO-favorites (betting BTC down), and
compare each side's bias (won - mid). Two readings:

  - If symmetric (no significant difference): the iter-56 independence caveat holds, the edge is
    structural (behavioural), and there's nothing to tilt.
  - If asymmetric: one side is more underpriced -> a candidate directional tilt. BUT with only
    ~4 collection days this is almost certainly REGIME-confounded (a bull/bear run makes one side
    win more), NOT a deployable behavioural skew. Reported with that caveat, and a regime check:
    the overall favorite YES-rate (how often the up-side was the favorite & won) flags a trend.
"""
import sys, math
import numpy as np
from polybot import backtester as bt
from polybot.database import determine_winner
import research_significance as sig


def split_obs(markets):
    yes, no = [], []   # each: (mid, ask, won)
    yes_fav = no_fav = 0
    for m in markets:
        wb, wa, rem = m["ws_bid"], m["ws_ask"], m["rem"]
        win = determine_winner(wb)
        if win is None:
            continue
        yes_won = (win == "YES")
        for i in range(len(wb)):
            if rem[i] < 0 or wb[i] <= 0 or wa[i] <= 0:
                continue
            if (1.0 - rem[i] / 300.0) < sig.TP_ENTRY:
                continue
            mid = 0.5 * (wb[i] + wa[i])
            if abs(mid - 0.5) < 1e-9:
                continue
            if mid > 0.5:                                  # YES is favorite (betting UP)
                yes.append((mid, wa[i], 1.0 if yes_won else 0.0)); yes_fav += 1
            else:                                          # NO is favorite (betting DOWN)
                no.append((1.0 - mid, 1.0 - wb[i], 1.0 if not yes_won else 0.0)); no_fav += 1
            break
    return yes, no, yes_fav, no_fav


def report_side(name, obs):
    a = np.array(obs)
    edge = a[:, 2] - a[:, 0]                                # won - mid
    mu, se, t, p, n = sig.one_sided(edge)
    star = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "n.s."
    print(f"  {name:<16} n={n:>5}  bias(won-mid)={mu:+.4f}  SE={se:.4f}  t={t:+.2f}  p={p:.2e}  {star}")
    return edge


def main():
    markets = bt.load_markets(sys.argv[1] if len(sys.argv) > 1 else "market_data")
    yes, no, yf, nf = split_obs(markets)
    print(f"Favorite side counts: YES-fav (up) {yf}  |  NO-fav (down) {nf}  "
          f"(regime flag: {yf/(yf+nf)*100:.0f}% of markets had the UP side as favorite)\n")
    print("=" * 66)
    print("FAVORITE-LONGSHOT BIAS BY SIDE")
    print("=" * 66)
    e_yes = report_side("YES-fav (up)", yes)
    e_no = report_side("NO-fav (down)", no)

    # two-sample (Welch) test of the difference in bias
    m1, m2 = e_yes.mean(), e_no.mean()
    v1, v2 = e_yes.var(ddof=1), e_no.var(ddof=1)
    se = math.sqrt(v1 / len(e_yes) + v2 / len(e_no))
    tdiff = (m1 - m2) / se if se > 0 else 0.0
    pdiff = math.erfc(abs(tdiff) / math.sqrt(2))           # two-sided
    print(f"\n  DIFFERENCE (YES-fav − NO-fav): {m1 - m2:+.4f}  t={tdiff:+.2f}  two-sided p={pdiff:.3f}")
    if pdiff < 0.05:
        print("  => SIGNIFICANT asymmetry — but with ~4 collection days this is almost certainly")
        print("     REGIME (bull/bear) confounding, NOT a deployable behavioural skew. Do not tilt")
        print("     on it without multi-regime / dated data to rule out trend confounding.")
    else:
        print("  => No significant asymmetry: the bias is direction-agnostic, as iter-56 assumed.")
        print("     Confirms the edge is structural (behavioural), not a one-sided trend artifact.")


if __name__ == "__main__":
    main()
