"""Validate the PRODUCTION ClobExecutor (v2/sig3) with one real ~$1 order. Run on the box."""
import json, time, urllib.request
from polybot.execution import ClobExecutor

ex = ClobExecutor("/home/palacedeforsaken/.config/polybot-clob.key",
                  "/home/palacedeforsaken/.config/polybot-clob.funder", dry_run=False)
print("ClobExecutor sig_type=%s dry_run=%s min_usd=%s max_shares=%s" %
      (ex.signature_type, ex.dry_run, ex.min_usd, ex.max_shares))


def gj(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla"}), timeout=15))


now = int(time.time()); base = now - (now % 300); pick = None
for a in ("btc", "eth", "sol", "xrp"):
    for st in (base, base + 300, base - 300):
        try:
            ev = gj("https://gamma-api.polymarket.com/events?slug=%s-updown-5m-%s" % (a, st))
            rows = ev if isinstance(ev, list) else ev.get("data", [])
            for r in rows:
                for m in (r.get("markets", [r]) if isinstance(r, dict) else []):
                    ids = m.get("clobTokenIds")
                    if isinstance(ids, str):
                        ids = json.loads(ids)
                    if not (ids and len(ids) == 2):
                        continue
                    ask = ex._book_ask(str(ids[0]))
                    if ask and 0.5 <= ask <= 0.95:
                        pick = (a, m.get("slug"), ids, ask); break
                if pick:
                    break
        except Exception:
            pass
        if pick:
            break
    if pick:
        break

if not pick:
    print("no favorite 0.5-0.95 right now — retry"); raise SystemExit
a, slug, ids, ask = pick
print("market %s ask=%.3f -> place_order via production ClobExecutor" % (a, ask))
fill = ex.place_order(str(ids[0]), "YES", 1.0, price_hint=ask, no_token=str(ids[1]))
print("FILL:", fill)
