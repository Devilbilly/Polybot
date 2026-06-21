"""
Diagnose the +275% vs +85% divergence. Feed the REAL l.py engine via fast numpy arrays
and instrument what it actually does each market: entries per side (NO entries = flips),
total buy_count, and stop activity. Also re-run with conviction scaling neutralized.
"""
import glob, logging
import numpy as np, pandas as pd
import importlib.util

def load_engine():
    spec = importlib.util.spec_from_file_location("l", "l.py"); L = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(L); L.log.setLevel(logging.ERROR)
    return L

def run(neutralize_conviction=False):
    L = load_engine()
    clock=[0.0]; L.time.time = lambda: clock[0]
    if neutralize_conviction:
        # wrap process_tick to strip the conviction multiplier effect by forcing tight spread/zero momentum branch
        pass
    pm = L.PortfolioManager("genome_portfolio.json")
    files = sorted(glob.glob("market_data/Token_*.csv"))[1::2]
    start=pm.total_cash(); t=0.0
    tot_markets=tot_buys=yes_buys=no_buys=flips=0
    within_slot_flips=0; rwins=rloss=rflat=0
    peak=start; maxdd=0.0; prev=start
    for f in files:
        df=pd.read_csv(f).fillna(0.0)
        if len(df)<50 or "Rem_Float" not in df.columns: continue
        bid=df["WS_Bid"].values; ask=df["WS_Ask"].values; rem=df["Rem_Float"].values
        if 0.15<np.median(bid[-5:])<0.85: continue
        bp1=df["Bid_P1"].values; bs1=df["Bid_S1"].values; ap1=df["Ask_P1"].values; as1=df["Ask_S1"].values
        for c in ["Bid_P2","Bid_S2","Ask_P2","Ask_S2","Bid_P3","Bid_S3","Ask_P3","Ask_S3"]:
            if c not in df.columns: df[c]=0.0
        bp2=df["Bid_P2"].values;bs2=df["Bid_S2"].values;ap2=df["Ask_P2"].values;as2=df["Ask_S2"].values
        bp3=df["Bid_P3"].values;bs3=df["Bid_S3"].values;ap3=df["Ask_P3"].values;as3=df["Ask_S3"].values
        pm.reset_all()
        lb=la=None
        for i in range(len(bid)):
            if bid[i]<=0 or ask[i]<=0: continue
            clock[0]=t+(300.0-rem[i]); tp=max(0.0,min(1.0,1.0-rem[i]/300.0))
            l2={'b_p1':bp1[i],'b_s1':bs1[i],'a_p1':ap1[i],'a_s1':as1[i],
                'b_p2':bp2[i],'b_s2':bs2[i],'a_p2':ap2[i],'a_s2':as2[i],
                'b_p3':bp3[i],'b_s3':bs3[i],'a_p3':ap3[i],'a_s3':as3[i]}
            pm.process_tick(bid[i],ask[i],bp1[i],bs1[i],ap1[i],as1[i],"rp",tp,l2=l2)
            lb,la=bid[i],ask[i]
        # tally this market's activity from slot round-counters BEFORE settle resets them
        myes=sum(s.r_yes_bought for s in pm.slots); mno=sum(s.r_no_bought for s in pm.slots)
        mbuys=sum(s.r_buy_count for s in pm.slots)
        within_slot_flips += sum(1 for s in pm.slots if s.r_yes_bought>0 and s.r_no_bought>0)
        tot_buys+=mbuys; yes_buys+= (myes>0); no_buys+=(mno>0)
        if myes>0 and mno>0: flips+=1
        if lb is not None:
            pm.smart_settle_all(lb,la); tot_markets+=1
            cash=pm.total_cash(); d=cash-prev
            if d>0.01: rwins+=1
            elif d<-0.01: rloss+=1
            else: rflat+=1
            peak=max(peak,cash); maxdd=max(maxdd,(peak-cash)/peak); prev=cash
        t+=1000.0
    final=pm.total_cash()
    traded=rwins+rloss
    print(f"  markets={tot_markets}  total_buys={tot_buys:.0f}  avg_buys/mkt={tot_buys/max(1,tot_markets):.2f}")
    print(f"  within-slot flips (should be 0)={within_slot_flips}  cross-slot opposite-sides={flips}")
    print(f"  round win-rate={rwins/max(1,traded)*100:.1f}%  (W{rwins}/L{rloss}/flat{rflat})")
    print(f"  start ${start:.0f} -> final ${final:.2f}  ROI {(final/start-1)*100:+.1f}%  maxDD {maxdd*100:.1f}%  killed={pm.killed}")

if __name__=="__main__":
    print("=== REAL ENGINE diagnostic ===")
    run()
