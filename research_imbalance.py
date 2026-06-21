"""
Does order-book IMBALANCE predict short-horizon price moves (a scalping edge), as opposed to
the terminal winner (already tested & rejected in ml_winner_check)? This is a DIFFERENT
algorithm: intra-window scalp, not buy-and-hold-to-settle.

Three honest questions, in order of increasing rigor:
  A. PREDICTIVE  : does imbalance correlate with the forward MID move? (information content)
  B. ECONOMIC    : is the predicted move bigger than the SPREAD you must cross? (tradeability)
  C. BACKTEST    : a real scalp — enter at the ask on strong imbalance, exit at the bid k secs
                   later — net of the round-trip spread. Positive => genuine new edge.

If A is significant but B/C are not, the signal is real but un-monetizable on these thin books
(spread eats it). That is the expected microstructure result and an honest negative.
"""
import sys
import numpy as np
from polybot import backtester as bt

DATA = sys.argv[1] if len(sys.argv) > 1 else "market_data"
HORIZONS = (15.0, 30.0, 60.0)     # forward seconds to measure the move
IMB_THRESH = 0.5                  # |imbalance| above this = "strong" signal for the scalp


def fwd_index(rem, i, secs):
    """First index j>i whose time is `secs` later (rem decreases over time)."""
    target = rem[i] - secs
    for j in range(i + 1, len(rem)):
        if rem[j] <= target:
            return j
    return -1


def sample(markets, horizon):
    """Collect (imbalance, fwd_mid_move, spread) triples at spaced ticks across all markets."""
    imb, move, spread = [], [], []
    for m in markets:
        wb, wa = m["ws_bid"], m["ws_ask"]
        bs, aS = m["bid_s1"], m["ask_s1"]
        rem = m["rem"]
        mid = 0.5 * (wb + wa)
        n = len(wb)
        i = 0
        while i < n:
            depth = bs[i] + aS[i]
            sp = wa[i] - wb[i]
            # only sample valid two-sided books with a real spread
            if depth > 0 and wb[i] > 0 and wa[i] > 0 and sp > 0:
                j = fwd_index(rem, i, horizon)
                if j > 0:
                    imb.append((bs[i] - aS[i]) / depth)
                    move.append(mid[j] - mid[i])
                    spread.append(sp)
                    # space samples by ~the horizon so windows barely overlap (independence)
                    i = j
                    continue
            i += 1
    return np.array(imb), np.array(move), np.array(spread)


def scalp_backtest(markets, horizon, thresh):
    """Enter when |imbalance|>=thresh: BUY the favored side by crossing to the ask, exit k secs
    later by crossing back to the bid. PnL per unit = exit_bid - entry_ask (favored side's own
    book). Returns mean PnL per trade (in price units) and trade count."""
    pnls = []
    for m in markets:
        wb, wa = m["ws_bid"], m["ws_ask"]
        bs, aS = m["bid_s1"], m["ask_s1"]
        rem = m["rem"]
        n = len(wb)
        i = 0
        while i < n:
            depth = bs[i] + aS[i]
            if depth > 0 and wb[i] > 0 and wa[i] > 0 and wa[i] > wb[i]:
                imb = (bs[i] - aS[i]) / depth
                if abs(imb) >= thresh:
                    j = fwd_index(rem, i, horizon)
                    if j > 0 and wb[j] > 0 and wa[j] > 0:
                        if imb > 0:   # bid-heavy -> expect UP -> buy YES at ask, sell YES at bid
                            pnls.append(wb[j] - wa[i])
                        else:         # ask-heavy -> expect DOWN -> buy NO; NO_ask=1-YES_bid, NO_bid=1-YES_ask
                            pnls.append((1 - wa[j]) - (1 - wb[i]))
                        i = j
                        continue
            i += 1
    a = np.array(pnls)
    return (float(a.mean()) if len(a) else 0.0, len(a))


def main():
    print(f"Loading real markets from {DATA} ...")
    markets = bt.load_markets(DATA)
    print(f"  {len(markets)} markets\n")

    print("=" * 64)
    print("A. PREDICTIVE  +  B. ECONOMIC")
    print("=" * 64)
    for h in HORIZONS:
        imb, move, spread = sample(markets, h)
        if len(imb) < 100:
            print(f"  h={h:>4.0f}s : too few samples ({len(imb)})"); continue
        corr = float(np.corrcoef(imb, move)[0, 1])
        # sign agreement: when imbalance is strong, does the move go its way?
        strong = np.abs(imb) >= IMB_THRESH
        agree = float(np.mean(np.sign(move[strong]) == np.sign(imb[strong]))) if strong.sum() else float("nan")
        # economic: median favorable move on strong signals vs median spread (the cost to cross)
        fav_move = float(np.median(np.abs(move[strong]))) if strong.sum() else 0.0
        med_spread = float(np.median(spread))
        print(f"  h={h:>4.0f}s  n={len(imb):>6}  corr(imb,move)={corr:+.3f}  "
              f"sign-agree={agree:.2%}  |move|@strong={fav_move:.4f}  spread={med_spread:.4f}  "
              f"move>spread? {'YES' if fav_move > med_spread else 'no'}")

    print("\n" + "=" * 64)
    print(f"C. SCALP BACKTEST (enter |imbalance|>={IMB_THRESH}, net of round-trip spread)")
    print("=" * 64)
    for h in HORIZONS:
        mean_pnl, n = scalp_backtest(markets, h, IMB_THRESH)
        verdict = "PROFITABLE" if mean_pnl > 0 else "loses"
        print(f"  h={h:>4.0f}s  trades={n:>6}  mean PnL/trade={mean_pnl:+.5f}  -> {verdict} after spread")
    print("\n(PnL is in price units, i.e. $ per $1 notional. >0 after spread = real scalp edge.)")


if __name__ == "__main__":
    main()
