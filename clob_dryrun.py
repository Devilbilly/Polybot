"""DRY-RUN validation of ClobExecutor on a live market: builds + SIGNS 1-share YES and NO
orders (and the share-cap), POSTS NOTHING. Proves the real order path end-to-end, no money moved."""
import json, time, urllib.request
from polybot.execution import ClobExecutor

def gj(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla"})
    return json.load(urllib.request.urlopen(req, timeout=15))

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
                    if ids and len(ids) == 2:
                        mk = (m, ids); break
                if mk: break
        except Exception:
            pass
        if mk: break
    if mk: break
if not mk:
    print("no live up/down market right now (between windows) — try again in a moment"); raise SystemExit

m, ids = mk
print("market:", m.get("slug"))
ex = ClobExecutor("/home/palacedeforsaken/.config/polybot-clob.key",
                  "/home/palacedeforsaken/.config/polybot-clob.funder", dry_run=True, max_shares=1.0)
print(f"executor: sig_type={ex.signature_type} dry_run={ex.dry_run} max_shares={ex.max_shares}")
ya = ex._book_ask(ids[0])
print(f"YES(Up) book ask={ya}")
print("YES 1-share dry order ->", ex.place_order(ids[0], "YES", 1.0, price_hint=ya, no_token=ids[1]))
print("CAP test (ask 5 shares) ->", end=" ")
f = ex.place_order(ids[0], "YES", 5.0, price_hint=ya, no_token=ids[1]); print(f"clamped shares={f.shares} status={f.status}")
print("NO 1-share dry order  ->", ex.place_order(ids[0], "NO", 1.0, price_hint=None, no_token=ids[1]))
print("\n==> nothing was posted (dry_run). order construction + signing validated.")
