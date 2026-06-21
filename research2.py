"""
Hunt for a SECOND, independent edge (to decorrelate the FAV portfolio) + refine FAV.
Everything is measured TRAIN-only first, then confirmed on TEST. We test:

  1. Order-flow imbalance: does L1 (bid_size vs ask_size) predict settlement BEYOND price?
     If yes -> a microstructure signal independent of the favorite-longshot bias.
  2. Depth filter: does requiring a deep book improve FAV win-rate (better fills + signal)?
  3. Buy-the-dip vs buy-the-top within the favorite band (entry-timing refinement).
"""
import glob, sys
import numpy as np
import pandas as pd

COST = 0.003
TAUS = [180, 120, 90, 60, 30, 15]

def collect(files):
    """Return per-observation arrays: price(of a side), depth, imbalance(for that side), won."""
    price, depth, imb, won, band_fav = [], [], [], [], []
    for f in files:
        try:
            df = pd.read_csv(f, usecols=["WS_Bid","WS_Ask","Bid_S1","Ask_S1","Rem_Float"]).dropna()
        except Exception:
            continue
        if len(df) < 50:
            continue
        bid=df["WS_Bid"].values; ask=df["WS_Ask"].values
        bs1=df["Bid_S1"].values; as1=df["Ask_S1"].values; rem=df["Rem_Float"].values
        fb=np.median(bid[-5:])
        if 0.15 < fb < 0.85:
            continue
        won_yes = fb > 0.5
        for tau in TAUS:
            i=int(np.argmin(np.abs(rem-tau)))
            if abs(rem[i]-tau)>12: continue
            tot = bs1[i]+as1[i]
            if tot<=0: continue
            # YES-side view: buying YES at ap... we only have WS ask here, use it as YES entry price
            ya=ask[i]
            if 0.0<ya<1.0:
                price.append(ya); depth.append(tot)
                imb.append((bs1[i]-as1[i])/tot)   # +ve = bid-heavy = bullish YES
                won.append(1 if won_yes else 0)
                band_fav.append(ya>=0.5)
            # NO-side view (symmetric): NO entry = 1-bid, NO is bullish when ask-heavy (as1>bs1)
            na=1.0-bid[i]
            if 0.0<na<1.0:
                price.append(na); depth.append(tot)
                imb.append((as1[i]-bs1[i])/tot)
                won.append(0 if won_yes else 1)
                band_fav.append(na>=0.5)
    return (np.array(price), np.array(depth), np.array(imb), np.array(won), np.array(band_fav))

def main():
    files=sorted(glob.glob("market_data/Token_*.csv"))
    tr=collect(files[0::2]); te=collect(files[1::2])
    print(f"[*] obs: train={len(tr[0])} test={len(te[0])}\n")

    def fav_mask(d): return (d[0]>=0.60)&(d[0]<=0.93)

    # ---- 1. Imbalance edge within the favorite band (control for price) ----
    print("=== 1. ORDER-FLOW IMBALANCE within favorites [0.60-0.93] ===")
    print(f"{'set':5} {'imb<-0.2 wr':>12} {'n':>6} | {'imb>+0.2 wr':>12} {'n':>6} | {'spread(pp)':>10}")
    for nm,d in [("TRAIN",tr),("TEST",te)]:
        m=fav_mask(d); p,dep,im,w,_=[x[m] for x in d]
        neg=im<-0.2; pos=im>0.2
        wn=w[neg].mean() if neg.sum() else 0; wp=w[pos].mean() if pos.sum() else 0
        print(f"{nm:5} {wn:>12.3f} {neg.sum():>6} | {wp:>12.3f} {pos.sum():>6} | {(wp-wn)*100:>10.1f}")

    # ---- 2. Depth filter effect on FAV win-rate ----
    print("\n=== 2. DEPTH FILTER on favorites [0.60-0.93] (win-rate by L1 total depth) ===")
    print(f"{'set':5} {'thin<100 wr':>12} {'n':>6} | {'deep>=300 wr':>13} {'n':>6}")
    for nm,d in [("TRAIN",tr),("TEST",te)]:
        m=fav_mask(d); p,dep,im,w,_=[x[m] for x in d]
        thin=dep<100; deep=dep>=300
        wt=w[thin].mean() if thin.sum() else 0; wd=w[deep].mean() if deep.sum() else 0
        print(f"{nm:5} {wt:>12.3f} {thin.sum():>6} | {wd:>13.3f} {deep.sum():>6}")

    # ---- 3. Combined: FAV + favorable imbalance, EV after cost ----
    print("\n=== 3. FAV [0.60-0.93] + favorable imbalance (imb>0) -> EV/token after cost ===")
    print(f"{'set':5} {'base EV':>9} {'base n':>7} | {'imb>0 EV':>9} {'n':>7} | {'imb>0 & deep EV':>16} {'n':>6}")
    for nm,d in [("TRAIN",tr),("TEST",te)]:
        m=fav_mask(d); p,dep,im,w,_=[x[m] for x in d]
        def ev(mask):
            if mask.sum()<20: return float('nan'),mask.sum()
            return (w[mask].mean()-p[mask].mean()*(1+COST)), mask.sum()
        e0,n0=ev(np.ones(len(p),bool)); e1,n1=ev(im>0); e2,n2=ev((im>0)&(dep>=200))
        print(f"{nm:5} {e0:>+9.3f} {n0:>7} | {e1:>+9.3f} {n1:>7} | {e2:>+16.3f} {n2:>6}")

if __name__=="__main__":
    main()
