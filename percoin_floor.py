"""Per-coin buy_p floor sweep with OOS train/test split. Answers: one global floor, or per-coin?
For each coin AND combined: chronological 60/40 split, sweep floors, report TRAIN-best vs TEST.
If a coin's train-best floor wins on TEST too -> per-coin tuning is real; if it flips -> overfit."""
import copy, json, sys
import numpy as np
from polybot import database
from polybot.backtester import compute_fractions, _compound
DB = sys.argv[1] if len(sys.argv) > 1 else "archive/recent_market_data.db"
SID = "fav_hold"
FLOORS = [0.70, 0.72, 0.74, 0.76, 0.78, 0.80]
coin_map = json.load(open("coin_map.json"))
base = json.load(open("polybot/portfolio.json"))
def mono(m):
    rem = m.get("rem")
    return True if rem is None or len(rem) < 2 else not bool(np.any(np.diff(rem) > 1.0))
db = database.Database(DB)
rows = db.conn.execute("SELECT market_id,end_ts FROM markets WHERE end_ts IS NOT NULL").fetchall()
mk = []
for mid, e in sorted(rows, key=lambda x: x[1]):
    m = db.load_market(mid)
    if m is not None and mono(m):
        mk.append((coin_map.get(mid), m))
db.close()
cut = int(len(mk) * 0.6)
train, test = mk[:cut], mk[cut:]
def arrs(sub, c=None): return [a for cn, a in sub if c is None or cn == c]
def met(A, bp):
    cfg = copy.deepcopy(base); cfg["strategies"][0]["params"]["buy_p"] = bp
    fr = compute_fractions(A, cfg)
    v = np.array([x.get(SID, 0.0) for x in fr if abs(x.get(SID, 0.0)) > 1e-9])
    if len(v) == 0: return None
    return dict(n=len(v), ev=v.mean()*100, std=v.std()*100, win=100*(v>0).mean(),
                sh=(v.mean()/v.std() if v.std()>0 else 0), dd=_compound(fr,cfg).max_dd_pct)
print(f"markets: {len(mk)} (train {len(train)} / test {len(test)})  floors {FLOORS}")
for c in ("COMBINED","btc","eth","sol","xrp"):
    cc = None if c=="COMBINED" else c
    print(f"\n=== {c} ===")
    print(f"{'floor':>6} | {'trN':>4} {'trSh':>6} | {'teN':>4} {'teEV%':>6} {'teStd%':>6} {'teSh':>6} {'teWin%':>6} {'teDD%':>5}")
    best_tr=(None,-9); best_te=(None,-9)
    for f in FLOORS:
        tr=met(arrs(train,cc),f); te=met(arrs(test,cc),f)
        if not tr or not te: continue
        if tr["sh"]>best_tr[1]: best_tr=(f,tr["sh"])
        if te["sh"]>best_te[1]: best_te=(f,te["sh"])
        print(f"{f:>6.2f} | {tr['n']:>4} {tr['sh']:>+6.3f} | {te['n']:>4} {te['ev']:>+6.2f} {te['std']:>6.2f} {te['sh']:>+6.3f} {te['win']:>5.0f}% {te['dd']:>5.1f}")
    print(f"   train-best floor = {best_tr[0]}   |   test-best floor = {best_te[0]}   -> {'HOLDS' if best_tr[0]==best_te[0] else 'FLIPS (overfit)'}")
