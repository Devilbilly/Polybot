"""
Does PRE-ENTRY price STABILITY predict a higher favorite win-rate? (A genuine edge filter,
not leverage.) For each favorite entry, measure recent volatility of the price over the
preceding window; bucket low- vs high-vol; compare realized win-rate. Train AND test.
Reject if it doesn't hold OOS (same discipline that killed imbalance + flip).
"""
import glob
import numpy as np
import pandas as pd

TAUS = [150, 120, 90, 60, 30]
VOL_WIN = 60   # ticks of history for volatility estimate

def collect(files):
    vols, prices, wons = [], [], []
    for f in files:
        try:
            df = pd.read_csv(f, usecols=["WS_Bid","WS_Ask","Rem_Float"]).dropna()
        except Exception:
            continue
        if len(df) < VOL_WIN + 50:
            continue
        bid = df["WS_Bid"].values; ask = df["WS_Ask"].values; rem = df["Rem_Float"].values
        fb = np.median(bid[-5:])
        if 0.15 < fb < 0.85:
            continue
        won_yes = fb > 0.5
        for tau in TAUS:
            i = int(np.argmin(np.abs(rem - tau)))
            if abs(rem[i]-tau) > 12 or i < VOL_WIN:
                continue
            # favorite side + its entry price
            if ask[i] >= 0.60:
                price, won = ask[i], won_yes
                hist = bid[i-VOL_WIN:i]
            elif (1.0-bid[i]) >= 0.60:
                price, won = 1.0-bid[i], (not won_yes)
                hist = bid[i-VOL_WIN:i]
            else:
                continue
            if not (0.60 <= price <= 0.93):
                continue
            vol = np.std(hist)
            vols.append(vol); prices.append(price); wons.append(1 if won else 0)
    return np.array(vols), np.array(prices), np.array(wons)

def main():
    files = sorted(glob.glob("market_data/Token_*.csv"))
    tr = collect(files[0::2]); te = collect(files[1::2])
    # choose a volatility threshold from TRAIN (median), apply to both
    thr = np.median(tr[0])
    print(f"[*] favorite-band entries: train={len(tr[0])} test={len(te[0])} | vol threshold (train median)={thr:.4f}\n")
    print(f"{'set':5} {'STABLE wr':>10} {'n':>6} {'avg_p':>6} | {'CHOPPY wr':>10} {'n':>6} {'avg_p':>6} | {'edge(pp)':>8}")
    for nm, d in [("TRAIN", tr), ("TEST", te)]:
        vol, p, w = d
        stable = vol <= thr; choppy = vol > thr
        ws = w[stable].mean() if stable.sum() else 0; wc = w[choppy].mean() if choppy.sum() else 0
        print(f"{nm:5} {ws:>10.3f} {stable.sum():>6} {p[stable].mean():>6.3f} | "
              f"{wc:>10.3f} {choppy.sum():>6} {p[choppy].mean():>6.3f} | {(ws-wc)*100:>8.1f}")

if __name__ == "__main__":
    main()
