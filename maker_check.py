"""
Should the favorite strategy use LIMIT (maker) orders to capture the spread on thin books,
instead of TAKING at the ask? Tests an explicit fill model: post a limit at the bid (or
ask-epsilon) at the favorite entry, fill if the ask later touches it within a horizon.

Conclusion (historical BTC 5-min data): NO. Favorites rise into the band, so a resting bid
fills only when the price comes back down -> adversely-selected weakening favorites
(filled win-rate < taker win-rate) AND a low fill rate (most entries missed). The ~1c price
improvement doesn't compensate. Taker (market order) is correct for momentum-style entries.

Run:  python maker_check.py
"""
import numpy as np
from polybot import backtester as bt

LO, HI, START, HORIZON = 0.70, 0.93, 0.50, 40


def main(data_dir="market_data"):
    markets = bt.load_markets(data_dir)
    tw = tn = mf = mn = mw = 0
    tc = mc = 0.0
    for m in markets:
        tp = 1 - m["rem"] / 300
        ask, bid = m["ws_ask"], m["ws_bid"]
        won = m["winner"] == "YES"
        n = len(ask)
        for i in range(n):
            if tp[i] < START:
                continue
            if LO <= ask[i] <= HI:                 # YES favorite in band -> one entry/market
                tn += 1; tc += ask[i]; tw += int(won)
                post = bid[i]; filled = any(ask[j] <= post for j in range(i + 1, min(n, i + HORIZON)))
                mn += 1
                if filled:
                    mf += 1; mc += post; mw += int(won)
                break
    print(f"[*] {tn} favorite entries")
    print(f"    TAKER  : win {tw/tn*100:.1f}%   avg price {tc/tn:.3f}   (fills 100%)")
    print(f"    MAKER  : fill {mf/mn*100:.1f}%   of filled: win {mw/max(1,mf)*100:.1f}%   avg price {mc/max(1,mf):.3f}")
    adverse = tw / tn * 100 - mw / max(1, mf) * 100
    print(f"\n[=>] adverse selection {adverse:+.1f}pp + low fill rate -> TAKER wins; do NOT post limits on favorites")


if __name__ == "__main__":
    main()
