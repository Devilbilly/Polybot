#!/usr/bin/env python3
"""What actually caused the ~25% maxDD in the backtest?
Rebuild the single-book equity curve, locate the peak->trough, characterise the
losing stretch, see how deep it would go WITHOUT the kill-switch, and how much
the number depends on market ordering.
"""
import random
from collections import Counter
from polybot import backtester as bt
from polybot import database

CFG = {
    "strategies": [{"id": "fav_hold", "name": "fav_convergence", "weight": 1.0,
                    "params": {"buy_p": 0.70, "sell_p": 0.93, "time_cutoff": 0.50,
                               "stop_p": 0.0, "max_buy": 1, "bullet_pct": 0.025}}],
    "engine": {"fee": 0.001, "slippage": 0.002, "cap_fills": True},
    "risk": {"kill_switch_dd": 0.25, "soft_dd": 0.15, "round_loss_limit": 0.08, "min_capital": 50.0},
}

db = database.Database("market_data.db")
mids = db.market_ids()
print("market_id sample:", mids[:4])
print("total markets:", len(mids), "\n")

data = []   # (mid, fraction, winner)
for mid in mids:
    m = db.load_market(mid)
    if m is None:
        continue
    fr = bt.simulate_market(m, CFG)["fav_hold"]
    data.append((mid, fr, m.get("winner")))
db.close()
traded = [d for d in data if abs(d[1]) > 1e-12]
print(f"markets that actually traded: {len(traded)} / {len(data)}")


def run(seq, kill_dd):
    bank = 1000.0; peak = 1000.0; maxdd = 0.0
    killed = False; kill_i = None
    pidx = 0; tidx = 0; cur_pidx = 0
    curve = []
    for i, (mid, fr, win) in enumerate(seq):
        if not killed:
            bank *= (1 + fr)
        if bank > peak:
            peak = bank; cur_pidx = i
        dd = (peak - bank) / peak
        if dd > maxdd:
            maxdd = dd; pidx = cur_pidx; tidx = i
        if not killed and dd > kill_dd:
            killed = True; kill_i = i
        curve.append(bank)
    return dict(maxdd=maxdd, pidx=pidx, tidx=tidx, killed=killed, kill_i=kill_i, curve=curve, final=bank)


# 1) default order WITH kill-switch (matches the sweep's 25.5%)
a = run(data, 0.25)
print(f"\nWITH kill-switch (default order): maxDD={a['maxdd']*100:.1f}%  "
      f"killed={a['killed']} at market #{a['kill_i']}  final=${a['final']:.0f}")
print(f"  peak  @#{a['pidx']}  = ${a['curve'][a['pidx']]:.0f}")
print(f"  trough@#{a['tidx']}  = ${a['curve'][a['tidx']]:.0f}")

# 2) same order WITHOUT kill-switch -> how deep would it really have gone?
b = run(data, 1.0)
print(f"\nWITHOUT kill-switch (same order): maxDD={b['maxdd']*100:.1f}%  final=${b['final']:.0f}")
print(f"  peak  @#{b['pidx']} = ${b['curve'][b['pidx']]:.0f}   trough@#{b['tidx']} = ${b['curve'][b['tidx']]:.0f}")

# 3) characterise the losing stretch (peak->trough of the no-kill run)
seg = data[b['pidx']:b['tidx'] + 1]
nl = sum(1 for _, fr, _ in seg if fr < -1e-12)
nw = sum(1 for _, fr, _ in seg if fr > 1e-12)
print(f"\nthe drawdown stretch: {len(seg)} markets, {nw} wins / {nl} losses "
      f"({100*nw//max(nw+nl,1)}% win vs ~80% normal)")
big = sorted([d for d in seg if d[1] < 0], key=lambda x: x[1])[:8]
print("  biggest single-market losses in the stretch (fraction of bank):")
for mid, fr, win in big:
    print(f"     {str(mid)[:34]:34} {fr*100:+.2f}%  winner={win}")

# 4) order sensitivity -- is 25% real or an ordering artifact?
random.seed(7)
dds = []
for _ in range(40):
    s = data[:]; random.shuffle(s)
    dds.append(run(s, 1.0)['maxdd'] * 100)   # no-kill, to see the true depth distribution
dds.sort()
print(f"\nTRUE maxDD (no kill) over 40 random orderings: "
      f"min {dds[0]:.0f}%  median {dds[len(dds)//2]:.0f}%  max {dds[-1]:.0f}%")
print("(default-order number is just ONE draw from this; ordering matters a lot)")
