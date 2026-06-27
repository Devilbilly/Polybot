"""Re-sync the CLOB's cached balance/allowance view (allowance already set on-chain).
READ + a sync call. Prints no secrets."""
import time
from eth_account import Account
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

key = open("/home/palacedeforsaken/.config/polybot-clob.key").read().strip()
funder = open("/home/palacedeforsaken/.config/polybot-clob.funder").read().strip()
sig = 0 if Account.from_key(key).address.lower() == funder.lower() else 1
c = ClobClient("https://clob.polymarket.com", key=key, chain_id=137, signature_type=sig, funder=funder)
c.set_api_creds(c.create_or_derive_api_creds())


def rd(tag):
    ba = c.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    bal = ba.get("balance"); usd = (float(bal) / 1e6) if bal not in (None, "") else None
    allw = ba.get("allowances") or ba.get("allowance")
    print(f"{tag}: balance=${usd if usd is not None else bal}  allowances_set={bool(allw)}")
    return usd


rd("before")
print("syncing CLOB balance view (update_balance_allowance, COLLATERAL)...")
try:
    r = c.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print("update ->", r)
except Exception as e:
    print("update err:", type(e).__name__, str(e)[:150])
time.sleep(3)
rd("after")
