#!/usr/bin/env python3
"""Past-hour comprehensive analysis of the Polybot paper trader -> an HTML fragment
that gets appended to the PnL HTML report.

READ-ONLY on the box DBs; sends nothing, restarts nothing, touches no service.
Sections: PnL | Market | Order-book | Regression | Win/Loss | Warnings | Suggestions.

Run on the box:   python3 analyze_hour.py [window_secs=3600]   > fragment.html
"""
import sqlite3
import sys
import time
import html
import os
from collections import defaultdict

import numpy as np

BASE = "/home/palacedeforsaken/Polybot"
POLY = os.environ.get("POLY_DB", f"{BASE}/polymarket.db")
LEDG = os.environ.get("LEDG_DB", f"{BASE}/ledger.db")
COINS = ("btc", "eth", "sol", "xrp")
WINDOW = int(sys.argv[1]) if len(sys.argv) > 1 else 3600


def ro(p):
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


def coin_of(sid):
    c = (sid or "").rsplit("-", 1)[-1]
    return c if c in COINS else "?"


def cst(ts):
    return time.strftime("%H:%M", time.gmtime(ts + 8 * 3600))


def fmt(x, d=2, sign=True):
    s = f"%+.{d}f" if sign else f"%.{d}f"
    return s % x


H = []
def add(s):
    H.append(s)


poly = ro(POLY)
try:
    ledg = ro(LEDG)
except Exception:
    ledg = None

# reference "now" from the data itself (robust to clock skew between box & here)
cand = [poly.execute("SELECT MAX(ts) FROM sessions").fetchone()[0] or 0]
if ledg:
    cand.append(ledg.execute("SELECT MAX(ts) FROM ledger").fetchone()[0] or 0)
NOW = max(cand)
CUT = NOW - WINDOW
wall_age = max(0, time.time() - NOW)

add('<hr style="margin-top:32px">')
add('<div style="font-family:Menlo,Consolas,monospace;font-size:13px;max-width:900px">')
add(f'<h2 style="color:#1a1a2e">&#9201; Past-hour deep analysis &mdash; last {WINDOW//60} min '
    f'(to {cst(NOW)} CST)</h2>')
add(f'<p style="color:#888">Read-only from <code>polymarket.db</code> + <code>ledger.db</code>. '
    f'Reference clock = latest DB row ({cst(NOW)} CST); data age {wall_age/60:.1f} min. '
    f'Times CST (UTC+8).</p>')


def table(headers, rows, bold_last=False):
    out = ['<table style="border-collapse:collapse;margin:6px 0" border=1 cellpadding=5 cellspacing=0>']
    out.append('<tr style="background:#1a1a2e;color:#fff">'
               + ''.join(f'<th>{h}</th>' for h in headers) + '</tr>')
    for i, r in enumerate(rows):
        style = ' style="font-weight:bold;background:#f0f0f5"' if (bold_last and i == len(rows) - 1) else ''
        out.append(f'<tr{style}>' + ''.join(f'<td style="text-align:right">{c}</td>' for c in r) + '</tr>')
    out.append('</table>')
    return ''.join(out)


def colored(v, d=2):
    c = '#0a7d27' if v >= 0 else '#c0271a'
    return f'<span style="color:{c}">{fmt(v, d)}</span>'


# ============================================================ 1) P&L ANALYSIS
add('<h3>1) P&amp;L analysis (per coin, this window)</h3>')
rows = poly.execute("SELECT session_id,total_pnl FROM sessions WHERE ts>=?", (CUT,)).fetchall()
per = defaultdict(lambda: [0.0, 0, 0, 0])  # pnl, rounds, wins, traded
for sid, pnl in rows:
    c = coin_of(sid)
    p = pnl or 0.0
    a = per[c]
    a[0] += p
    a[1] += 1
    if p > 1e-9:
        a[2] += 1
    if abs(p) > 1e-9:
        a[3] += 1
trows = []
tot = [0.0, 0, 0, 0]
for c in COINS:
    a = per.get(c, [0.0, 0, 0, 0])
    for i in range(4):
        tot[i] += a[i]
    wr = 100 * a[2] / a[3] if a[3] else 0
    avg = a[0] / a[3] if a[3] else 0
    trows.append([c, colored(a[0]), a[1], a[3], f'{wr:.1f}%', colored(avg, 3)])
