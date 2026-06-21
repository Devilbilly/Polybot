"""
Does edge-proportional / fractional-Kelly sizing beat flat sizing for FAV?
Win-rate model w(p) is FIT ON TRAIN ONLY, then used to size bets on TEST (no leakage:
sizing uses the train-fit model, outcomes come from test). Reports return AND drawdown.
"""
import glob
import numpy as np
import backtest_fav as B

FEE, SLIP = 0.001, 0.002

def fit_winrate_model(markets, taus=(180,120,90,60,30,15)):
    """Bucketed P(win|entry price) from train markets (hold-to-settlement)."""
    from collections import defaultdict
    wins=defaultdict(int); tot=defaultdict(int)
    for bid,ask,rem,won_yes in markets:
        tp=1.0-rem/300.0
        for tau in taus:
            i=int(np.argmin(np.abs(rem-tau)))
            if abs(rem[i]-tau)>12: continue
            for price,won in ((ask[i],won_yes),(1.0-bid[i],not won_yes)):
                if 0.0<price<1.0:
                    b=round(price/0.05)*0.05
                    tot[b]+=1; wins[b]+= 1 if won else 0
    return {b:(wins[b]/tot[b]) for b in tot if tot[b]>=30}

def w_of(model, p):
    b=round(p/0.05)*0.05
    return model.get(b, p)   # fall back to calibrated (no edge) if unseen

def kelly_frac(p, w):
    if p<=0 or p>=1: return 0.0
    b=(1.0-p)/p                       # net odds per unit staked
    f=(b*w-(1.0-w))/b                 # full Kelly
    return max(0.0, f)

def sim(markets, lo, hi, start, sizer, base=0.03, cap=0.15):
    bank=1000.0; peak=bank; mdd=0.0; wins=trades=0
    rets=[]
    for bid,ask,rem,won_yes in markets:
        tp=1.0-rem/300.0
        done=False
        for i in range(len(bid)):
            if tp[i]<start or done: continue
            for price,won in ((ask[i],won_yes),(1.0-bid[i],not won_yes)):
                if lo<=price<=hi:
                    mult=sizer(price)
                    frac=min(cap, base*mult)
                    bet=bank*frac
                    exec_p=min(0.999, price+SLIP)
                    tokens=bet/(exec_p*(1+FEE)); cost=tokens*exec_p*(1+FEE)
                    pnl=(tokens if won else 0.0)-cost
                    bank+=pnl; rets.append(pnl/max(1e-9,bet))
                    trades+=1; wins+= 1 if won else 0
                    peak=max(peak,bank); mdd=max(mdd,(peak-bank)/peak)
                    done=True; break
            if done: break
    roi=(bank/1000-1)*100
    sharpe=(np.mean(rets)/np.std(rets)*np.sqrt(len(rets))) if len(rets)>1 and np.std(rets)>0 else 0
    return bank, roi, trades, wins/trades*100 if trades else 0, mdd*100, sharpe

def main():
    files=sorted(glob.glob("market_data/Token_*.csv"))
    train=B.load_clean(files[0::2]); test=B.load_clean(files[1::2])
    model=fit_winrate_model(train)
    print("[*] train win-rate model (price->P win):")
    for b in sorted(model):
        if 0.5<=b<=0.95: print(f"     {b:.2f} -> {model[b]:.3f}  (edge {model[b]-b:+.3f})")
    lo,hi,start=0.70,0.93,0.50

    flat   = lambda p: 1.0
    edgew  = lambda p: np.clip((w_of(model,p)-p)/0.02, 0.2, 3.0)             # ∝ edge, 2% reference
    kelly  = lambda p: np.clip(0.25*kelly_frac(p, w_of(model,p))/0.03, 0.2, 3.0)  # 0.25x Kelly, scaled to ~3% base

    print(f"\n=== SIZING SCHEMES — OUT-OF-SAMPLE (test, band [{lo},{hi}] start {start}) ===")
    print(f"{'scheme':14} {'final$':>9} {'ROI%':>7} {'trades':>7} {'win%':>6} {'maxDD%':>7} {'sharpe':>7}")
    for nm,sz in [("flat 3%",flat),("edge-prop",edgew),("0.25x Kelly",kelly)]:
        bank,roi,tr,wr,dd,sh=sim(test,lo,hi,start,sz)
        print(f"{nm:14} {bank:>9.2f} {roi:>+7.1f} {tr:>7d} {wr:>6.1f} {dd:>7.1f} {sh:>7.2f}")

if __name__=="__main__":
    main()
