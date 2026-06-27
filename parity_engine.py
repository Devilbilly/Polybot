"""Deterministic engine parity: run a FIXED favorite market through the live build_portfolio +
live_tick path and print the settled result. Same code -> identical numbers on any box."""
import json
from polybot.live import build_portfolio, live_tick

cfg = json.load(open("polybot/portfolio.json"))
pf = build_portfolio(cfg, 1000.0); pf.new_market()
seq = [(0.70, 0.71, 250), (0.80, 0.81, 180), (0.88, 0.89, 100), (0.93, 0.94, 40), (0.96, 0.97, 8)]
for bid, ask, rem in seq:
    book = {}
    for lvl, dp in ((1, 0.0), (2, 0.01), (3, 0.02)):
        book[f"bid_p{lvl}"] = round(bid - dp, 4); book[f"bid_s{lvl}"] = 500.0
        book[f"ask_p{lvl}"] = round(ask + dp, 4); book[f"ask_s{lvl}"] = 500.0
    pf.process_tick(live_tick(rem, bid, ask, book))
res = pf.settle(True)   # YES settles
print("RESULT  final_cash=%.6f  total_pnl=%.6f  winner=%s" % (pf.total_cash(), res.total_pnl, res.winner))
