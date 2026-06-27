"""Check / set the Polymarket USDC trading allowance (one-time, required before real orders).
READ-ONLY by default: prints current balance + allowance. Pass --execute to actually SET it
(a REAL on-chain/relayed approval; Polymarket relays gas for proxy/email accounts). Prints no secrets."""
import sys
from eth_account import Account
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

KEYF = "/home/palacedeforsaken/.config/polybot-clob.key"
FUNDF = "/home/palacedeforsaken/.config/polybot-clob.funder"
key = open(KEYF).read().strip(); funder = open(FUNDF).read().strip()
eoa = Account.from_key(key).address
sig = 0 if eoa.lower() == funder.lower() else 1
c = ClobClient("https://clob.polymarket.com", key=key, chain_id=137, signature_type=sig, funder=funder)
c.set_api_creds(c.create_or_derive_api_creds())


def show(tag):
    ba = c.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    bal = ba.get("balance"); usdc = (float(bal) / 1e6) if bal not in (None, "") else None
    print(f"{tag}: USDC balance={'$%.2f' % usdc if usdc is not None else bal}  allowance={ba.get('allowance')}")
    return ba


show("BEFORE")
if "--execute" in sys.argv:
    print(">>> setting USDC collateral allowance — REAL transaction ...")
    try:
        r = c.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        print("update_balance_allowance ->", r)
    except Exception as e:
        print("FAILED:", type(e).__name__, str(e)[:200])
    show("AFTER")
else:
    print("(read-only — pass --execute to actually set the allowance; that is a real tx)")
