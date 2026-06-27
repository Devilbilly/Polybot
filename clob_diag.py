"""READ-ONLY: where did the $118 go? balance, allowance, open orders, recent trades, positions."""
from eth_account import Account
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

key = open("/home/palacedeforsaken/.config/polybot-clob.key").read().strip()
funder = open("/home/palacedeforsaken/.config/polybot-clob.funder").read().strip()
eoa = Account.from_key(key).address
sig = 0 if eoa.lower() == funder.lower() else 1
print(f"funder ...{funder[-6:]}  signer ...{eoa[-6:]}  sig_type={sig}")
c = ClobClient("https://clob.polymarket.com", key=key, chain_id=137, signature_type=sig, funder=funder)
c.set_api_creds(c.create_or_derive_api_creds())

for label, at in (("COLLATERAL", AssetType.COLLATERAL),):
    try:
        ba = c.get_balance_allowance(BalanceAllowanceParams(asset_type=at))
        print(f"{label}: {ba}")
    except Exception as e:
        print(f"{label} err: {type(e).__name__} {str(e)[:120]}")

try:
    o = c.get_orders()
    print(f"open orders: {len(o)}")
    for x in (o or [])[:5]:
        print("  ", {k: x.get(k) for k in ("asset_id", "side", "price", "original_size", "size_matched", "status")} if isinstance(x, dict) else x)
except Exception as e:
    print("get_orders err:", type(e).__name__, str(e)[:120])

try:
    t = c.get_trades()
    print(f"recent trades: {len(t)}")
    for x in (t or [])[:5]:
        print("  ", {k: x.get(k) for k in ("asset_id", "side", "price", "size", "status", "match_time")} if isinstance(x, dict) else x)
except Exception as e:
    print("get_trades err:", type(e).__name__, str(e)[:120])
