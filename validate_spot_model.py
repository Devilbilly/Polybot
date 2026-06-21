"""
Validate the FOUNDATION of the spot edge: is btc_model.prob_up() accurate on REAL BTC data?

The spot edge assumes the spot-vs-strike model is a good probability of the 5-min up/down
outcome. This fetches real BTC 1s data, and at tau seconds-to-close compares the model's
probability to the REALIZED outcome -- calibration (does p match realized freq?) and
discrimination (AUC). A calibrated, discriminative model is NECESSARY for the spot edge
(though not sufficient -- the edge also needs the market to LAG the model, measurable only
live). This is the spot analog of validate_settlement.py for the favorite edge.

Run:  python validate_spot_model.py
"""
import numpy as np
from polybot import binance as B
from polybot.btc_model import prob_up


def main(n_seconds=36000, tau=60):
    closes = [c for _, c in B.fetch_klines("BTCUSDT", "1s", total=n_seconds)]
    wins = [w for w in B.closes_to_windows(closes, 300) if len(w) == 300]
    vol = B.estimate_vol(closes)
    t = 300 - tau
    preds = np.array([prob_up(w[t], w[0], tau, vol) for w in wins])
    outs = np.array([1 if w[-1] > w[0] else 0 for w in wins])
    print(f"[*] {len(wins)} real windows, vol {vol:.6f}, model evaluated at tau={tau}s\n")
    print(f"{'model p':>10} {'n':>5} {'realized up%':>13}  {'model%':>7}")
    for lo, hi in [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]:
        sel = (preds >= lo) & (preds < hi)
        if sel.sum() < 5:
            continue
        print(f"  {lo:.1f}-{hi:.1f}    {sel.sum():>5} {outs[sel].mean()*100:>12.0f}%  {preds[sel].mean()*100:>6.0f}%")
    order = np.argsort(preds); r = np.empty(len(preds)); r[order] = np.arange(1, len(preds) + 1)
    n1, n0 = outs.sum(), len(outs) - outs.sum()
    auc = (r[outs == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0) if n1 and n0 else 0.5
    verdict = "SOUND" if auc > 0.8 else "WEAK"
    print(f"\n[=>] model AUC vs real BTC outcomes: {auc:.3f}  -> spot model is {verdict} "
          f"(necessary for the spot edge; the LAG is the part only measurable live)")


if __name__ == "__main__":
    main()
