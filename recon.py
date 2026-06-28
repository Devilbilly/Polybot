"""Reconcile: does my ledger 'realized +$X' match the ACTUAL account? (account = ground truth)."""
import json
import os
import sqlite3
import time
import urllib.request

from eth_account import Account
from py_clob_client_v2 import ClobClient
from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType

key = open("/home/palacedeforsaken/.config/polybot-clob.key").read().strip()
funder = open("/home/palacedeforsaken/.config/polybot-clob.funder").read().strip()
c = ClobClient("https://clob.polymarket.com", 137, key=key, signature_type=3, funder=funder)
c.set_api_creds(c.derive_api_key())
cash = float(c.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)).get("balance")) / 1e6


def g(u):
    for _ in range(4):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla"}), timeout=12))
        except Exception:
            time.sleep(1)
    return None


v = g("https://data-api.polymarket.com/value?user=%s" % funder)
posval = float(v[0]["value"]) if (v and isinstance(v, list) and v) else None

ln = sqlite3.connect("file:/home/palacedeforsaken/Polybot/ledger.db?mode=ro", uri=True)
rows = ln.execute("SELECT f.side,f.fill_price,f.fill_shares,s.winner FROM ledger f "
                  "LEFT JOIN ledger s ON s.trade_id=f.trade_id AND s.event='SETTLE' "
                  "WHERE f.event='FILL' AND f.mode='LIVE' AND f.fill_price>0").fetchall()
realized = spent = 0.0
nset = nopen = 0
opencost = 0.0
for side, fp, sh, win in rows:
    fp = float(fp or 0); sh = float(sh or 0)
    if win:
        nset += 1
        realized += (sh if side == win else 0.0) - fp * sh
    else:
        nopen += 1; opencost += fp * sh

H = "/home/palacedeforsaken/Polybot/balance_history.tsv"
first = float([x.split() for x in open(H) if x.strip()][0][1]) if os.path.exists(H) else None

print("cash (USDC, ground truth):            $%.2f" % cash)
print("open positions (data-api value):      %s" % ("$%.2f" % posval if posval is not None else "FETCH FAILED"))
print("open positions (ledger, %d, at cost):  $%.2f" % (nopen, opencost))
acct = cash + (posval if posval is not None else opencost)
print("=> account value (cash + open):       $%.2f" % acct)
print("ledger realized (MY proxy winner):    +$%.2f  over %d settled" % (realized, nset))
print("balance-history first snapshot:       %s" % ("$%.2f" % first if first else "?"))
print("approx true deposit start:            ~$118.57 (first read this session)")
if first:
    print("account_value - first_snap:           %+.2f" % (acct - first))
print("account_value - 118.57:               %+.2f   <- the REAL P&L vs deposit start" % (acct - 118.57))