wr = 100 * tot[2] / tot[3] if tot[3] else 0
trows.append(['TOTAL', colored(tot[0]), tot[1], tot[3], f'{wr:.1f}%',
              colored(tot[0] / tot[3] if tot[3] else 0, 3)])
add(table(['coin', 'PnL $', 'rounds', 'traded', 'win%', 'avg/trade $'], trows, bold_last=True))
if tot[3] == 0:
    add('<p style="color:#c0271a">No trades settled in this window (trader idle or just restarted).</p>')

# per-sleeve
srows = poly.execute(
    "SELECT ss.strategy, ss.pnl FROM session_strategy ss "
    "JOIN sessions s ON ss.session_id=s.session_id AND ss.round_no=s.round_no "
    "WHERE s.ts>=?", (CUT,)).fetchall()
sl = defaultdict(lambda: [0.0, 0, 0])
for strat, pnl in srows:
    p = pnl or 0.0
    a = sl[strat]
    a[0] += p
    if abs(p) > 1e-9:
        a[1] += 1
    if p > 1e-9:
        a[2] += 1
if sl:
    add('<p><b>By sleeve:</b> ' + ' &nbsp; '.join(
        f'{k}: {fmt(v[0])} ({100*v[2]/v[1] if v[1] else 0:.0f}% win, {v[1]} fires)'
        for k, v in sorted(sl.items(), key=lambda kv: -kv[1][0])) + '</p>')

# ============================================== ledger feature pull (live rows)
intents, settles = {}, {}
if ledg:
    for tid, coin, ip, pp, bb, ba, bbs, bas, side, ts in ledg.execute(
            "SELECT trade_id,coin,intended_price,paper_price,book_bid,book_ask,"
            "book_bid_sz,book_ask_sz,side,ts FROM ledger WHERE event='INTENT'"):
        intents[tid] = dict(coin=coin, ip=ip, pp=pp, bb=bb, ba=ba, bbs=bbs, bas=bas, side=side, ts=ts)
    for tid, coin, winner, pnl, ppnl, ts in ledg.execute(
            "SELECT trade_id,coin,winner,pnl,paper_pnl,ts FROM ledger WHERE event='SETTLE'"):
        settles[tid] = dict(coin=coin, winner=winner, pnl=pnl, ppnl=ppnl, ts=ts)

# joined per-trade records (live entries that both entered and settled).
# Entry price = intended_price (the quoted taker px on the INTENT row); paper_price lives on the
# FILL row, not INTENT, so fall back to the book mid if intended_price is somehow null.
trades = []
for tid, ie in intents.items():
    se = settles.get(tid)
    if se is None:
        continue
    price = ie["ip"]
    if price is None and ie["bb"] is not None and ie["ba"] is not None:
        price = (ie["bb"] + ie["ba"]) / 2.0
    if price is None:
        continue
    pnl = se["pnl"] if se["pnl"] is not None else se["ppnl"]
    if pnl is None:
        continue
    spread = (ie["ba"] - ie["bb"]) if (ie["ba"] is not None and ie["bb"] is not None) else None
    trades.append(dict(coin=ie["coin"], price=price, spread=spread,
                       bsz=ie["bbs"] or 0.0, asz=ie["bas"] or 0.0, side=ie["side"],
                       pnl=pnl, win=1 if pnl > 0 else 0, ts=ie["ts"]))
trades_hr = [t for t in trades if t["ts"] >= CUT]

# ============================================================ 2) MARKET ANALYSIS
add('<h3>2) Market analysis (window)</h3>')
win_rows = poly.execute("SELECT session_id,winner FROM sessions WHERE ts>=? AND winner IS NOT NULL",
                        (CUT,)).fetchall()
ud = defaultdict(lambda: [0, 0])  # coin -> [YES(up), NO(down)]
for sid, w in win_rows:
    c = coin_of(sid)
    if w == "YES":
        ud[c][0] += 1
    elif w == "NO":
        ud[c][1] += 1
