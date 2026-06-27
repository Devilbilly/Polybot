"""FIRST REAL ORDER: exactly ONE 1-share YES probe on a current Up-favorite (0.70-0.93).
Safety: aborts WITHOUT ordering if the USDC allowance isn't set. max_shares=1 hard cap. FOK
(fills 1 share immediately or cancels). Real money ~ the share price (<$1)."""
import json, time, urllib.request
from polybot.execution import ClobExecutor
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

def gj(u): return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla"}), timeout=15))

KEYF = "/home/palacedeforsaken/.config/polybot-clob.key"
FUNDF = "/home/palacedeforsaken/.config/polybot-clob.funder"
ex = ClobExecutor(KEYF, FUNDF, dry_run=False, max_shares=1.0)        # REAL

ba = ex.client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
allw = ba.get("allowance")
bal = ba.get("balance"); usdc = (float(bal) / 1e6) if bal not in (None, "") else None
print(f"balance=${usdc:.2f}  allowance={allw}")
if not allw or str(allw) in ("0", "None", ""):
    print("ALLOWANCE NOT SET -> aborting, NO order placed. (do the website enable-trading / wait for it to confirm)")
    raise SystemExit

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
                    if up and 0.70 <= up <= 0.93:
                        pick = (a, m.get("slug"), ids, up); break
                if pick: break
        except Exception:
            pass
        if pick: break
    if pick: break

if not pick:
    print("no clean Up-favorite (0.70-0.93) in current markets right now — re-run in a moment."); raise SystemExit

a, slug, ids, up = pick
print(f"FIRST REAL PROBE -> {a} {slug}  side=YES  Up ask={up}  size=1 share")
fill = ex.place_order(ids[0], "YES", 1.0, price_hint=up, no_token=ids[1])
print("RESULT:", fill)
print("(settles to $1 or $0 at the 5-min window close)")
