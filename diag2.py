"""After a fresh manual trade: dump full latest trade (to find the real maker/owner proxy),
balance, and data-api value/positions for all candidates. READ-ONLY."""
import json, urllib.request
from eth_account import Account
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

key = open("/home/palacedeforsaken/.config/polybot-clob.key").read().strip()
funder = open("/home/palacedeforsaken/.config/polybot-clob.funder").read().strip()
signer = Account.from_key(key).address
c = ClobClient("https://clob.polymarket.com", key=key, chain_id=137, signature_type=1, funder=funder)
c.set_api_creds(c.create_or_derive_api_creds())

t = c.get_trades()
print("trades seen by our key:", len(t))
if t:
    print("LATEST TRADE (full):", json.dumps(t[0], default=str)[:1600])

ba = c.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
print("balance(funder):", ba.get("balance"), " allowances_set:", bool(ba.get("allowances")))

def gj(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla"}), timeout=12))

for nm, a in (("funder", funder), ("0x7D31", "0x7d31d4a6c24f947c179cd6439e175b648ca15472"), ("signer", signer)):
    try:
        v = gj(f"https://data-api.polymarket.com/value?user={a}")
        p = gj(f"https://data-api.polymarket.com/positions?user={a}")
        print(f"data-api {nm}: value={v}  positions={len(p)}")
    except Exception as e:
        print(f"data-api {nm}: err {e}")
