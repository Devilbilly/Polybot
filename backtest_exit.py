"""
NEW LOGIC TEST: does a reversal STOP-LOSS beat hold-to-settlement for FAV?
FAV buys a favorite then (currently) holds to settlement -> full loss on upsets.
Here we exit if the side reverses below `stop`. Tune stop on TRAIN, confirm on TEST.
Reports return AND maxDD (drawdown is the thing we're trying to cut).
"""
import glob
import numpy as np
import backtest_fav as B

FEE, SLIP = 0.001, 0.002

def sim_market_stop(mkt, lo, hi, start, bet, stop):
    """Enter favorite in late window; if it reverses below `stop`, sell; else hold to settlement."""
    bid, ask, rem, won_yes = mkt
    tp = 1.0 - rem / 300.0
    n = len(bid)
    for i in range(n):
        if tp[i] < start:
            continue
        # pick favorite side in band
        if lo <= ask[i] <= hi:
            side = "YES"; entry = ask[i]
        elif lo <= (1.0 - bid[i]) <= hi:
            side = "NO"; entry = 1.0 - bid[i]
        else:
            continue
        exec_p = min(0.999, entry + SLIP)
        tokens = bet / (exec_p * (1 + FEE))
        cost = tokens * exec_p * (1 + FEE)
        # watch for reversal after entry
        if stop is not None:
            for j in range(i + 1, n):
                cur = bid[j] if side == "YES" else (1.0 - ask[j])   # current sellable price of our side
                if cur < stop:
                    rev = tokens * max(0.0001, cur - SLIP) * (1 - FEE)
                    return rev - cost, False
        win = (side == "YES" and won_yes) or (side == "NO" and not won_yes)
        return (tokens if win else 0.0) - cost, win
    return 0.0, None

def run(markets, lo, hi, start, stop, frac=0.02):
    bank = 1000.0; peak = bank; mdd = 0.0; wins = trades = 0
    for mkt in markets:
        pnl, win = sim_market_stop(mkt, lo, hi, start, bank * frac, stop)
        if win is not None:
            trades += 1; wins += 1 if win else 0
        bank += pnl; peak = max(peak, bank); mdd = max(mdd, (peak - bank) / peak)
    return bank, (bank/1000-1)*100, trades, (wins/trades*100 if trades else 0), mdd*100

def main():
    files = sorted(glob.glob("market_data/Token_*.csv"))
    train = B.load_clean(files[0::2]); test = B.load_clean(files[1::2])
    lo, hi, start = 0.70, 0.93, 0.50
    print(f"[*] band [{lo},{hi}] start {start}, frac 2%/market\n")
    print(f"{'stop':>6} | {'TRAIN roi%':>10} {'DD%':>6} | {'TEST roi%':>9} {'DD%':>6} {'win%':>6}")
    for stop in [None, 0.35, 0.45, 0.50, 0.55, 0.60]:
        _, trroi, _, _, trdd = run(train, lo, hi, start, stop)
        _, teroi, _, tewr, tedd = run(test, lo, hi, start, stop)
        tag = "hold " if stop is None else f"{stop:.2f}"
        print(f"{tag:>6} | {trroi:>+10.1f} {trdd:>6.1f} | {teroi:>+9.1f} {tedd:>6.1f} {tewr:>6.1f}")

if __name__ == "__main__":
    main()
