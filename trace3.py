"""Small-range trace + current balances of all candidate addresses. READ-ONLY."""
import json, urllib.request, time

ADDRS = {"oldproxy(EDd7DF)": "0x95c6458d0f2064e5669eae41392c0fe260edd7df",
         "shown(7D31)":      "0x7d31d4a6c24f947c179cd6439e175b648ca15472",
         "signer(727cdE)":   "0x485c55a21e1c7aa642f12823df4fe00c4a727cde"}
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOK = {"USDC.e": "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",
       "USDC": "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"}
RPCS = ("https://polygon-bor-rpc.publicnode.com", "https://polygon-rpc.com",
        "https://polygon.llamarpc.com", "https://rpc.ankr.com/polygon")

def rpc(method, params):
    for u in RPCS:
        try:
            req = urllib.request.Request(u, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                         headers={"Content-Type": "application/json", "User-Agent": "Mozilla"})
            j = json.load(urllib.request.urlopen(req, timeout=15))
            if j.get("result") is not None:
                return j["result"]
        except Exception:
            continue
    return None

def erc(t, w):
    r = rpc("eth_call", [{"to": t, "data": "0x70a08231" + w[2:].lower().rjust(64, "0")}, "latest"])
    return int(r, 16) / 1e6 if r and r != "0x" else 0.0

print("CURRENT balances:")
for nm, a in ADDRS.items():
    print(f"  {nm:18} USDC.e=${erc(TOK['USDC.e'],a):.2f}  USDC=${erc(TOK['USDC'],a):.2f}")

cur = int(rpc("eth_blockNumber", []) or "0x0", 16)
old = ADDRS["oldproxy(EDd7DF)"]
pad = "0x" + "0" * 24 + old[2:].lower()
print(f"\nOUT transfers from oldproxy in last ~3h (block {cur}):")
found = False
for nm, t in TOK.items():
    for c0 in range(cur - 6000, cur, 1500):           # 1500-block chunks
        logs = rpc("eth_getLogs", [{"address": t, "topics": [TRANSFER, pad], "fromBlock": hex(c0), "toBlock": hex(min(c0 + 1500, cur))}])
        if isinstance(logs, list):
            for lg in logs:
                to = "0x" + lg["topics"][2][26:]; amt = int(lg["data"], 16) / 1e6
                print(f"  {nm}: -> 0x{to[2:]}  ${amt:.2f}  blk {int(lg['blockNumber'],16)}")
                found = True
if not found:
    print("  (no outgoing ERC20 transfer found in window — migration may use a different mechanism)")
