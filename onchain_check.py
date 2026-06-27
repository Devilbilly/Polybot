"""READ-ONLY on-chain balance check (raw JSON-RPC, no web3). Where is the USDC?
funder + signer, native USDC + bridged USDC.e, + POL gas."""
import json, urllib.request
from eth_account import Account

funder = open("/home/palacedeforsaken/.config/polybot-clob.funder").read().strip()
signer = Account.from_key(open("/home/palacedeforsaken/.config/polybot-clob.key").read().strip()).address
TOKENS = {"USDC.e": "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",
          "USDC":   "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"}
RPCS = ("https://polygon-rpc.com", "https://polygon.llamarpc.com", "https://rpc.ankr.com/polygon")


def rpc(method, params):
    for url in RPCS:
        try:
            req = urllib.request.Request(url, data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                headers={"Content-Type": "application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=12)).get("result")
            if r is not None:
                return r
        except Exception:
            continue
    return None


def erc20(token, who):
    data = "0x70a08231" + who[2:].lower().rjust(64, "0")
    r = rpc("eth_call", [{"to": token, "data": data}, "latest"])
    return int(r, 16) / 1e6 if r and r != "0x" else 0.0


def pol(who):
    r = rpc("eth_getBalance", [who, "latest"])
    return int(r, 16) / 1e18 if r else 0.0


for who, addr in (("funder", funder), ("signer", signer)):
    line = f"{who} ...{addr[-6:]}  POL={pol(addr):.4f}"
    for nm, t in TOKENS.items():
        line += f"  {nm}=${erc20(t, addr):.2f}"
    print(line)
