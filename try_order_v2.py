"""Drop ONE real ~$1 (1-share) FOK order via py-clob-client-v2.

The V1 py-clob-client (0.34.6) is archived and signs the OLD EIP-712 order schema, which the
exchange now rejects with `order_version_mismatch` ("invalid order version"). V2 signs domain "2".
HARD cap = 1 share, FOK (fills instantly or cancels). Real cost < $1. Account is sig_type=1 (proxy),
not the type-3 deposit wallet that still has a v2 bug.
"""
import json, math, sys, time, urllib.request
from eth_account import Account
from py_clob_client_v2 import ClobClient
from py_clob_client_v2.clob_types import OrderArgs
try:
    from py_clob_client_v2.order_builder.constants import BUY
except Exception:
    BUY = "BUY"

KEYF = "/home/palacedeforsaken/.config/polybot-clob.key"
FUNDF = "/home/palacedeforsaken/.config/polybot-clob.funder"
key = open(KEYF).read().strip(); funder = open(FUNDF).read().strip()
eoa = Account.from_key(key).address
# sig override via argv[1] (0=EOA, 1=Magic proxy, 2=Gnosis safe, 3=POLY_1271 deposit wallet)
sig = int(sys.argv[1]) if len(sys.argv) > 1 else (0 if eoa.lower() == funder.lower() else 3)  # 3=deposit wallet (confirmed for 0x7D31)
c = ClobClient("https://clob.polymarket.com", 137, key=key, signature_type=sig, funder=funder)
c.set_api_creds(c.derive_api_key())
print(f"funder=...{funder[-6:]}  sig_type={sig}  client=v2")


def gj(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla"}), timeout=15))


def asks_of(ob):
    # v2 get_order_book returns a dict {"asks": [{"price","size"}, ...], "bids": [...]}
    asks = ob.get("asks") if isinstance(ob, dict) else getattr(ob, "asks", None)
    out = []
    for x in (asks or []):
        if isinstance(x, dict):
            p = x.get("price")
        elif isinstance(x, (list, tuple)):
            p = x[0]
        else:
            p = getattr(x, "price", None)
        if p is not None:
            out.append(float(p))
    return out


now = int(time.time()); base = now - (now % 300); pick = None
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
                    if not (ids and len(ids) == 2):
                        continue
                    pr = asks_of(c.get_order_book(str(ids[0])))
                    if pr and 0.5 <= min(pr) <= 0.95:
                        pick = (a, m.get("slug"), ids, min(pr)); break
                if pick:
                    break
        except Exception:
            pass
        if pick:
            break
    if pick:
        break

if not pick:
    print("no Up-favorite 0.5-0.95 right now — re-run in a moment."); raise SystemExit
a, slug, ids, ask = pick
ts = float(c.get_tick_size(str(ids[0])))
limit = round(round((ask + 3 * ts) / ts) * ts, 6)
shares = min(math.ceil(1.0 / limit), 3)   # integer shares -> maker amount (price*shares) stays on whole cents; >= $1 min
amt = round(shares * limit, 2)
print(f"REAL ORDER -> {a} {slug}  side=YES(Up)  ask={ask}  limit={limit}  size={shares} (~${amt})  (FOK)")
try:
    resp = c.create_and_post_order(OrderArgs(token_id=str(ids[0]), price=limit, size=shares, side=BUY),
                                   order_type="FOK")
    print("ORDER RESULT:", resp)
except Exception as e:
    print("ORDER ERR:", type(e).__name__, str(e)[:400])
