"""Test the stale-book gate: a NO order whose paper price_hint disagrees with the real Down ask
must be REJECTED:stale; one that agrees must build (DRYRUN). No orders posted."""
import json, time, urllib.request
from polybot.execution import ClobExecutor

def gj(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla"}), timeout=15))

mk = None; now = int(time.time()); base = now - (now % 300)
for a in ("btc", "eth", "sol", "xrp"):
    for st in (base, base + 300, base - 300):
        try:
            ev = gj(f"https://gamma-api.polymarket.com/events?slug={a}-updown-5m-{st}")
            rows = ev if isinstance(ev, list) else ev.get("data", [])
            for r in rows:
                for m in (r.get("markets", [r]) if isinstance(r, dict) else []):
                    ids = m.get("clobTokenIds")
                    if isinstance(ids, str): ids = json.loads(ids)
                    if ids and len(ids) == 2: mk = ids; break
                if mk: break
        except Exception: pass
        if mk: break
    if mk: break
if not mk:
    print("no live market right now"); raise SystemExit

ex = ClobExecutor("/home/palacedeforsaken/.config/polybot-clob.key",
                  "/home/palacedeforsaken/.config/polybot-clob.funder", dry_run=True, desync_tol=0.05)
real_ask = ex._book_ask(mk[1])
print(f"real Down ask = {real_ask}  (desync_tol={ex.desync_tol})")
print("NO, paper hint AGREES (=real ask)  ->", ex.place_order(mk[0], "NO", 1.0, price_hint=real_ask, no_token=mk[1]).status)
print("NO, paper hint STALE  (0.95)       ->", ex.place_order(mk[0], "NO", 1.0, price_hint=0.95, no_token=mk[1]).status)
print("(first should be DRYRUN, second REJECTED:stale — gate works)")
