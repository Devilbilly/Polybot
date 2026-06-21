"""
Validate the SETTLEMENT PROXY that underlies every favorite-edge result.

All favorite validation scores wins as `final_price > 0.5`. That is only sound if markets
actually CONVERGE to the true BTC outcome at settlement (price -> ~0 or ~1, forced by
arbitrage). This measures the final-price distribution across the raw historical markets:
high clean-convergence => the proxy == the true outcome => the favorite edge is real, not
an artifact of scoring wins against the price's own final value.

Run:  python validate_settlement.py
"""
import glob
import numpy as np
import pandas as pd


def main(data_dir="market_data", limit=2000):
    files = sorted(glob.glob(f"{data_dir}/Token_*.csv"))[:limit]
    finals = []
    for f in files:
        try:
            b = pd.read_csv(f, usecols=["WS_Bid"]).dropna()["WS_Bid"].values
        except Exception:
            continue
        if len(b) < 50 or np.nanmedian(b) > 1.5:   # skip too-short / corrupted (non-prob) files
            continue
        finals.append(float(np.median(b[-5:])))
    finals = np.array(finals)
    n = len(finals)
    clean = float(((finals < 0.10) | (finals > 0.90)).mean() * 100)
    ambig = float(((finals >= 0.40) & (finals <= 0.60)).mean() * 100)
    print(f"[*] {n} markets")
    print(f"    cleanly resolved (final <0.10 or >0.90): {clean:.1f}%")
    print(f"    ambiguous coin-flip (0.40-0.60):         {ambig:.1f}%")
    print(f"    settled NO (<0.10): {(finals<0.10).mean()*100:.1f}%   "
          f"settled YES (>0.90): {(finals>0.90).mean()*100:.1f}%")
    verdict = "VALID — proxy == true outcome, favorite edge is real" if clean > 80 \
        else "WEAK — markets don't converge cleanly; proxy is unreliable"
    print(f"\n[=>] settlement proxy: {verdict}")


if __name__ == "__main__":
    main()
