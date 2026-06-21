"""
Live trader. Runs the SAME core.Portfolio used in backtests — there is no separate live
strategy/execution code, so backtest results carry over by construction.

Pure helper live_tick() is unit-tested; the async loop is a thin wrapper.
Run:  python -m polybot.live   (paper trading against live markets)
"""
from __future__ import annotations
import json
from typing import Optional

from .core import Tick, Portfolio, ExecutionEngine, RiskGovernor
from .strategies import get_strategy
from .recorder import (predicted_slugs, parse_book, winner_from_recent,
                       WS_URI, GAMMA_API, CLOB_BOOK, WINDOW_SEC, _get_json)


def build_portfolio(cfg: dict, capital: float = 1000.0) -> Portfolio:
    strats = []
    for i, s in enumerate(cfg["strategies"]):
        st = get_strategy(s["name"], s.get("params", {}))
        st.name = s.get("id") or f"{s['name']}#{i}"
        strats.append(st)
    weights = [s["weight"] for s in cfg["strategies"]]
    engine = ExecutionEngine(**cfg.get("engine", {}))
    risk = RiskGovernor(capital, **cfg.get("risk", {}))
    return Portfolio(strats, weights, total_capital=capital, engine=engine, risk=risk)


def live_tick(rem: float, ws_bid: float, ws_ask: float, book: dict,
              spot: float = 0.0, strike: float = 0.0) -> Tick:
    """Assemble a core.Tick from a WS update + parsed L2 book (+ optional BTC spot/strike)."""
    tp = max(0.0, min(1.0, 1.0 - rem / WINDOW_SEC))
    return Tick(
        ts="live", time_progress=tp, ws_bid=ws_bid, ws_ask=ws_ask,
        bid_p=(book.get("bid_p1", 0.0), book.get("bid_p2", 0.0), book.get("bid_p3", 0.0)),
        bid_s=(book.get("bid_s1", 0.0), book.get("bid_s2", 0.0), book.get("bid_s3", 0.0)),
        ask_p=(book.get("ask_p1", 0.0), book.get("ask_p2", 0.0), book.get("ask_p3", 0.0)),
        ask_s=(book.get("ask_s1", 0.0), book.get("ask_s2", 0.0), book.get("ask_s3", 0.0)),
        spot=spot, strike=strike,
    )


async def run(config_path: str = "polybot/portfolio.json",
              db_path: str = "polymarket.db"):  # pragma: no cover (needs live network)
    import asyncio, ssl, time, logging
    import aiohttp, websockets
    from .database import Database
    log = logging.getLogger("polybot.live"); logging.basicConfig(level=logging.INFO, format="%(message)s")

    cfg = json.load(open(config_path))
    pf = build_portfolio(cfg)
    db = Database(db_path)
    session_id = f"live-{int(time.time())}"
    log.info("[LIVE] %d strategies, capital $%.0f, session %s", len(pf.strategies), pf.total_cash(), session_id)
    ssl_ctx = ssl.create_default_context(); ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE

    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla"},
                                     connector=aiohttp.TCPConnector(ssl=False)) as session:
        try:
            from .binance import fetch_spot
        except Exception:
            fetch_spot = None
        done = set()          # tokens already settled this session -> never re-trade/double-count
        while True:
            now = int(time.time())
            slug = None
            for cand in predicted_slugs(now):
                if int(cand.split("-")[-1]) > now:        # skip not-yet-STARTED future windows
                    continue
                d = await _get_json(session, GAMMA_API.format(cand))
                if d and len(d) > 0 and not d[0].get("closed"):
                    slug = cand; break
            if not slug:
                await asyncio.sleep(2); continue
            end_ts = int(slug.split("-")[-1]) + WINDOW_SEC
            ev = await _get_json(session, GAMMA_API.format(slug))
            token = None
            for m in (ev[0].get("markets", []) if ev else []):
                ids = m.get("clobTokenIds")
                if isinstance(ids, str):
                    try: ids = json.loads(ids)
                    except Exception: ids = []
                if ids:
                    token = ids[0]; break
            if not token or token in done:                # already traded/settled -> skip
                await asyncio.sleep(2); continue
            pf.new_market()
            recent_bids = []      # last valid YES bids -> median settlement proxy (matches backtest)
            strike = 0.0          # BTC spot at window open (set on first tick)
            try:
                async with websockets.connect(WS_URI, ssl=ssl_ctx, ping_interval=25) as ws:
                    await ws.send(json.dumps({"assets_ids": [token], "type": "market"}))
                    while end_ts - time.time() > 0:
                        try:
                            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                        except asyncio.TimeoutError:
                            continue
                        book = parse_book(await _get_json(session, CLOB_BOOK.format(token), timeout=3) or {})
                        spot = 0.0
                        if fetch_spot is not None:
                            try:
                                spot = fetch_spot()
                                if strike == 0.0:
                                    strike = spot            # window-open price = strike
                            except Exception:
                                spot = 0.0
                        rem = end_ts - time.time()           # re-sample AFTER blocking I/O (recv+REST)
                        if rem <= 0:
                            break
                        for it in (msg if isinstance(msg, list) else [msg]):
                            if it.get("event_type") not in ("price_change", "best_bid_ask"):
                                continue
                            wb = float(it.get("best_bid") or 0); wa = float(it.get("best_ask") or 0)
                            if wb <= 0 or wa <= 0:
                                continue
                            pf.process_tick(live_tick(rem, wb, wa, book, spot=spot, strike=strike)); recent_bids.append(wb)
            except Exception as e:
                log.info("[LIVE] reconnect: %s", e)
                await asyncio.sleep(2)
            finally:
                # ALWAYS settle a traded market (even on exception) so the open position is never
                # leaked into the next market's new_market() reset; mark done to avoid re-trading.
                if recent_bids:
                    res = pf.settle(winner_from_recent(recent_bids) == "YES")
                    db.log_round(session_id, res, market_id=token, ts=int(time.time()))
                    log.info("[LIVE] round %d winner=%s pnl=$%+.2f cash=$%.2f",
                             res.round_no, res.winner, res.total_pnl, res.total_cash)
                    done.add(token)


if __name__ == "__main__":  # pragma: no cover
    import asyncio
    asyncio.run(run())