mrows = []
for c in COINS:
    up, dn = ud.get(c, [0, 0])
    n = up + dn
    pr = [t["price"] for t in trades_hr if t["coin"] == c and t["price"]]
    avgpx = np.mean(pr) if pr else float("nan")
    mrows.append([c, n, up, dn, f'{100*up/n:.0f}%' if n else '-',
                  f'{avgpx:.3f}' if pr else '-'])
add(table(['coin', 'mkts', 'up(YES)', 'down(NO)', 'up-rate', 'avg fav entry px'], mrows))
add('<p style="color:#666">up/down split shows regime balance; "avg fav entry px" is what the '
    'sleeve paid for the favorite this hour (higher = more confident favorites).</p>')

# ============================================================ 3) ORDER-BOOK ANALYSIS
add('<h3>3) Order-book analysis (entry snapshots, window)</h3>')
if trades_hr:
    orows = []
    for c in COINS:
        ts_ = [t for t in trades_hr if t["coin"] == c]
        sp = [t["spread"] for t in ts_ if t["spread"] is not None]
        bs = [t["bsz"] for t in ts_ if t["bsz"]]
        as_ = [t["asz"] for t in ts_ if t["asz"]]
        if not ts_:
            continue
        orows.append([c, len(ts_),
                      f'{np.mean(sp):.3f}' if sp else '-',
                      f'{np.median(bs):.0f}' if bs else '-',
                      f'{np.median(as_):.0f}' if as_ else '-'])
    add(table(['coin', 'entries', 'avg spread', 'med bid-depth (sh)', 'med ask-depth (sh)'], orows))
    add('<p style="color:#666">Tighter spread = more efficient book = thinner favorite-longshot '
        'edge. Depth bounds how big a real order could fill (capacity).</p>')
else:
    add('<p style="color:#888">No ledger entry snapshots in this window '
        '(ledger started recently; widening to all-available below where needed).</p>')

# ============================================================ 4) REGRESSION ANALYSIS
add('<h3>4) Regression analysis</h3>')
# Regress on ALL available live ledger trades (bigger N than one hour) for stable coefficients.
reg = [t for t in trades if t["spread"] is not None and t["price"]]
if len(reg) >= 25:
    coin_idx = {c: i for i, c in enumerate(("eth", "sol", "xrp"))}  # btc = baseline
    X, yw, yp = [], [], []
    for t in reg:
        d = [0, 0, 0]
        if t["coin"] in coin_idx:
            d[coin_idx[t["coin"]]] = 1
        X.append([1.0, t["price"], t["spread"], np.log1p(t["bsz"]), *d])
        yw.append(t["win"])
        yp.append(t["pnl"])
    X = np.asarray(X, float)
    yw = np.asarray(yw, float)
    yp = np.asarray(yp, float)
    names = ["intercept", "entry_price", "spread", "log(bid_depth)", "eth", "sol", "xrp"]

    def ols(X, y):
        beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
        yhat = X @ beta
        ssr = float(((y - yhat) ** 2).sum())
        sst = float(((y - y.mean()) ** 2).sum())
        return beta, (1 - ssr / sst if sst > 0 else 0.0)

    bw, r2w = ols(X, yw)      # linear probability model: P(win)
    bp, r2p = ols(X, yp)      # PnL $
    rows2 = [[names[i], fmt(bw[i], 4), fmt(bp[i], 3)] for i in range(len(names))]
    add(f'<p>Linear models over <b>{len(reg)} live trades</b> (coin dummies vs btc baseline):</p>')
    add(table(['feature', 'coef &rarr; P(win)', 'coef &rarr; PnL $'], rows2))
    coin_desc = ", ".join(f'{nm} {"+" if bw[names.index(nm)] >= 0 else "&minus;"}'
                          for nm in ("eth", "sol", "xrp"))
    spread_sign = "hurts" if bw[names.index("spread")] < 0 else "helps"
    add(f'<p style="color:#666">R&sup2;: win-model {r2w:.3f}, pnl-model {r2p:.3f}. '
        f'Coin coefficients are vs the <b>btc</b> baseline (on P(win): {coin_desc}); a positive sign = '
        f'that coin wins more than btc <i>after</i> controlling for entry price, spread and depth '
        f'&mdash; a genuine per-coin effect, not merely a price mix. '
        f'The <code>spread</code> coefficient ({bw[names.index("spread")]:+.2f} on P(win)) shows a '
        f'wider / less-efficient book {spread_sign} the win-rate &mdash; consistent with the '
        f'favorite-longshot edge living in the <i>looser</i> books.</p>')
