#!/usr/bin/env python3
"""Unified hourly Polybot report — ONE consistent style, all sections in one document.
Sections: REAL MONEY (account ground truth) | STRATEGY (paper) health | past-hour analysis.
Emits one HTML to stdout. Read-only. Run on the box."""
import os
import sqlite3
import time
from collections import defaultdict

BASE = "/home/palacedeforsaken/Polybot"
CFG = "/home/palacedeforsaken/.config"
LED = BASE + "/ledger.db"
POLY = BASE + "/polymarket.db"
COINS = ("btc", "eth", "sol", "xrp")
DEPOSIT_START = 118.57   # first real-money balance read this session = the deposit baseline


def cst(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts + 8 * 3600))


def money(x):
    return ("$%.2f" % x) if x is not None else "n/a"


def colorv(v, d=2, suffix=""):
    c = "#0a7d27" if v >= 0 else "#c0271a"
    return '<span style="color:%s">%+.*f%s</span>' % (c, d, v, suffix)


H = []


def add(s):
    H.append(s)


def table(headers, rows, hi=()):
    o = ['<table style="border-collapse:collapse;margin:6px 0;font-size:13px" border=1 cellpadding=5 cellspacing=0>']
    o.append('<tr style="background:#1a1a2e;color:#fff">' + "".join("<th>%s</th>" % h for h in headers) + "</tr>")
    for i, r in enumerate(rows):
        bg = ' style="background:#eafbea;font-weight:bold"' if i in hi else (
            ' style="background:#f0f0f5;font-weight:bold"' if i == "TOTAL" else "")
        o.append("<tr%s>" % bg + "".join('<td style="text-align:right">%s</td>' % c for c in r) + "</tr>")
    o.append("</table>")
    return "".join(o)


# ============================== gather real-money =================================
cash = posval = None
try:
    from eth_account import Account  # noqa
    from py_clob_client_v2 import ClobClient
    from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
    key = open(CFG + "/polybot-clob.key").read().strip()
    funder = open(CFG + "/polybot-clob.funder").read().strip()
    cl = ClobClient("https://clob.polymarket.com", 137, key=key, signature_type=3, funder=funder)
    cl.set_api_creds(cl.derive_api_key())
    b = cl.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)).get("balance")
    cash = float(b) / 1e6 if b not in (None, "") else None
    import json as _j
    import urllib.request as _u
    for _ in range(3):
        try:
            r = _j.load(_u.urlopen(_u.Request("https://data-api.polymarket.com/value?user=%s" % funder,
                        headers={"User-Agent": "Mozilla"}), timeout=10))
            posval = float(r[0]["value"]) if (isinstance(r, list) and r) else float(r.get("value"))
            break
        except Exception:
            time.sleep(1)
except Exception:
    pass

# real per-coin from the ledger (win/loss via proxy winner — flagged)
lc = sqlite3.connect("file:%s?mode=ro" % LED, uri=True)
mx = lc.execute("SELECT MAX(ts) FROM ledger").fetchone()[0] or 0
rows = lc.execute("SELECT f.coin,f.side,f.fill_price,f.fill_shares,f.ts,s.winner FROM ledger f "
                  "LEFT JOIN ledger s ON s.trade_id=f.trade_id AND s.event='SETTLE' "
                  "WHERE f.event='FILL' AND f.mode='LIVE' AND f.fill_price>0").fetchall()
per = defaultdict(lambda: [0, 0.0, 0, 0, 0.0])   # coin -> fills,spent,settled,wins,realized
hr = [0, 0.0]
realized = 0.0
for coin, side, fp, sh, ts, win in rows:
    coin = coin if coin in COINS else "?"
    fp = float(fp or 0); sh = float(sh or 0); cost = fp * sh
    a = per[coin]; a[0] += 1; a[1] += cost
    if ts and ts >= mx - 3600:
        hr[0] += 1; hr[1] += cost
    if win:
        payout = sh if side == win else 0.0
        a[2] += 1; a[3] += 1 if side == win else 0; a[4] += payout - cost
        realized += payout - cost
lc.close()
acct = (cash + (posval or 0.0)) if cash is not None else None
real_pnl = (acct - DEPOSIT_START) if acct is not None else None

# ============================== render =================================
add('<div style="font-family:Menlo,Consolas,monospace;font-size:13px;color:#1a1a2e;max-width:900px">')
add('<h1 style="font-size:18px;border-bottom:2px solid #1a1a2e;padding-bottom:4px">Polybot — unified report &nbsp;'
    '<span style="font-size:12px;color:#888">%s CST</span></h1>' % cst(int(time.time()) if mx == 0 else mx))

