"""Confirm the eth-specific chop: fetch 1m klines (ETH/BTC/SOL/XRP) over the trading window and
compute 30-min realized volatility (bps) + net move. If ETH spiked in 18:00-20:00 UTC while the
others stayed calm, that is the concrete 'something during that period'. Read-only."""
import json
import sqlite3
import time
import urllib.request

import numpy as np


def klines(sym, start_ms, end_ms):
    url = ("https://api.binance.com/api/v3/klines?symbol=%s&interval=1m&startTime=%d&endTime=%d&limit=1000"
           % (sym, start_ms, end_ms))
    data = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla"}), timeout=20))
    return [(int(k[0]), float(k[4])) for k in data]   # (openTime_ms, close)


c = sqlite3.connect("file:/home/palacedeforsaken/Polybot/ledger.db?mode=ro", uri=True)
ts = [r[0] for r in c.execute("SELECT ts FROM ledger WHERE event='FILL' AND mode='LIVE' AND coin='eth' AND fill_price>0")]
start = (int(min(ts)) // 1800) * 1800 - 1800
end = int(max(ts)) + 600
print("window UTC %s -> %s\n" % (time.strftime("%m-%d %H:%M", time.gmtime(start)),
                                 time.strftime("%m-%d %H:%M", time.gmtime(end))))

syms = [("ETHUSDT", "eth"), ("BTCUSDT", "btc"), ("SOLUSDT", "sol"), ("XRPUSDT", "xrp")]
buckets = {}
for sym, coin in syms:
    try:
        ks = klines(sym, start * 1000, end * 1000)
    except Exception as e:
        print("fetch err", sym, str(e)[:60]); continue
    by = {}
    for ot, cl in ks:
        b = (ot // 1000 // 1800) * 1800
        by.setdefault(b, []).append(cl)
    buckets[coin] = by

allb = sorted({b for coin in buckets for b in buckets[coin]})
print("30-min REALIZED VOL (std of 1m returns, bps) — higher = choppier:")
print("  %-7s %8s %8s %8s %8s" % ("UTC", "eth", "btc", "sol", "xrp"))
for b in allb:
    line = "  %-7s" % time.strftime("%H:%M", time.gmtime(b))
    for coin in ("eth", "btc", "sol", "xrp"):
        cls = buckets.get(coin, {}).get(b, [])
        if len(cls) > 3:
            rets = np.diff(np.log(cls))
            line += " %8.1f" % (np.std(rets) * 1e4)
        else:
            line += " %8s" % "-"
    print(line)
