"""Trace outgoing USDC / USDC.e transfers FROM the funder (where did the ~$117 go?). READ-ONLY."""
import json, urllib.request

FUNDER = "0x95c6458d0f2064e5669eae41392c0fe260edd7df"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOK = {"USDC.e": "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",
       "USDC": "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"}

def rpc(m, p):
    for u in ("https://polygon-rpc.com", "https://polygon.llamarpc.com", "https://rpc.ankr.com/polygon"):
        try:
            req = urllib.request.Request(u, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                         headers={"Content-Type": "application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=15)).get("result")
            if r is not None:
                return r
        except Exception:
            pass

cur = int(rpc("eth_blockNumber", []) or "0x0", 16)
frm = cur - 12000           # ~6-7h of Polygon blocks
frompad = "0x" + "0" * 24 + FUNDER[2:].lower()
print(f"scanning blocks {frm}..{cur} for transfers FROM ...{FUNDER[-6:]}")
for nm, t in TOK.items():
    logs = rpc("eth_getLogs", [{"address": t, "topics": [TRANSFER, frompad], "fromBlock": hex(frm), "toBlock": "latest"}])
    if isinstance(logs, list):
        print(f"{nm}: {len(logs)} outgoing transfer(s)")
        for lg in logs[-6:]:
            to = "0x" + lg["topics"][2][26:]
            amt = int(lg["data"], 16) / 1e6
            print(f"   -> to 0x…{to[-6:]}  ${amt:.2f}  block {int(lg['blockNumber'],16)}")
    else:
        print(f"{nm}: getLogs -> {str(logs)[:120]}")
