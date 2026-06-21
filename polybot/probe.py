"""
Live-API discovery probe. Run on a machine WITH Polymarket access:

    python -m polybot.probe

It (1) checks reachability of gamma-api / clob / binance, (2) tries the slug the bot currently
guesses, and (3) hunts for the REAL active BTC up/down 5-min market across several known
Polymarket endpoint shapes — printing the real slug + clobTokenIds so live.py's discovery can be
fixed precisely. Paste the output back. No side effects, ~10s.
"""
from __future__ import annotations
import json, ssl, time, urllib.request, urllib.error

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0"}


def _get(url, timeout=10):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout, context=CTX)
        raw = r.read()
        try:
            return r.status, json.loads(raw)
        except Exception:
            return r.status, raw[:200]
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return f"{type(e).__name__}", None


def _looks_btc_5m(text):
    t = str(text).lower()
    return ("btc" in t or "bitcoin" in t) and ("up" in t or "down" in t or "5m" in t or "5-min" in t or "5 min" in t)


def main():
    now = int(time.time()); base = now - (now % 300)
    print(f"== probe @ {now} (window base {base}) ==\n")

    print("[1] reachability")
    for name, url in [
        ("binance spot", "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"),
        ("gamma slug(now)", f"https://gamma-api.polymarket.com/events?slug=btc-updown-5m-{base}"),
        ("gamma slug(prev)", f"https://gamma-api.polymarket.com/events?slug=btc-updown-5m-{base-300}"),
        ("clob book(0)", "https://clob.polymarket.com/book?token_id=0"),
    ]:
        st, _ = _get(url)
        print(f"    {name:<18} -> {st}   {url}")

    print("\n[2] hunting for the live BTC up/down market across endpoint shapes")
    candidates = [
        "https://gamma-api.polymarket.com/events?closed=false&limit=200&tag_slug=crypto",
        "https://gamma-api.polymarket.com/events?closed=false&limit=200&order=startDate&ascending=false",
        "https://gamma-api.polymarket.com/markets?closed=false&limit=300",
        "https://clob.polymarket.com/sampling-markets",
        "https://clob.polymarket.com/markets",
        "https://gamma-api.polymarket.com/events?slug=bitcoin-up-or-down",
    ]
    found = []
    for url in candidates:
        st, data = _get(url, timeout=15)
        n = (len(data) if isinstance(data, (list, dict)) else "-")
        print(f"    HTTP {str(st):<6} items={n:<5} {url}")
        rows = []
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = data.get("data") or data.get("events") or data.get("markets") or []
        for e in (rows if isinstance(rows, list) else []):
            blob = json.dumps(e)[:400] if isinstance(e, dict) else str(e)
            if _looks_btc_5m(blob):
                slug = e.get("slug") or e.get("market_slug") or e.get("ticker") or "?"
                ttl = (e.get("title") or e.get("question") or "")[:60]
                toks = e.get("clobTokenIds") or e.get("tokens") or e.get("clob_token_ids")
                found.append((slug, ttl, toks))

    print("\n[3] BTC-up/down candidates found:")
    if not found:
        print("    NONE matched. Either no 5-min market is open right now, or the endpoints above")
        print("    are the wrong shape. Paste this whole output back so discovery can be fixed.")
    else:
        for slug, ttl, toks in found[:12]:
            print(f"    slug={slug!r}  title={ttl!r}")
            print(f"        clobTokenIds={toks}")
    print("\n(Compare the real slug above to the bot's guess: btc-updown-5m-<unix>. If they differ,")
    print(" live.py predicted_slugs() needs updating to the real format.)")


if __name__ == "__main__":
    main()
