"""Check a candidate funds address: on-chain USDC/USDC.e/POL, Polymarket value, and whether the
CLOB shows it as collateral for OUR key. READ-ONLY."""
import json, urllib.request
from eth_account import Account
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

CAND = "0x7d31d4a6c24f947c179cd6439e175b648ca15472"
key = open("/home/palacedeforsaken/.config/polybot-clob.key").read().strip()
signer = Account.from_key(key).address
funder = open("/home/palacedeforsaken/.config/polybot-clob.funder").read().strip()
print(f"candidate ...{CAND[-6:]} | our signer ...{signer[-6:]} | our funder ...{funder[-6:]}")

TOK = {"USDC.e": "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",
       "USDC": "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"}

def rpc(m, p):
    for u in ("https://polygon-rpc.com", "https://polygon.llamarpc.com"):
        try:
            req = urllib.request.Request(u, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                         headers={"Content-Type": "application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=12)).get("result")
            if r is not None:
                return r
        except Exception:
            pass

def erc(t, w):
    r = rpc("eth_call", [{"to": t, "data": "0x70a08231" + w[2:].lower().rjust(64, "0")}, "latest"])
    return int(r, 16) / 1e6 if r and r != "0x" else 0.0

ln = "on-chain candidate: POL=%.4f" % (int(rpc("eth_getBalance", [CAND, "latest"]) or "0x0", 16) / 1e18)
for n, t in TOK.items():
    ln += "  %s=$%.2f" % (n, erc(t, CAND))
print(ln)

def gj(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla"}), timeout=12))

try:
    print("data-api value:", gj("https://data-api.polymarket.com/value?user=%s" % CAND))
    print("data-api positions:", str(gj("https://data-api.polymarket.com/positions?user=%s" % CAND))[:200])
except Exception as e:
    print("data-api err", e)

c = ClobClient("https://clob.polymarket.com", key=key, chain_id=137, signature_type=1, funder=CAND)
c.set_api_creds(c.create_or_derive_api_creds())
ba = c.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
bal = ba.get("balance")
usd = (float(bal) / 1e6) if bal not in (None, "") else 0.0
print("CLOB balance (funder=candidate): $%.2f  allowances_set: %s" % (usd, bool(ba.get("allowances"))))
