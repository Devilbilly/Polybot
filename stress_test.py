"""
ADVERSARIAL ROBUSTNESS: does the deployed FAV+stop portfolio survive worse frictions?
Tests a grid of (slippage, fee) AND realistic size-capped fills (you can only buy what's
actually resting on the book — uses real Ask_S1 / Bid_S1). If the edge collapses under
2-5x costs or thin fills, we need to know BEFORE trusting it live.
"""
import glob
import numpy as np

def load_with_depth(files):
    out = []
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
        out.append((bid,ask,bs1,as1,rem,fb>0.5))
    return out

import pandas as pd

def sim(mkt, lo, hi, start, stop, bet, fee, slip, cap_fill):
    bid,ask,bs1,as1,rem,won_yes = mkt
    tp = 1.0 - rem/300.0
    n=len(bid)
    for i in range(n):
        if tp[i] < start: continue
        if lo<=ask[i]<=hi:
            side,price,avail = "YES",ask[i],as1[i]
        elif lo<=(1.0-bid[i])<=hi:
            side,price,avail = "NO",1.0-bid[i],bs1[i]
        else:
            continue
        exec_p=min(0.999, price+slip)
        want = bet/(exec_p*(1+fee))
        tokens = min(want, avail) if cap_fill else want      # realistic: capped by resting size
        if tokens < 1.0:
            continue
        cost = tokens*exec_p*(1+fee)
        # reversal stop
        for j in range(i+1,n):
            cur = bid[j] if side=="YES" else (1.0-ask[j])
            if cur < stop:
                rev = tokens*max(0.0001,cur-slip)*(1-fee)
                return rev-cost, cost
        win = (side=="YES" and won_yes) or (side=="NO" and not won_yes)
        return (tokens if win else 0.0)-cost, cost
    return 0.0, 0.0

def run_portfolio(markets, fee, slip, cap_fill):
    variants=[("main",0.70,0.93,0.50,0.50,0.60),("late",0.85,0.96,0.70,0.50,0.40)]
    banks={v[0]:1000.0*v[5] for v in variants}; peak=1000.0; mdd=0.0
    deployed=0.0; filled_short=0; n=0
    for mkt in markets:
        for name,lo,hi,start,stop,_ in variants:
            pnl,cost = sim(mkt,lo,hi,start,stop,banks[name]*0.02,fee,slip,cap_fill)
            banks[name]+=pnl
        tot=sum(banks.values()); peak=max(peak,tot); mdd=max(mdd,(peak-tot)/peak)
    final=sum(banks.values())
    return (final/1000-1)*100, mdd*100

def main():
    test=load_with_depth(sorted(glob.glob("market_data/Token_*.csv"))[1::2])
    print(f"[*] {len(test)} OOS markets. Deployed config under adversarial frictions:\n")
    print(f"  {'slippage':>9} {'fee':>6} {'fills':>10} {'ROI%':>8} {'maxDD%':>7}")
    base=("base", 0.001, 0.002)
    grid=[("base",      0.001, 0.002, False),
          ("2x slip",   0.001, 0.004, False),
          ("5x slip",   0.001, 0.010, False),
          ("2x fee+slip",0.002, 0.004, False),
          ("real fills", 0.001, 0.002, True),
          ("harsh+fills",0.002, 0.005, True)]
    for tag,fee,slip,cap in grid:
        roi,dd=run_portfolio(test,fee,slip,cap)
        print(f"  {slip:>9.3f} {fee:>6.3f} {('capped' if cap else 'full'):>10} {roi:>+8.1f} {dd:>7.1f}   [{tag}]")

if __name__=="__main__":
    main()
