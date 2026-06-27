"""Trace USDC/USDC.e flow in+out of the funder proxy to find where the ~$117 went. READ-ONLY."""
import json, urllib.request, time

FUNDER = "0x95c6458d0f2064e5669eae41392c0fe260edd7df"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOK = {"USDC.e": "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",
       "USDC": "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"}
RPCS = ("https://polygon-rpc.com", "https://polygon-bor-rpc.publicnode.com",
        "https://polygon.llamarpc.com", "https://rpc.ankr.com/polygon", "https://1rpc.io/matic")

def rpc(method, params, tries=2):
    for _ in range(tries):
        for u in RPCS:
            try:
                req = urllib.request.Request(u, data=json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                    headers={"Content-Type": "application/json", "User-Agent": "Mozilla"})
                j = json.load(urllib.request.urlopen(req, timeout=15))
                if j.get("result") is not None:
                    return j["result"]
            except Exception:
                continue
        time.sleep(1)
    return None

cur = rpc("eth_blockNumber", [])
if not cur:
    print("could not get block number from any RPC"); raise SystemExit
cur = int(cur, 16)
frm = cur - 60000          # ~33h
pad = "0x" + "0" * 24 + FUNDER[2:].lower()
print(f"current block {cur}, scanning from {frm} (~33h)")
for nm, t in TOK.items():
    for dir_name, topics in (("OUT", [TRANSFER, pad]), ("IN", [TRANSFER, None, pad])):
        logs = rpc("eth_getLogs", [{"address": t, "topics": topics, "fromBlock": hex(frm), "toBlock": "latest"}])
        if isinstance(logs, list):
            print(f"{nm} {dir_name}: {len(logs)} transfer(s)")
            for lg in logs[-6:]:
                frm_a = "0x" + lg["topics"][1][26:]; to_a = "0x" + lg["topics"][2][26:]
                amt = int(lg["data"], 16) / 1e6
                print(f"   {frm_a[-6:]} -> {to_a[-6:]}  ${amt:.2f}  blk {int(lg['blockNumber'],16)}")
        else:
            print(f"{nm} {dir_name}: getLogs failed ({str(logs)[:60]})")
