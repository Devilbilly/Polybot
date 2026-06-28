"""Can we backtest PER COIN? Need market_id -> coin. Check slug, spot(BTC), and ledger mapping."""
import sqlite3

md = sqlite3.connect("file:/home/palacedeforsaken/Polybot/market_data.db?mode=ro", uri=True)
total = md.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
slug_nonnull = md.execute("SELECT COUNT(*) FROM markets WHERE slug IS NOT NULL").fetchone()[0]
sample_slugs = md.execute("SELECT slug FROM markets WHERE slug IS NOT NULL LIMIT 5").fetchall()
btc_spot = md.execute("SELECT COUNT(DISTINCT market_id) FROM ticks WHERE spot>0").fetchone()[0]
md_mids = set(r[0] for r in md.execute("SELECT market_id FROM markets"))
print("market_data.db: %d markets | slug non-null: %d | sample slugs: %s" % (total, slug_nonnull, sample_slugs))
print("markets with spot>0 (=BTC identifiable): %d" % btc_spot)

lg = sqlite3.connect("file:/home/palacedeforsaken/Polybot/ledger.db?mode=ro", uri=True)
led_coin = {}
for (tid,) in lg.execute("SELECT DISTINCT trade_id FROM ledger WHERE mode='LIVE'"):
    p = (tid or "").split("-")
    if len(p) >= 2 and p[0] in ("btc", "eth", "sol", "xrp"):
        led_coin[p[1]] = p[0]
mapped = sum(1 for mid in md_mids if mid in led_coin)
print("ledger gives coin for %d markets; of market_data's %d markets, %d are mappable via ledger"
      % (len(led_coin), len(md_mids), mapped))
from collections import Counter
cc = Counter(led_coin[mid] for mid in md_mids if mid in led_coin)
print("per-coin mappable market counts:", dict(cc))
