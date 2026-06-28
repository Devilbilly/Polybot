"""Per-hour A/B: replay buy_p=0.70 (live) vs 0.78 (variant) over the recorded markets, bucket the
per-market returns by CST hour -> one card-style table with a 0.70 column AND a 0.78 column so the
hour-by-hour comparison is visible. Fixed $1k basis (no compounding) so it's a clean A/B. Offline."""
import copy
import json
import sys
import time
from collections import defaultdict

import numpy as np

from polybot import database
from polybot.backtester import compute_fractions

DB = sys.argv[1] if len(sys.argv) > 1 else "archive/recent_market_data.db"
SID = "fav_hold"
HOURS = 12


def mono(m):
    rem = m.get("rem")
    if rem is None or len(rem) < 2:
        return True
    return not bool(np.any(np.diff(rem) > 1.0))


db = database.Database(DB)
rows = db.conn.execute("SELECT market_id, end_ts FROM markets WHERE end_ts IS NOT NULL").fetchall()
ets, arrs = [], []
for mid, e in sorted(rows, key=lambda x: x[1]):
    m = db.load_market(mid)
    if m is not None and mono(m):
        ets.append(e); arrs.append(m)
db.close()
base = json.load(open("polybot/portfolio.json"))


def hourly(buy_p):
    cfg = copy.deepcopy(base)
    cfg["strategies"][0]["params"]["buy_p"] = buy_p
    fr = compute_fractions(arrs, cfg)
    ph = defaultdict(lambda: [0.0, 0, 0])   # hour -> [pnl$ at $1k, n, wins]
    for e, f in zip(ets, fr):
        v = f.get(SID, 0.0)
        if abs(v) < 1e-9:
            continue
        hk = time.strftime("%m-%d %H", time.gmtime(int(e) + 8 * 3600))
        b = ph[hk]; b[0] += v * 1000.0; b[1] += 1; b[2] += 1 if v > 0 else 0
    return ph


LIVE = round(float(base["strategies"][0]["params"].get("buy_p", 0.70)), 2)
FLOORS = sorted(set([0.70, LIVE, 0.78]))
H = {f: hourly(f) for f in FLOORS}

TH = "padding:6px 8px;text-align:right;font-size:13px;border-bottom:2px solid #ddd;"
TD = "padding:5px 8px;text-align:right;font-size:13px;border-bottom:1px solid #eee;"
TDL = TD.replace("right", "left")


def col(v):
    return "#067d06" if v > 0.5 else ("#c0392b" if v < -0.5 else "#777")


def wr(b):
    return f"{round(100*b[2]/b[1])}%" if b[1] else "-"


def is_live(f):
    return abs(f - LIVE) < 1e-9


def lbl(f):
    return f"{f:.2f}" + (" (live)" if is_live(f) else "")


P = ["<div style='max-width:680px;margin:0 auto;padding:0 12px;"
     "font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#222;'>"]
P.append("<h3 style='margin:16px 0 4px;'>Hourly A/B - buy_p floors "
         "<span style='font-size:11px;color:#999;font-weight:400;'>(all replayed, fixed $1k basis; live floor marked)</span></h3>")
P.append("<div style='overflow-x:auto;'><table style='border-collapse:collapse;width:100%;background:#fff;border-radius:8px;'>")
hdr = f"<tr><th style='{TDL.replace('1px solid #eee','2px solid #ddd')}'>hour</th>"
for f in FLOORS:
    mark = "color:#067d06;" if is_live(f) else ""
    hdr += f"<th style='{TH}{mark}'>{lbl(f)} $</th><th style='{TH}'>win%</th>"
P.append(hdr + "</tr>")
allh = sorted(set().union(*[set(h) for h in H.values()]))[-HOURS:]
cum = {f: 0.0 for f in FLOORS}
for hk in allh:
    row = f"<tr><td style='{TDL}'>{hk}</td>"
    for f in FLOORS:
        b = H[f].get(hk, [0.0, 0, 0]); cum[f] += b[0]
        mark = "font-weight:700;" if is_live(f) else ""
        row += f"<td style='{TD}color:{col(b[0])};{mark}'><b>{b[0]:+.1f}</b></td><td style='{TD}'>{wr(b)}</td>"
    P.append(row + "</tr>")
crow = f"<tr><td style='{TDL}'><b>cum (shown)</b></td>"
for f in FLOORS:
    mark = "font-weight:700;" if is_live(f) else ""
    crow += f"<td style='{TD}color:{col(cum[f])};{mark}'><b>{cum[f]:+.1f}</b></td><td style='{TD}'></td>"
P.append(crow + "</tr>")
P.append("</table></div>")
P.append("<p style='font-size:11px;color:#999;margin-top:6px;'>$ at a fixed $1k book (no compounding) so floors are a clean "
         "A/B; magnitudes differ from the live compounding grid above. Live floor = " + lbl(LIVE) +
         ". Single-hour A/B is high-variance - read the cum row.</p>")
P.append("</div>")
print("\n".join(P))
