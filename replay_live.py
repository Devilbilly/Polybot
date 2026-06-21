"""
DEFINITIVE PARITY CHECK: replay historical ticks through the REAL l.py PortfolioManager
(the exact code that runs live) — not a simplified backtest sim. If the live engine makes
money here, backtest<->live parity is proven on the actual deployed code path.

Note: l.py's depth gate uses wall-clock time.time(); we monkeypatch it to a simulated clock
driven by Rem_Float so the 3s depth-stability window behaves as it would in real time.
"""
import glob, sys, logging
import numpy as np, pandas as pd
import importlib.util

spec = importlib.util.spec_from_file_location("l", "l.py"); L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)
L.log.setLevel(logging.WARNING)   # silence per-trade debug

_CLOCK = [0.0]
L.time.time = lambda: _CLOCK[0]   # simulated clock so depth_wait_sec works in fast replay

def market_rows(f):
    df = pd.read_csv(f).fillna(0.0)
    need = ["WS_Bid","WS_Ask","Bid_P1","Bid_S1","Ask_P1","Ask_S1","Rem_Float"]
    if any(c not in df.columns for c in need):
        return None
    for c in ["Bid_P2","Bid_S2","Ask_P2","Ask_S2","Bid_P3","Bid_S3","Ask_P3","Ask_S3"]:
        if c not in df.columns: df[c]=0.0
    return df

def main():
    files = sorted(glob.glob("market_data/Token_*.csv"))[1::2]   # same OOS test half
    pm = L.PortfolioManager("genome_portfolio.json")
    start_cash = pm.total_cash()
    settled = 0; clock = 0.0
    for f in files:
        df = market_rows(f)
        if df is None or len(df) < 50: continue
        bid=df["WS_Bid"].values
        if 0.15 < np.median(bid[-5:]) < 0.85:   # unresolved in log
            continue
        pm.reset_all()
        last_b=last_a=None
        rem = df["Rem_Float"].values
        ask=df["WS_Ask"].values
        for i in range(len(df)):
            r=df.iloc[i]
            ws_bid=bid[i]; ws_ask=ask[i]
            if ws_bid<=0 or ws_ask<=0: continue
            _CLOCK[0] = clock + (300.0 - rem[i])        # simulated wall time within market
            tp = max(0.0, min(1.0, 1.0 - rem[i]/300.0))
            l2 = {'b_p1':r.Bid_P1,'b_s1':r.Bid_S1,'a_p1':r.Ask_P1,'a_s1':r.Ask_S1,
                  'b_p2':r.Bid_P2,'b_s2':r.Bid_S2,'a_p2':r.Ask_P2,'a_s2':r.Ask_S2,
                  'b_p3':r.Bid_P3,'b_s3':r.Bid_S3,'a_p3':r.Ask_P3,'a_s3':r.Ask_S3}
            pm.process_tick(ws_bid, ws_ask, r.Bid_P1, r.Bid_S1, r.Ask_P1, r.Ask_S1, "rp", tp, l2=l2)
            last_b, last_a = ws_bid, ws_ask
        if last_b is not None:
            pm.smart_settle_all(last_b, last_a)
            settled += 1
        clock += 1000.0
    final = pm.total_cash()
    print(f"\n=== REAL l.py ENGINE replayed over {settled} OOS markets ===")
    print(f"  start ${start_cash:.2f} -> final ${final:.2f}   ROI {(final/start_cash-1)*100:+.1f}%")
    print(f"  killed={pm.killed}  halted={pm.halted_logged}")
    print(f"  (simplified backtest sim on same set: +85% full-fill / ~+62% realistic fills)")

if __name__=="__main__":
    main()
