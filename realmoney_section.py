#!/usr/bin/env python3
"""Real-money HTML section for the hourly report: account balance (money left + increase/decrease),
open positions, and realized P&L per coin from the ledger. Read-only except a tiny balance-history
log it appends (to compute the change-over-time). Run on the box; emits an HTML fragment to stdout."""
import os
import sqlite3
import time
from collections import defaultdict

LED = "/home/palacedeforsaken/Polybot/ledger.db"
HIST = "/home/palacedeforsaken/Polybot/balance_history.tsv"
COINS = ("btc", "eth", "sol", "xrp")
c = sqlite3.connect("file:%s?mode=ro" % LED, uri=True)
mx = c.execute("SELECT MAX(ts) FROM ledger").fetchone()[0] or 0
HR = mx - 3600

rows = c.execute(
    "SELECT f.coin, f.side, f.fill_price, f.fill_shares, f.ts, s.winner "
    "FROM ledger f LEFT JOIN ledger s ON s.trade_id=f.trade_id AND s.event='SETTLE' "
    "WHERE f.event='FILL' AND f.mode='LIVE'").fetchall()

life = defaultdict(lambda: [0, 0.0, 0, 0, 0.0])   # coin -> [fills, spent, settled, wins, realized_pnl]
hour = [0, 0.0]                                     # [fills_last_hr, spent_last_hr]
inflight = [0, 0.0]                                 # [open_positions, cost_in_flight]
for coin, side, fp, sh, ts, win in rows:
    coin = coin if coin in COINS else "?"
    fp = float(fp or 0); sh = float(sh or 0); cost = fp * sh
    a = life[coin]
    a[0] += 1; a[1] += cost
    if ts and ts >= HR:
        hour[0] += 1; hour[1] += cost
    if win:
        payout = sh * 1.0 if side == win else 0.0
        a[2] += 1; a[3] += 1 if side == win else 0; a[4] += payout - cost
    else:
        inflight[0] += 1; inflight[1] += cost

realized = sum(a[4] for a in life.values())

# current deposit-wallet USDC (v2/sig3)
bal = None
try:
    from eth_account import Account
    from py_clob_client_v2 import ClobClient
    from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
    key = open("/home/palacedeforsaken/.config/polybot-clob.key").read().strip()
    funder = open("/home/palacedeforsaken/.config/polybot-clob.funder").read().strip()
    cl = ClobClient("https://clob.polymarket.com", 137, key=key, signature_type=3, funder=funder)
    cl.set_api_creds(cl.derive_api_key())
    ba = cl.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    b = ba.get("balance")
    bal = float(b) / 1e6 if b not in (None, "") else None
except Exception:
    pass

# balance history -> change since start of real trading and since last report
hist = []
if os.path.exists(HIST):
    for line in open(HIST):
        try:
            t, bb = line.split()
            hist.append((float(t), float(bb)))
        except Exception:
            pass
first_bal = hist[0][1] if hist else bal
prev_bal = hist[-1][1] if hist else bal
if bal is not None:
    try:
        with open(HIST, "a") as f:
            f.write("%d\t%.4f\n" % (int(time.time()), bal))
    except Exception:
        pass


def money(x):
    return ("$%.2f" % x) if x is not None else "n/a"


def delta(now, ref):
    if now is None or ref is None:
        return "n/a"
    d = now - ref
    col = "#0a7d27" if d >= 0 else "#c0271a"
    return '<span style="color:%s">%+.2f</span>' % (col, d)


# account value estimate = cash + open positions valued at cost (conservative)
acct = (bal + inflight[1]) if bal is not None else None

H = []
H.append('<div style="font-family:Menlo,Consolas,monospace;font-size:13px;max-width:900px">')
H.append('<hr style="margin-top:28px"><h2 style="color:#0a7d27">&#128176; REAL MONEY '
         '(live $1/trade, 4 coins, sig3 deposit wallet)</h2>')
H.append('<table style="border-collapse:collapse;margin:6px 0" border=1 cellpadding=6 cellspacing=0>'
         '<tr style="background:#0a7d27;color:#fff"><th>USDC cash left</th><th>since start</th>'
         '<th>since last report</th><th>open positions</th><th>realized P&amp;L</th><th>acct value est.</th></tr>'
         '<tr style="font-size:15px"><td style="text-align:right"><b>%s</b></td>'
         '<td style="text-align:right">%s</td><td style="text-align:right">%s</td>'
         '<td style="text-align:right">%d (%s)</td>'
         '<td style="text-align:right;color:%s"><b>%+.2f</b></td>'
         '<td style="text-align:right">%s</td></tr></table>'
         % (money(bal), delta(bal, first_bal), delta(bal, prev_bal),
            inflight[0], money(inflight[1]),
            ('#0a7d27' if realized >= 0 else '#c0271a'), realized, money(acct)))
H.append('<p style="color:#888">Cash dips when capital is deployed into open positions and recovers as '
         'winners settle &mdash; the real scorecard is <b>realized P&amp;L</b> (settled trades). '
         'LIVE fills last hour: %d ($%.2f).</p>' % (hour[0], hour[1]))

H.append('<table style="border-collapse:collapse" border=1 cellpadding=5 cellspacing=0>')
H.append('<tr style="background:#1a1a2e;color:#fff"><th>coin</th><th>real fills</th><th>$ spent</th>'
         '<th>settled</th><th>win%</th><th>realized P&amp;L</th></tr>')
tot = [0, 0.0, 0, 0, 0.0]
for coin in COINS:
    a = life.get(coin, [0, 0.0, 0, 0, 0.0])
    for i in range(5):
        tot[i] += a[i]
    wr = (100.0 * a[3] / a[2]) if a[2] else 0.0
    col = '#0a7d27' if a[4] >= 0 else '#c0271a'
    H.append('<tr><td>%s</td><td style="text-align:right">%d</td><td style="text-align:right">$%.2f</td>'
             '<td style="text-align:right">%d</td><td style="text-align:right">%.0f%%</td>'
             '<td style="text-align:right;color:%s">%+.2f</td></tr>'
             % (coin, a[0], a[1], a[2], wr, col, a[4]))
wr = (100.0 * tot[3] / tot[2]) if tot[2] else 0.0
col = '#0a7d27' if tot[4] >= 0 else '#c0271a'
H.append('<tr style="font-weight:bold;background:#f0f0f5"><td>TOTAL</td><td style="text-align:right">%d</td>'
         '<td style="text-align:right">$%.2f</td><td style="text-align:right">%d</td>'
         '<td style="text-align:right">%.0f%%</td><td style="text-align:right;color:%s">%+.2f</td></tr>'
         % (tot[0], tot[1], tot[2], wr, col, tot[4]))
H.append('</table>')
H.append('</div>')
print("\n".join(H))
