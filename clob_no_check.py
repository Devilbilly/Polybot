"""READ-ONLY: confirm the YES/NO token mapping + that the NO book is readable via REST
without any WS subscription. No creds, no orders."""
import json, time, urllib.request
from py_clob_client.client import ClobClient

def gj(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla"})
    return json.load(urllib.request.urlopen(req, timeout=15))

# find a live up/down market: try current 5-min window slugs, then the listing
mk = None
now = int(time.time()); base = now - (now % 300)
for a in ("btc", "eth", "sol", "xrp"):
    for st in (base, base + 300, base - 300):
        try:
            ev = gj(f"https://gamma-api.polymarket.com/events?slug={a}-updown-5m-{st}")
            rows = ev if isinstance(ev, list) else ev.get("data", [])
            for r in rows:
                for m in (r.get("markets", [r]) if isinstance(r, dict) else []):
                    ids = m.get("clobTokenIds")
                    if isinstance(ids, str):
                        ids = json.loads(ids)
                    if ids and len(ids) == 2:
                        mk = m; break
                if mk: break
        except Exception:
            pass
        if mk: break
    if mk: break

if not mk:
    print("no live up/down market right now (between windows) — mapping check skipped"); raise SystemExit

ids = mk.get("clobTokenIds")
if isinstance(ids, str): ids = json.loads(ids)
outs = mk.get("outcomes")
if isinstance(outs, str): outs = json.loads(outs)
print("slug:", mk.get("slug") or mk.get("question", "")[:40])
print("outcomes:", outs)
print(f"clobTokenIds: [0]=...{ids[0][-6:]}  [1]=...{ids[1][-6:]}")

c = ClobClient("https://clob.polymarket.com", chain_id=137)   # public read; no creds for order book
def best(tid):
    ob = c.get_order_book(tid)
    bids = [float(x.price) for x in (ob.bids or [])]
    asks = [float(x.price) for x in (ob.asks or [])]
    return (max(bids) if bids else 0.0, min(asks) if asks else 0.0)

yb, ya = best(ids[0]); nb, na = best(ids[1])
print(f"token[0] '{outs[0]}': bid={yb:.3f} ask={ya:.3f}")
print(f"token[1] '{outs[1]}': bid={nb:.3f} ask={na:.3f}   <- read via REST, NOT subscribed")
print(f"sanity: 1 - token0_bid = {1-yb:.3f}  vs  token1_ask = {na:.3f}  (close => synthetic NO price valid)")
