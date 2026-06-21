"""
NEW LOGIC TEST: stop-AND-FLIP. When our favorite reverses below stop, don't just exit —
re-enter the NEW favorite (now underpriced by the same bias). Tests whether riding both
sides of volatile markets beats stop-only. Tuned on TRAIN, confirmed on TEST. Reject if it
doesn't hold OOS (same discipline that killed the imbalance idea).
"""
import glob
import numpy as np
import backtest_fav as B

FEE, SLIP = 0.001, 0.002

def sim_flip(mkt, lo, hi, start, bet, stop, max_entries):
    bid, ask, rem, won_yes = mkt
    tp = 1.0 - rem / 300.0
    n = len(bid)
    cash = 0.0                      # realized pnl accumulator (relative)
    pos_side = None; pos_tokens = 0.0; pos_cost = 0.0
    entries = 0
    for i in range(n):
        if tp[i] < start:
            continue
        # manage open position: stop if reversed
        if pos_side is not None:
            cur = bid[i] if pos_side == "YES" else (1.0 - ask[i])
            if cur < stop:
                cash += pos_tokens * max(0.0001, cur - SLIP) * (1 - FEE) - pos_cost
                pos_side = None; pos_tokens = 0.0; pos_cost = 0.0
        # open a position if flat and a favorite is in band
        if pos_side is None and entries < max_entries:
            if lo <= ask[i] <= hi:
                side, price = "YES", ask[i]
            elif lo <= (1.0 - bid[i]) <= hi:
                side, price = "NO", 1.0 - bid[i]
            else:
                continue
            exec_p = min(0.999, price + SLIP)
            pos_tokens = bet / (exec_p * (1 + FEE))
            pos_cost = pos_tokens * exec_p * (1 + FEE)
            pos_side = side; entries += 1
    # settle whatever we still hold
    if pos_side is not None:
        win = (pos_side == "YES" and won_yes) or (pos_side == "NO" and not won_yes)
        cash += (pos_tokens if win else 0.0) - pos_cost
    return cash, entries

def run(markets, lo, hi, start, stop, max_entries, frac=0.02):
    bank = 1000.0; peak = bank; mdd = 0.0
    for mkt in markets:
        pnl, _ = sim_flip(mkt, lo, hi, start, bank * frac, stop, max_entries)
        bank += pnl; peak = max(peak, bank); mdd = max(mdd, (peak - bank) / peak)
    return (bank/1000-1)*100, mdd*100

def main():
    files = sorted(glob.glob("market_data/Token_*.csv"))
    train = B.load_clean(files[0::2]); test = B.load_clean(files[1::2])
    lo, hi, start, stop = 0.70, 0.93, 0.50, 0.50
    print(f"[*] band [{lo},{hi}] start {start} stop {stop}, frac 2%/market\n")
    print(f"{'max_entries':>11} | {'TRAIN roi%':>10} {'DD%':>6} | {'TEST roi%':>9} {'DD%':>6}")
    for me in [1, 2, 3, 5]:
        trroi, trdd = run(train, lo, hi, start, stop, me)
        teroi, tedd = run(test, lo, hi, start, stop, me)
        tag = "1 (no flip)" if me == 1 else f"{me} (flip)"
        print(f"{tag:>11} | {trroi:>+10.1f} {trdd:>6.1f} | {teroi:>+9.1f} {tedd:>6.1f}")

if __name__ == "__main__":
    main()
