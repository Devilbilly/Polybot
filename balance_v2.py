"""Read the real CLOB collateral balance for the deposit wallet (v2 client, sig_type=3)."""
from eth_account import Account
from py_clob_client_v2 import ClobClient
from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType

key = open("/home/palacedeforsaken/.config/polybot-clob.key").read().strip()
funder = open("/home/palacedeforsaken/.config/polybot-clob.funder").read().strip()
eoa = Account.from_key(key).address
c = ClobClient("https://clob.polymarket.com", 137, key=key, signature_type=3, funder=funder)
c.set_api_creds(c.derive_api_key())
print("funder(deposit wallet):", funder)
try:
    ba = c.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print("raw:", ba)
    b = ba.get("balance") if isinstance(ba, dict) else getattr(ba, "balance", None)
    if b not in (None, ""):
        print("USDC collateral = $%.4f" % (float(b) / 1e6))
except Exception as e:
    print("balance err:", type(e).__name__, str(e)[:200])