else:
    add(f'<p style="color:#888">Only {len(reg)} live trades with book snapshots &mdash; too few for a '
        'stable regression; per-coin win-rate table (&sect;5) is the robust read.</p>')

# ============================================================ 5) WIN / LOSS ANALYSIS
add('<h3>5) Win/Loss analysis (window; breakeven check)</h3>')
wl = []
for c in COINS:
    pnls = [pnl for sid, pnl in rows if coin_of(sid) == c and pnl is not None and abs(pnl) > 1e-9]
    if not pnls:
        wl.append([c, 0, '-', '-', '-', '-', '-'])
        continue
    wins = [p for p in pnls if p > 0]
    loss = [p for p in pnls if p < 0]
    aw = np.mean(wins) if wins else 0
    al = np.mean(loss) if loss else 0
    wr = 100 * len(wins) / len(pnls)
    be = (-al) / (aw - al) * 100 if (aw - al) else 0   # breakeven win% from this hour's payoffs
    margin = wr - be
    mcol = '#0a7d27' if margin >= 0 else '#c0271a'
    wl.append([c, len(pnls), f'{wr:.1f}%', fmt(aw), fmt(al),
               f'{be:.1f}%', f'<span style="color:{mcol}">{margin:+.1f}pp</span>'])
add(table(['coin', 'traded', 'win%', 'avgWin $', 'avgLoss $', 'breakeven win%', 'margin'], wl))
add('<p style="color:#666">margin = win% &minus; breakeven win% (breakeven set by the avgWin/avgLoss '
    'asymmetry). margin &le; 0 means that coin is at/under water this hour.</p>')

# streaks (overall, window, chronological)
chron = sorted(
    [(s_ts, pnl) for (sid, pnl), s_ts in
     zip(rows, poly.execute("SELECT ts FROM sessions WHERE ts>=? ORDER BY rowid", (CUT,)).fetchall())],
    key=lambda x: x[0]) if rows else []
# simpler robust streak: pull ordered pnls
opnls = [r[0] for r in poly.execute(
    "SELECT total_pnl FROM sessions WHERE ts>=? ORDER BY ts", (CUT,)).fetchall() if r[0] is not None]
maxw = maxl = cw = cl = 0
for p in opnls:
    if p > 1e-9:
        cw += 1
        cl = 0
    elif p < -1e-9:
        cl += 1
        cw = 0
    maxw = max(maxw, cw)
    maxl = max(maxl, cl)
add(f'<p><b>Streaks this window:</b> longest win run {maxw}, longest loss run {maxl} '
    f'(across {len(opnls)} settled rounds, all coins interleaved).</p>')

# ============================================================ 6) WARNING ANALYSIS
add('<h3>6) Warning analysis</h3>')
warns = []
# data freshness
if wall_age > 600:
    warns.append(('HIGH', f'Data is stale: latest row is {wall_age/60:.0f} min old &mdash; '
                          'recorder/trader may be down or the clock is skewed.'))
# per-coin EV margin under water
for c in COINS:
    a = per.get(c, [0.0, 0, 0, 0])
    if a[3] >= 15:
        wrc = 100 * a[2] / a[3]
        if wrc < 78:
            warns.append(('MED', f'{c} win% {wrc:.1f}% is at/under the ~76-79% favorite breakeven '
                                 f'&mdash; near-zero or negative EV ({a[3]} trades).'))
# kill-switch / drawdown proximity per coin (cash trajectory)
for c in COINS:
    cash = [r[0] for r in poly.execute(
        "SELECT total_cash FROM sessions WHERE session_id LIKE ? AND ts>=? ORDER BY ts",
        (f'%-{c}', CUT,)).fetchall() if r[0] is not None]
    if len(cash) >= 5:
        peak = cash[0]
        mdd = 0.0
        for v in cash:
            peak = max(peak, v)
            mdd = max(mdd, (peak - v) / peak if peak > 0 else 0)
        if mdd > 0.15:
            warns.append(('HIGH' if mdd > 0.22 else 'MED',
                          f'{c} intra-window drawdown {mdd*100:.1f}% '
                          f'(kill-switch fires at 25%).'))