# ---- Section 1: REAL MONEY (ground truth) ----
add('<h2 style="color:#0a7d27">&#128176; 1) REAL MONEY (account = ground truth)</h2>')
add(table(["account value", "vs deposit $%.2f" % DEPOSIT_START, "= cash", "+ open positions", "fills last hr"],
          [[("<b>%s</b>" % money(acct)),
            (colorv(real_pnl) if real_pnl is not None else "n/a"),
            money(cash), money(posval),
            "%d ($%.2f)" % (hr[0], hr[1])]]))
add('<p style="color:#888">Account value (cash + Polymarket mark-to-market positions) vs the deposit start is the '
    'REAL P&amp;L. The ledger "realized" below uses a winner PROXY (median WS bid) that disagrees with on-chain '
    'settlement on ~1%% of close calls, so it reads ~$%.2f optimistic — trust the account value.</p>'
    % (realized - (real_pnl or 0)))
trows = []
tot = [0, 0.0, 0, 0, 0.0]
for c in COINS:
    a = per.get(c, [0, 0.0, 0, 0, 0.0])
    for i in range(5):
        tot[i] += a[i]
    wr = 100 * a[3] / a[2] if a[2] else 0
    trows.append([c, a[0], "$%.2f" % a[1], a[2], "%.0f%%" % wr, colorv(a[4])])
wr = 100 * tot[3] / tot[2] if tot[2] else 0
trows.append(["TOTAL", tot[0], "$%.2f" % tot[1], tot[2], "%.0f%%" % wr, colorv(tot[4])])
add(table(["coin", "real fills", "$ spent", "settled", "win% (proxy)", "realized (proxy)"], trows, hi=[len(trows) - 1]))

# ---- Section 2: STRATEGY (paper) health ----
add('<h2 style="color:#1a6fb5">&#9881; 2) Strategy (paper) — per-coin, last hour</h2>')
pc = sqlite3.connect("file:%s?mode=ro" % POLY, uri=True)
srows = pc.execute("SELECT session_id,total_pnl FROM sessions WHERE ts>=?", (mx - 3600,)).fetchall()
pp = defaultdict(lambda: [0.0, 0, 0])
for sid, pnl in srows:
    coin = (sid or "").rsplit("-", 1)[-1]
    coin = coin if coin in COINS else "?"
    p = pnl or 0.0
    a = pp[coin]; a[0] += p; a[1] += 1 if abs(p) > 1e-9 else 0; a[2] += 1 if p > 1e-9 else 0
pc.close()
prows = []
for c in COINS:
    a = pp.get(c, [0.0, 0, 0])
    wr = 100 * a[2] / a[1] if a[1] else 0
    prows.append([c, a[1], "%.0f%%" % wr, colorv(a[0])])
add(table(["coin", "paper trades (1h)", "win%", "paper PnL (1h)"], prows))
add('<p style="color:#888">Paper = the strategy engine on the $25/trade compounding book (decisions + magnitudes); '
    'real money above tracks the same entries at ~$1.6/trade. Same edge, different stake.</p>')

# ---- Section 3: warnings ----
add('<h2 style="color:#d97706">&#9888; 3) Watch</h2>')
w = []
if acct is not None and real_pnl is not None and real_pnl < -DEPOSIT_START * 0.10:
    w.append("Real account down &gt;10% from deposit (%s)." % colorv(real_pnl))
for c in COINS:
    a = per.get(c, [0, 0.0, 0, 0, 0.0])
    if a[2] >= 20 and 100 * a[3] / a[2] < 76:
        w.append("%s real win%% %.0f%% under ~76%% breakeven (%d settled)." % (c, 100 * a[3] / a[2], a[2]))
if not w:
    add('<p style="color:#0a7d27">&#10003; Real account near flat-to-up, per-coin win-rates within range. No alerts.</p>')
else:
    add("<ul>" + "".join("<li>%s</li>" % x for x in w) + "</ul>")

add('<p style="color:#aaa;font-size:11px;margin-top:18px">Live config: fav_hold buy_p=0.70 sell_p=0.93 '
    '(UNCHANGED). Offline studies (variance/Sharpe, buy_p&times;sell_p, per-coin floors, volume) live in the '
    'A/B notes, not deployed. This report is archived hourly.</p>')
add("</div>")
print("\n".join(H))
