"""
Paper-trade the FAVORITE-CONVERGENCE strategy on historical markets, with an honest
TRAIN/TEST split (odd/even markets) so we can't fool ourselves by overfitting.

Strategy FAV: in the late window (time_progress >= start), if a side's ask is inside
[lo, hi], buy it and HOLD TO SETTLEMENT (favorites converge to 1.0). Realistic costs.
Sequential bankroll with fractional sizing -> reports equity curve, win-rate, max DD.
"""
import glob, sys
import numpy as np
import pandas as pd

FEE = 0.001
SLIP = 0.002

def load_clean(files):
    out = []
    for f in files:
        try:
            df = pd.read_csv(f, usecols=["WS_Bid", "WS_Ask", "Rem_Float"]).dropna()
        except Exception:
            continue
        if len(df) < 50:
            continue
        bid = df["WS_Bid"].values; ask = df["WS_Ask"].values; rem = df["Rem_Float"].values
        final_bid = np.median(bid[-5:])
        if 0.15 < final_bid < 0.85:
            continue                      # unresolved in the log
        out.append((bid, ask, rem, final_bid > 0.5))
    return out

def sim_market(mkt, lo, hi, start, bet_usd):
    """One market: single favorite entry in the late window, hold to settlement. Returns PnL on bet_usd."""
    bid, ask, rem, won_yes = mkt
    tp = 1.0 - rem / 300.0
    for i in range(len(bid)):
        if tp[i] < start:
            continue
        yes_ask = ask[i]
        no_ask = 1.0 - bid[i]
        side = None; price = None
        if lo <= yes_ask <= hi:
            side, price = "YES", yes_ask
        elif lo <= no_ask <= hi:
            side, price = "NO", no_ask
        if side is None:
            continue
        exec_p = min(0.999, price + SLIP)
        tokens = bet_usd / (exec_p * (1 + FEE))
        cost = tokens * exec_p * (1 + FEE)
        win = (side == "YES" and won_yes) or (side == "NO" and not won_yes)
        payout = tokens * 1.0 if win else 0.0
        return payout - cost, win
    return 0.0, None        # never qualified -> no trade this market

def run(markets, lo, hi, start, frac=0.03, label=""):
    bank = 1000.0
    peak = bank; maxdd = 0.0
    wins = trades = 0
    curve = [bank]
    for mkt in markets:
        bet = bank * frac
        pnl, win = sim_market(mkt, lo, hi, start, bet)
        if win is not None:
            trades += 1
            if win:
                wins += 1
        bank += pnl
        peak = max(peak, bank)
        maxdd = max(maxdd, (peak - bank) / peak)
        curve.append(bank)
        if bank < 1.0:
            break
    wr = wins / trades * 100 if trades else 0.0
    roi = (bank / 1000.0 - 1) * 100
    if label:
        print(f"  {label:14s} | final ${bank:8.2f} ({roi:+7.1f}%) | trades {trades:4d} | "
              f"winrate {wr:5.1f}% | maxDD {maxdd*100:4.1f}%")
    return bank, trades, wr, maxdd

def main():
    files = sorted(glob.glob("market_data/Token_*.csv"))
    train_f = files[0::2]
    test_f = files[1::2]
    print(f"[*] Loading TRAIN ({len(train_f)} files) and TEST ({len(test_f)} files)...")
    train = load_clean(train_f)
    test = load_clean(test_f)
    print(f"[*] Clean markets -> train={len(train)}  test={len(test)}\n")

    # ---- Optimize band on TRAIN only (small honest grid) ----
    print("=== TRAIN: grid search (frac=3% bankroll/market) ===")
    best = None
    for lo in [0.70, 0.75, 0.80, 0.85]:
        for hi in [0.90, 0.93, 0.96]:
            for start in [0.0, 0.3, 0.5, 0.7]:
                if hi <= lo:
                    continue
                bank, tr, wr, dd = run(train, lo, hi, start)
                score = bank if tr >= 50 else -1   # require enough activity
                if best is None or score > best[0]:
                    best = (score, lo, hi, start, bank, tr, wr, dd)
    _, lo, hi, start, bank, tr, wr, dd = best
    print(f"\n[+] BEST TRAIN PARAMS: band=[{lo},{hi}] start={start} "
          f"-> ${bank:.2f} | trades {tr} | winrate {wr:.1f}% | DD {dd*100:.1f}%\n")

    # ---- Validate on TEST (untouched) ----
    print("=== OUT-OF-SAMPLE VALIDATION (test markets, same params) ===")
    run(test, lo, hi, start, label="FAV (OOS)")
    print("\n=== BASELINES on TEST ===")
    run(test, 0.0, 0.35, 0.0, label="longshot(bot)")   # what the old bot did
    print("  cash (no trade) | final $ 1000.00 (   +0.0%) | trades    0 | winrate   0.0% | maxDD  0.0%")

    print(f"\n[VERDICT] Use band=[{lo},{hi}], start={start} for the live FAV slot.")

if __name__ == "__main__":
    main()
