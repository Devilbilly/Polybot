import json, time, urllib.request
from eth_account import Account
from py_clob_client_v2 import ClobClient

key = open("/home/palacedeforsaken/.config/polybot-clob.key").read().strip()
funder = open("/home/palacedeforsaken/.config/polybot-clob.funder").read().strip()
eoa = Account.from_key(key).address
sig = 0 if eoa.lower() == funder.lower() else 1
c = ClobClient("https://clob.polymarket.com", 137, key=key, signature_type=sig, funder=funder)

for meth in ("derive_api_key", "create_or_derive_api_key", "create_api_key"):
    try:
        creds = getattr(c, meth)()
        c.set_api_creds(creds)
        print(meth, "-> OK creds:", bool(creds))
        break
    except Exception as e:
        print(meth, "ERR:", str(e)[:160])


def gj(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla"}), timeout=15))


now = int(time.time()); base = now - (now % 300); tok = None
for a in ("btc", "eth", "sol", "xrp"):
    for st in (base, base - 300, base + 300):
        try:
            ev = gj("https://gamma-api.polymarket.com/events?slug=%s-updown-5m-%s" % (a, st))
            rows = ev if isinstance(ev, list) else ev.get("data", [])
            for r in rows:
                for m in (r.get("markets", [r]) if isinstance(r, dict) else []):
                    ids = m.get("clobTokenIds")
                    if isinstance(ids, str):
                        ids = json.loads(ids)
                    if ids and len(ids) == 2:
                        tok = ids[0]; break
                if tok:
                    break
        except Exception:
            pass
        if tok:
            break
    if tok:
        break

print("found token:", str(tok)[:22] if tok else None)
if tok:
    ob = c.get_order_book(str(tok))
    print("ob type:", type(ob).__name__)
    print("ob attrs:", [x for x in dir(ob) if not x.startswith("_")][:20])
    ak = getattr(ob, "asks", None)
    print("asks repr:", repr(ak)[:300])
    if ak:
        a0 = ak[0]
        print("ask[0]:", type(a0).__name__, [x for x in dir(a0) if not x.startswith("_")][:10], repr(a0)[:140])
