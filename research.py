"""
Edge-discovery study: where (if anywhere) is there positive EV in the historical data?

Method: for each market, find the settlement winner, then at several time-to-close points
record every (side, entry_price, won?) pair. Aggregate into a CALIBRATION curve:
  realized win-rate  vs  entry price (the market's implied probability).
If win-rate(p) > p*(1+cost) -> buying that bucket is +EV.  (favorite-longshot bias test)
"""
import glob, sys
import numpy as np
import pandas as pd

COST = 0.003          # fee(0.001) + slippage(0.002), per token of notional
TAUS = [240, 180, 120, 90, 60, 30, 15, 5]   # seconds-to-close sample points

def main(sample=None):
    files = sorted(glob.glob("market_data/Token_*.csv"))
    if sample:
        step = max(1, len(files)//sample)
        files = files[::step]
    print(f"[*] Studying {len(files)} markets (cost={COST}) ...")

    rec_tau, rec_price, rec_won = [], [], []
    clean, skipped = 0, 0
    for f in files:
        try:
            df = pd.read_csv(f, usecols=["WS_Bid", "WS_Ask", "Rem_Float"]).dropna()
        except Exception:
            skipped += 1; continue
        if len(df) < 50:
            skipped += 1; continue
        rem = df["Rem_Float"].values
        bid = df["WS_Bid"].values
        ask = df["WS_Ask"].values
        final_bid = np.median(bid[-5:])
        # Only use markets that actually resolved cleanly in the logged data
        if 0.15 < final_bid < 0.85:
            skipped += 1; continue
        won_yes = final_bid > 0.5
        clean += 1
        for tau in TAUS:
            idx = int(np.argmin(np.abs(rem - tau)))
            if abs(rem[idx] - tau) > 12:
                continue
            a = ask[idx]; b = bid[idx]
            if 0.0 < a < 1.0:          # buy YES at its ask
                rec_tau.append(tau); rec_price.append(a); rec_won.append(1 if won_yes else 0)
            no_ask = 1.0 - b
            if 0.0 < no_ask < 1.0:     # buy NO at its ask (symmetric view)
                rec_tau.append(tau); rec_price.append(no_ask); rec_won.append(0 if won_yes else 1)

    r = pd.DataFrame({"tau": rec_tau, "price": rec_price, "won": rec_won})
    print(f"[*] Clean resolved markets: {clean} | skipped: {skipped} | trade-observations: {len(r)}\n")

    # ---- Calibration by price bucket (all times pooled) ----
    r["bucket"] = (r["price"] * 10).round() / 10
    print("=== CALIBRATION: buy a side at price p, hold to settlement (all times) ===")
    print(f"{'price~':>6} {'n':>7} {'winrate':>8} {'p(impl)':>8} {'edge(wr-p)':>11} {'EV/token':>9}")
    g = r.groupby("bucket")
    for bkt, sub in g:
        if len(sub) < 30:
            continue
        wr = sub["won"].mean()
        ev = wr - bkt * (1 + COST)
        flag = "  <== +EV" if ev > 0 else ""
        print(f"{bkt:>6.1f} {len(sub):>7d} {wr:>8.3f} {bkt:>8.2f} {wr-bkt:>+11.3f} {ev:>+9.3f}{flag}")

    # ---- Favorite buying by time-to-close ----
    print("\n=== FAVORITES ONLY (entry price >= 0.60): EV by time-to-close ===")
    print(f"{'tau(s)':>6} {'n':>7} {'winrate':>8} {'avg_p':>7} {'EV/token':>9}")
    fav = r[r["price"] >= 0.60]
    for tau, sub in fav.groupby("tau"):
        if len(sub) < 20:
            continue
        wr = sub["won"].mean(); ap = sub["price"].mean()
        ev = wr - ap * (1 + COST)
        print(f"{tau:>6d} {len(sub):>7d} {wr:>8.3f} {ap:>7.3f} {ev:>+9.3f}")

    # ---- Longshot buying (what the CURRENT bot does) ----
    print("\n=== LONGSHOTS (entry price <= 0.35): what the current bot buys ===")
    ls = r[r["price"] <= 0.35]
    if len(ls):
        wr = ls["won"].mean(); ap = ls["price"].mean()
        print(f"  n={len(ls)}  winrate={wr:.3f}  avg_price={ap:.3f}  EV/token={wr-ap*(1+COST):+.3f}")

if __name__ == "__main__":
    main(sample=int(sys.argv[1]) if len(sys.argv) > 1 else None)
