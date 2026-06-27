"""Try ONE real 1-share order with the corrected funder (0x7D31). No pre-gate — let the order
result be the test. max_shares=1 hard cap, FOK (fills instantly or cancels). Real ~<$1."""
import json, time, urllib.request
from polybot.execution import ClobExecutor

def gj(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla"}), timeout=15))

ex = ClobExecutor("/home/palacedeforsaken/.config/polybot-clob.key",
                  "/home/palacedeforsaken/.config/polybot-clob.funder", dry_run=False, max_shares=1.0)
print(f"funder=...{ex.funder[-6:]}  sig_type={ex.signature_type}  dry_run={ex.dry_run}")

now = int(time.time()); base = now - (now % 300); pick = None
for a in ("btc", "eth", "sol", "xrp"):
    for st in (base, base + 300, base - 300):
        try:
            ev = gj(f"https://gamma-api.polymarket.com/events?slug={a}-updown-5m-{st}")
            rows = ev if isinstance(ev, list) else ev.get("data", [])
            for r in rows:
                for m in (r.get("markets", [r]) if isinstance(r, dict) else []):
                    ids = m.get("clobTokenIds")
                    if isinstance(ids, str): ids = json.loads(ids)
                    if not (ids and len(ids) == 2): continue
                    up = ex._book_ask(ids[0])
                    if up and 0.05 <= up <= 0.97:
                        pick = (a, m.get("slug"), ids, up); break
                if pick: break
        except Exception:
            pass
        if pick: break
    if pick: break

if not pick:
    print("no Up-favorite (0.60-0.95) right now — re-run in a moment."); raise SystemExit

a, slug, ids, up = pick
print(f"TRYING REAL ORDER -> {a} {slug}  side=YES(Up)  ask={up}  size=1 share")
fill = ex.place_order(ids[0], "YES", 1.0, price_hint=up, no_token=ids[1])
print("ORDER RESULT:", fill)
