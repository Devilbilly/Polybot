"""OOS rolling validation: split the recorded markets chronologically (train = older 60%, test =
newer 40%) and check whether the offline findings hold on UNSEEN data:
  (a) combined: does buy_p=0.76 still beat the live 0.70 on TEST (Sharpe/win/DD)?
  (b) per-coin: does each coin's train-best floor also beat 0.70 on TEST?
Emits an HTML fragment. As the recorder grows, the TEST set grows and the verdict firms up. Offline."""
import copy
import json
import sys

import numpy as np

from polybot import database
from polybot.backtester import compute_fractions, _compound

DB = sys.argv[1] if len(sys.argv) > 1 else "archive/recent_market_data.db"
SID = "fav_hold"
FLOORS = [0.70, 0.74, 0.76, 0.78]
coin_map = json.load(open("coin_map.json"))
base = json.load(open("polybot/portfolio.json"))


def mono(m):
    rem = m.get("rem")
    if rem is None or len(rem) < 2:
        return True
    return not bool(np.any(np.diff(rem) > 1.0))


db = database.Database(DB)
rows = db.conn.execute("SELECT market_id, end_ts FROM markets WHERE end_ts IS NOT NULL").fetchall()
mk = []
for mid, ets in sorted(rows, key=lambda x: x[1]):
    m = db.load_market(mid)
    if m is not None and mono(m):
        mk.append((coin_map.get(mid), m))
db.close()
n = len(mk)
cut = int(n * 0.6)
train, test = mk[:cut], mk[cut:]


def met(arrs, bp):
    cfg = copy.deepcopy(base)
    cfg["strategies"][0]["params"]["buy_p"] = bp
    fr = compute_fractions(arrs, cfg)
    vals = np.array([x.get(SID, 0.0) for x in fr if abs(x.get(SID, 0.0)) > 1e-9])
    if len(vals) == 0:
        return None
    ev, std = vals.mean(), vals.std()
    return dict(n=len(vals), win=100 * (vals > 0).mean(),
                sharpe=(ev / std if std > 0 else 0.0), dd=_compound(fr, cfg).max_dd_pct)


def arrs(subset, coin=None):
    return [a for cn, a in subset if coin is None or cn == coin]


# ---- render in the SAME card style as the paper report ----
TH = "padding:6px 8px;text-align:right;font-size:13px;border-bottom:2px solid #ddd;"
THL = TH.replace("right", "left")
TD = "padding:5px 8px;text-align:right;font-size:13px;border-bottom:1px solid #eee;"
TDL = TD.replace("right", "left")
CARD = "border-collapse:collapse;width:100%;background:#fff;border-radius:8px;overflow:hidden;"


def col(v):
    return "#067d06" if v > 0.005 else ("#c0392b" if v < -0.005 else "#777")


H = ["<div style='max-width:680px;margin:0 auto;padding:0 12px;"
     "font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#222;'>"]
H.append("<h3 style='margin:16px 0 4px;'>OOS validation <span style='font-size:11px;color:#999;"
         "font-weight:400;'>(train older %d / test newer %d mkts)</span></h3>" % (len(train), len(test)))
H.append("<table style='%s'>" % CARD)
H.append("<tr><th style='%s'>buy_p</th><th style='%s'>train Sh</th><th style='%s'>test Sh</th>"
         "<th style='%s'>test win%%</th><th style='%s'>test DD%%</th></tr>" % (THL, TH, TH, TH, TH))
test_sharpe = {}
for f in FLOORS:
    tr = met(arrs(train), f)
    te = met(arrs(test), f)
    if not tr or not te:
        continue
    test_sharpe[f] = te["sharpe"]
    H.append("<tr><td style='%s'><b>%.2f</b></td><td style='%s'>%.3f</td>"
             "<td style='%scolor:%s;'><b>%.3f</b></td><td style='%s'>%.0f%%</td><td style='%s'>%.1f</td></tr>"
             % (TDL, f, TD, tr["sharpe"], TD, col(te["sharpe"]), te["sharpe"], TD, te["win"], TD, te["dd"]))
H.append("</table>")
if 0.76 in test_sharpe and 0.70 in test_sharpe:
    holds = test_sharpe[0.76] >= test_sharpe[0.70]
    H.append("<div style='font-size:13px;color:%s;margin:4px 0;'><b>OOS: 0.76 %s 0.70 on TEST</b> "
             "(test Sharpe %.3f vs %.3f) - one window, needs several days.</div>"
             % (col(0.01 if holds else -0.01), "HOLDS over" if holds else "does NOT beat",
                test_sharpe[0.76], test_sharpe[0.70]))
H.append("<h3 style='margin:14px 0 4px;'>Per-coin floor stability "
         "<span style='font-size:11px;color:#999;font-weight:400;'>(train-best vs test, small N)</span></h3>")
H.append("<table style='%s'>" % CARD)
H.append("<tr><th style='%s'>coin</th><th style='%s'>train-best</th><th style='%s'>that: test Sh</th>"
         "<th style='%s'>0.70: test Sh</th><th style='%s'>holds?</th></tr>" % (THL, TH, TH, TH, TH))
for c in ("btc", "eth", "sol", "xrp"):
    trm = {f: met(arrs(train, c), f) for f in FLOORS}
    trm = {f: v for f, v in trm.items() if v}
    if not trm:
        continue
    bestf = max(trm, key=lambda f: trm[f]["sharpe"])
    te_best = met(arrs(test, c), bestf)
    te_70 = met(arrs(test, c), 0.70)
    if not te_best or not te_70:
        continue
    ok = te_best["sharpe"] >= te_70["sharpe"]
    H.append("<tr><td style='%s'><b>%s</b></td><td style='%s'>%.2f</td><td style='%s'>%.3f</td>"
             "<td style='%s'>%.3f</td><td style='%scolor:%s;'><b>%s</b></td></tr>"
             % (TDL, c, TD, bestf, TD, te_best["sharpe"], TD, te_70["sharpe"], TD,
                "#067d06" if ok else "#c0392b", "yes" if ok else "no"))
H.append("</table>")
H.append("<p style='font-size:11px;color:#999;margin-top:8px;'>If the same floors keep winning OOS over "
         "several days the pattern is real; if 'holds?' flips hour-to-hour it was overfit. Live config UNCHANGED.</p>")
H.append("</div>")
print("\n".join(H))
