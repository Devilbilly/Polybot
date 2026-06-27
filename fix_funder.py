"""Re-point funder to the CORRECT proxy (0x7D31, the maker_address of the user's trades),
sync the CLOB balance view, and read the real collateral. READ + a sync call."""
import time
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

key = open("/home/palacedeforsaken/.config/polybot-clob.key").read().strip()
NEW = "0x7D31D4a6C24f947c179cD6439E175b648cA15472"
c = ClobClient("https://clob.polymarket.com", key=key, chain_id=137, signature_type=1, funder=NEW)
c.set_api_creds(c.create_or_derive_api_creds())

def rd(tag):
    ba = c.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    bal = ba.get("balance")
    usd = (float(bal) / 1e6) if bal not in (None, "") else 0.0
    print(f"{tag}: balance=${usd:.2f}  allowances_set={bool(ba.get('allowances'))}")

rd("before sync")
try:
    print("update ->", c.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)))
except Exception as e:
    print("update err:", type(e).__name__, str(e)[:150])
time.sleep(3)
rd("after sync")
