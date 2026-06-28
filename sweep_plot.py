"""Re-run the buy_p sweep, draw a 4-panel plot (win% / ROI / maxDD / EV vs buy_p), and build an
HTML report. Saves sweep_plot.png + sweep_report.html. Offline; nothing online touched."""
import copy
import json

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from polybot import database
from polybot.backtester import compute_fractions, _compound

DB = "archive/recent_market_data.db"
SID = "fav_hold"


def mono(m):
    rem = m.get("rem")
    if rem is None or len(rem) < 2:
        return True
    return not bool(np.any(np.diff(rem) > 1.0))


db = database.Database(DB)
rows = db.conn.execute("SELECT market_id, end_ts FROM markets WHERE end_ts IS NOT NULL").fetchall()
markets = []
for mid, ets in sorted(rows, key=lambda x: x[1]):
    m = db.load_market(mid)
    if m is not None and mono(m):
        markets.append(m)
db.close()

base = json.load(open("polybot/portfolio.json"))
floors = [round(0.50 + 0.01 * i, 2) for i in range(41)]
win, roi, dd, ev, ent = [], [], [], [], []
for f in floors:
    cfg = copy.deepcopy(base)
    cfg["strategies"][0]["params"]["buy_p"] = f
    fr = compute_fractions(markets, cfg)
    vals = [x.get(SID, 0.0) for x in fr if abs(x.get(SID, 0.0)) > 1e-9]
    n = len(vals)
    r = _compound(fr, cfg)
    win.append(r.win_rate_pct); roi.append(r.roi_pct); dd.append(r.max_dd_pct)
    ev.append((sum(vals) / n if n else 0.0) * 1e3); ent.append(n)

# ---- plot ----
fig, axs = plt.subplots(2, 2, figsize=(12, 8))
fig.suptitle("Polybot entry-floor (buy_p) sweep  —  %d recent markets, sell_p=0.93 fixed" % len(markets),
             fontsize=13, fontweight="bold")


def deco(ax, title, ylabel):
    ax.axvline(0.70, color="#c0271a", ls="--", lw=1.2, label="live 0.70")
    ax.axvspan(0.76, 0.78, color="#0a7d27", alpha=0.12, label="sweet ~0.76-0.78")
    ax.axvspan(0.61, 0.64, color="#999", alpha=0.12, label="valley 0.61-0.64")
    ax.set_xlabel("buy_p (entry floor)"); ax.set_ylabel(ylabel); ax.set_title(title)
    ax.grid(alpha=0.3); ax.legend(fontsize=7, loc="best")


axs[0, 0].plot(floors, win, "-o", ms=3, color="#1a6fb5"); deco(axs[0, 0], "Win rate (rises monotonically)", "win %")
axs[0, 1].plot(floors, roi, "-o", ms=3, color="#7a3fb5"); deco(axs[0, 1], "ROI % (path-influenced)", "ROI %")
axs[1, 0].plot(floors, dd, "-o", ms=3, color="#c0271a"); deco(axs[1, 0], "Max drawdown (lower = better)", "maxDD %")
axs[1, 1].plot(floors, ev, "-o", ms=3, color="#0a7d27"); deco(axs[1, 1], "EV per trade (order-independent)", "EV/trade x1e-3")
plt.tight_layout(rect=[0, 0, 1, 0.95])
plt.savefig("sweep_plot.png", dpi=110)
print("saved sweep_plot.png")

# ---- report ----
def row(f):
    i = floors.index(f)
    return ("<tr%s><td>%.2f</td><td>%d</td><td>%.1f%%</td><td>%+.1f</td><td>%.1f</td><td>%+.5f</td></tr>"
            % (" style='background:#eafbea;font-weight:bold'" if f in (0.76, 0.77) else
               (" style='background:#fdecea'" if f == 0.70 else ""),
               f, ent[i], win[i], roi[i], dd[i], ev[i] / 1e3))


key = [0.50, 0.56, 0.60, 0.63, 0.69, 0.70, 0.75, 0.76, 0.77, 0.78, 0.80, 0.82, 0.85, 0.88, 0.90]
html = """<div style="font-family:Menlo,Consolas,monospace;font-size:13px;max-width:900px">
<h2>Polybot entry-floor (buy_p) sweep — report</h2>
<p style="color:#666">__N__ recent recorded markets, chronological order, sell_p=0.93 fixed. Offline backtest; the live bot is UNCHANGED (still buy_p=0.70).</p>
{{PLOT}}
<h3>Key rows</h3>
<table border=1 cellpadding=5 cellspacing=0 style="border-collapse:collapse">
<tr style="background:#1a1a2e;color:#fff"><th>buy_p</th><th>entered</th><th>win%</th><th>ROI%</th><th>maxDD%</th><th>EV/trade</th></tr>
__ROWS__
</table>
<h3>Findings</h3>
<ul style="line-height:1.6">
<li><b>Win-rate rises monotonically</b> with the floor (the robust signal): 0.70&rarr;83%, 0.77&rarr;87%, 0.85&rarr;90%, 0.90&rarr;91%.</li>
<li><b>Drawdown sweet spot is 0.76&ndash;0.82 (~14&ndash;16%)</b>; the live 0.70 sits at ~22%.</li>
<li><b>EV/trade has two zones</b>: cheap 0.50&ndash;0.56 (high EV but high variance / low win), and <b>0.75&ndash;0.78 (good EV + high win + low DD = best risk-adjusted)</b>. A <b>valley at 0.61&ndash;0.64</b> (avoid), and the edge <b>collapses above 0.85</b> (band pinched by the 0.93 ceiling; negative EV by 0.90).</li>
<li><b>~0.76&ndash;0.77 dominates the live 0.70</b>: +3&ndash;4pp win, &minus;6&ndash;8pp drawdown, similar EV.</li>
</ul>
<div style="padding:8px;background:#fff6e5;border-left:4px solid #d97706">
<b>Caveat / recommendation:</b> this is ONE ~15h period — the 0.76 peak may be overfit (on older data 0.70 and 0.78 had identical EV). The bumps (0.56 peak, 0.61 valley) are likely sample noise; only "win&uarr; with floor" and "low DD at 0.76&ndash;0.82" are robust. <b>Do not deploy on a single-period peak</b> (that's the &minus;99% bot's failure mode). Validate OOS (train/test split + more days) before changing the live floor.
</div>
</div>"""
html = html.replace("__N__", str(len(markets))).replace("__ROWS__", "\n".join(row(f) for f in key))
open("sweep_report.html", "w").write(html)
print("saved sweep_report.html")
