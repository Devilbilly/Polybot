"""
Two GENUINELY-NEW, CAUSAL conditioning variables for the favorite edge (both known at entry,
no look-ahead, orthogonal to the existing momentum filter):

  (A) ENTRY SPREAD  — efficiency regime. Wider spread = less efficient book => favorite-longshot
      bias maybe stronger; but wider spread also = higher entry cost. Net effect is empirical.
  (B) TIME-OF-DAY   — intraday liquidity cycle (CSVs carry millisecond time-of-day; never used).
      If the edge concentrates in certain hours, a time filter is deployable & causal.

For each market: at the first tick past tp=0.5, take the favorite (mid>0.5), the price we'd PAY
(YES->ask, NO->1-bid), the entry spread, the window-start time-of-day, and whether the favorite
won (csv winner). Bucket; per bucket report n, win-rate, edge (win-price), per-trade ROI (w/p-1).
A robust monotone pattern => a candidate filter to OOS-test next.
"""
import sys, glob, os, csv
import numpy as np
from polybot import database as D

DATA = sys.argv[1] if len(sys.argv) > 1 else "market_data"
TP_ENTRY = 0.5
SPREAD_BUCKETS = [(0.0, 0.01), (0.01, 0.02), (0.02, 0.03), (0.03, 0.05), (0.05, 0.10), (0.10, 1.0)]
TOD_BUCKETS = [(h, h + 4) for h in range(0, 24, 4)]   # six 4-hour blocks


def parse_start_tod_hours(path):
    """First data-row Timestamp 'HH:MM:SS.mmm' -> hour-of-day float, or None."""
    try:
        with open(path) as f:
            r = csv.reader(f); next(r)
            row = next(r, None)
        if not row:
            return None
        hh, mm, ss = row[0].split(":")
        return int(hh) + int(mm) / 60.0 + float(ss) / 3600.0
    except Exception:
        return None


def favorite_entry(m, tp_entry=TP_ENTRY):
    wb, wa, rem = m["ws_bid"], m["ws_ask"], m["rem"]
    won_yes = (m["winner"] == "YES")
    win = 300.0
    for i in range(len(wb)):
        if rem[i] < 0:
            continue
        tp = 1.0 - rem[i] / win
        if tp < tp_entry or wb[i] <= 0 or wa[i] <= 0:
            continue
        mid = 0.5 * (wb[i] + wa[i])
        if abs(mid - 0.5) < 1e-9:
            continue
        spread = wa[i] - wb[i]
        if mid > 0.5:
            return (wa[i], spread, won_yes)
        return (1.0 - wb[i], spread, (not won_yes))
    return None


def report(title, recs, buckets, keyfn, fmt):
    print(f"\n  {title}")
    print(f"    {'bucket':>12} {'n':>5} {'price':>6} {'win':>6} {'edge':>7} {'ROI/trade':>10}")
    for b in buckets:
        sel = [(p, won) for (key, p, won) in recs if b[0] <= keyfn(key) < b[1]]
        if len(sel) < 40:
            print(f"    {fmt(b):>12} {len(sel):>5}   (too few)"); continue
        pr = np.array([p for p, _ in sel]); wn = np.array([1.0 if w else 0.0 for _, w in sel])
        mp, wr = float(pr.mean()), float(wn.mean())
        print(f"    {fmt(b):>12} {len(sel):>5} {mp:>6.3f} {wr:>6.3f} {wr-mp:>+7.3f} {wr/mp-1:>+9.1%}")


def main():
    files = sorted(glob.glob(os.path.join(DATA, "Token_*.csv")))
    spread_recs, tod_recs = [], []
    for f in files:
        m = D.csv_to_arrays(f)
        if m is None:
            continue
        fe = favorite_entry(m)
        if fe is None:
            continue
        price, spread, won = fe
        spread_recs.append((spread, price, won))
        tod = parse_start_tod_hours(f)
        if tod is not None:
            tod_recs.append((tod, price, won))
    print("=" * 64)
    print(f"FAVORITE EDGE by NEW CAUSAL REGIMES  ({len(spread_recs)} markets)")
    print("=" * 64)
    report("(A) by ENTRY SPREAD", spread_recs, SPREAD_BUCKETS,
           lambda s: s, lambda b: f"{b[0]:.2f}-{b[1]:.2f}")
    report("(B) by TIME-OF-DAY (hour blocks)", tod_recs, TOD_BUCKETS,
           lambda h: h, lambda b: f"{b[0]:02d}-{b[1]:02d}h")
    print("\nA monotone edge gradient in either => a deployable causal filter to OOS-test.")


if __name__ == "__main__":
    main()
