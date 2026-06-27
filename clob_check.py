"""READ-ONLY Polymarket CLOB credential check. Derives API creds, auto-detects signature_type
(EOA vs email/Magic proxy), confirms USDC balance. PLACES NO ORDERS. Prints NO secrets."""
KEYF = "/home/palacedeforsaken/.config/polybot-clob.key"
FUNDF = "/home/palacedeforsaken/.config/polybot-clob.funder"
HOST = "https://clob.polymarket.com"
CHAIN = 137

from eth_account import Account
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

key = open(KEYF).read().strip()
funder = open(FUNDF).read().strip()
eoa = Account.from_key(key).address
is_eoa = eoa.lower() == funder.lower()
sig = 0 if is_eoa else 1          # email/Magic login -> proxy account -> signature_type 1

print(f"signer EOA == funder address: {is_eoa}  ->  signature_type={sig} "
      f"({'EOA' if is_eoa else 'email/Magic proxy'})")
print(f"funder ...{funder[-6:]}   signer ...{eoa[-6:]}")

c = ClobClient(HOST, key=key, chain_id=CHAIN, signature_type=sig, funder=funder)
try:
    creds = c.create_or_derive_api_creds()
    c.set_api_creds(creds)
    print("API creds derived + set: OK")
except Exception as e:
    print("API creds FAILED:", type(e).__name__, str(e)[:200]); raise SystemExit

try:
    ba = c.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    bal = ba.get("balance")
    usdc = (float(bal) / 1e6) if bal not in (None, "") else None
    print(f"USDC collateral balance: {('$%.2f' % usdc) if usdc is not None else ba}")
    print(f"allowance set: {ba.get('allowance')}")
except Exception as e:
    print("balance read FAILED:", type(e).__name__, str(e)[:200])
print("READ-ONLY check complete — no orders placed.")