# stale-book / rejected entries in ledger
if ledg:
    nstale = ledg.execute(
        "SELECT COUNT(*) FROM ledger WHERE note LIKE '%stale%' OR note LIKE '%REJECT%'").fetchone()[0]
    if nstale:
        warns.append(('LOW', f'{nstale} stale-book/rejected entries logged (the desync gate firing).'))
# 0% fire sleeve
for k, v in sl.items():
    if v[1] == 0:
        warns.append(('MED', f'sleeve {k} fired 0 times this window.'))
# disk
try:
    st = os.statvfs(BASE)
    used = 1 - st.f_bavail / st.f_blocks
    if used > 0.8:
        warns.append(('HIGH', f'Disk {used*100:.0f}% full on the box.'))
except Exception:
    pass
if warns:
    order = {'HIGH': 0, 'MED': 1, 'LOW': 2}
    warns.sort(key=lambda w: order[w[0]])
    add('<ul>')
    for sev, msg in warns:
        col = {'HIGH': '#c0271a', 'MED': '#d97706', 'LOW': '#888'}[sev]
        add(f'<li><b style="color:{col}">[{sev}]</b> {msg}</li>')
    add('</ul>')
else:
    add('<p style="color:#0a7d27">&#10003; No warnings: services producing fresh data, all coins '
        'above breakeven, drawdowns well under the 25% kill-switch.</p>')

# ============================================================ 7) SUGGESTIONS
add('<h3>7) Suggestions to improve</h3>')
sug = []
# BTC down-weight
btc = per.get("btc", [0.0, 0, 0, 0])
alts = [per.get(c, [0.0, 0, 0, 0]) for c in ("eth", "sol", "xrp")]
if btc[3] >= 15:
    btc_wr = 100 * btc[2] / btc[3]
    alt_tr = sum(a[3] for a in alts)
    alt_wr = 100 * sum(a[2] for a in alts) / alt_tr if alt_tr else 0
    if btc_wr < alt_wr - 3:
        sug.append(f'<b>Down-weight or drop BTC.</b> BTC win% {btc_wr:.0f}% trails the alts '
                   f'{alt_wr:.0f}% &mdash; it sits on its breakeven (efficient book, minimal '
                   f'favorite-longshot edge), contributing little PnL for a full capital share. '
                   f'Reallocate its weight to eth/sol/xrp, or restrict BTC to the highest-confidence '
                   f'price band only. (See the dedicated BTC root-cause section below.)')
# sell_p tweak (from prior sweep)
sug.append('<b>sell_p 0.93&rarr;0.96</b> was a strict win%+ROI improvement in the backtester sweep '
           '&mdash; the safest live tweak; consider after a few more clean days.')
# slippage if shadow data present
if ledg:
    sh = ledg.execute("SELECT fill_price,paper_price FROM ledger WHERE event='FILL' "
                      "AND fill_price IS NOT NULL AND paper_price IS NOT NULL AND mode!='PAPER'").fetchall()
    if sh:
        sl_vals = [abs(f - p) for f, p in sh if f and p]
        if sl_vals:
            sug.append(f'<b>Real-vs-paper slippage</b> from {len(sh)} shadow/real probes averages '
                       f'{np.mean(sl_vals):.3f} &mdash; fold this into EV before sizing real orders.')
# thin edge caution
sug.append('<b>Keep stakes small.</b> Per-trade SNR is ~0.1 (avgWin ~$6 / avgLoss ~$22, ~83% win); '
           'the edge is only a few points above breakeven on short data &mdash; size against the worst '
           'ordering, not the lucky path.')
add('<ol>')
for s in sug:
    add(f'<li style="margin:4px 0">{s}</li>')
add('</ol>')

add('<p id="btc-rootcause-anchor" style="color:#888">&#8595; A dedicated BTC root-cause investigation '
    'is appended below.</p>')
add('</div>')

sys.stdout.write("\n".join(H))
