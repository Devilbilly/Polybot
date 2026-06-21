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
from .recorder import (predicted_slugs, parse_book, winner_from_recent, extract_token,
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


def window_open_strike(end_ts: int, fetch_klines_fn, window: int = WINDOW_SEC) -> float:
    """True settlement reference for a BTC up/down window = BTC close at the window-OPEN time
    (end_ts - window), fetched from Binance history. The live loop otherwise anchors `strike` to
    the FIRST tick it sees — a few seconds late due to connect latency — which biases the spot
    model's probability for the whole window. Returns 0.0 on any failure so the caller can fall
    back to the first-tick spot. Pure w.r.t. fetch_klines_fn (injected) so it is unit-testable."""
    try:
        open_ms = (int(end_ts) - int(window)) * 1000
        kl = fetch_klines_fn(interval="1s", total=1, end_time=open_ms)
        return float(kl[-1][1]) if kl else 0.0
    except Exception:
        return 0.0


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


async def _live_msg_stream(ws, session, token, end_ts, fetch_spot, time_fn):  # pragma: no cover (network)
    """I/O generator: yield (msg, book, spot, rem) each WS update until the window closes. ALL
    network lives here so the trading consumer (_trade_one_market) stays pure and unit-testable.
    `rem` is sampled AFTER the blocking recv+REST so time_progress is never stale."""
    import asyncio
    while end_ts - time_fn() > 0:
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
        except asyncio.TimeoutError:
            continue
        book = parse_book(await _get_json(session, CLOB_BOOK.format(token), timeout=3) or {})
        spot = 0.0
        if fetch_spot is not None:
            try:
                spot = fetch_spot()
            except Exception:
                spot = 0.0
        yield (msg, book, spot, end_ts - time_fn())


async def _trade_one_market(pf, end_ts, strike, msg_stream, token, db, session_id, done,
                            time_fn, log=None):
    """Consume a market's (msg, book, spot, rem) stream — same filtering + tick processing as the
    live loop — and ALWAYS settle in `finally` so a mid-market disconnect can never leak the open
    position into the next new_market(). Settlement + DB record + done-mark happen in `finally`
    so they occur even when the stream raises (the exception then propagates for the reconnect
    backoff). Returns the RoundResult or None. Extracted from run() to make this invariant
    unit-testable (run() injects the live _live_msg_stream)."""
    recent_bids = []
    res = None
    ticks = 0; last_hb = 0.0
    try:
        async for (msg, book, spot, rem) in msg_stream:
            if strike == 0.0 and spot > 0.0:
                strike = spot                       # fallback: first valid spot if no window-open strike
            if rem <= 0:
                break
            for it in (msg if isinstance(msg, list) else [msg]):
                if it.get("event_type") not in ("price_change", "best_bid_ask"):
                    continue
                wb = float(it.get("best_bid") or 0); wa = float(it.get("best_ask") or 0)
                if wb <= 0 or wa <= 0:
                    continue
                pf.process_tick(live_tick(rem, wb, wa, book, spot=spot, strike=strike))
                recent_bids.append(wb); ticks += 1
                if log and ticks == 1:
                    log.info("[LIVE]     first tick: bid=%.3f ask=%.3f rem=%.0fs", wb, wa, rem)
                elif log and (now := time_fn()) - last_hb > 30:   # in-window heartbeat every ~30s
                    log.info("[LIVE]     trading… %d ticks, last bid=%.3f, %.0fs left, cash=$%.2f",
                             ticks, wb, rem, pf.total_cash())
                    last_hb = now
    finally:
        if recent_bids:
            res = pf.settle(winner_from_recent(recent_bids) == "YES")
            db.log_round(session_id, res, market_id=token, ts=int(time_fn()))
            done.add(token)
            if log:
                log.info("[LIVE] <<< settled round %d  winner=%s  pnl=$%+.2f  cash=$%.2f  (%d ticks)",
                         res.round_no, res.winner, res.total_pnl, res.total_cash, ticks)
        elif log:
            log.info("[LIVE] <<< window ended with no valid ticks (no trade)")
    return res


async def _connectivity_report(session, log):  # pragma: no cover (network)
    """One-time startup probe so the (overnight) log shows immediately whether each endpoint is
    reachable — turns a silent 'is it dead?' into a diagnosable 'gamma returned 404'."""
    import time, aiohttp
    now = int(time.time()); base = now - (now % WINDOW_SEC)
    sample_slug = f"btc-updown-5m-{base}"
    for name, url in [("gamma(events?slug)", GAMMA_API.format(sample_slug)),
                      ("clob(book)", CLOB_BOOK.format("0"))]:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                body = await r.text()
                log.info("[LIVE] connectivity %-20s HTTP %s (%d bytes)  %s",
                         name, r.status, len(body), url)
        except Exception as e:
            log.info("[LIVE] connectivity %-20s FAILED: %s: %s", name, type(e).__name__, e)
    try:
        from .binance import fetch_spot
        log.info("[LIVE] connectivity %-20s OK  BTC=$%.2f", "binance(spot)", fetch_spot())
    except Exception as e:
        log.info("[LIVE] connectivity %-20s FAILED: %s", "binance(spot)", type(e).__name__)
    log.info("[LIVE] (if gamma is not HTTP 200, the slug/endpoint changed since data collection — "
             "run `python -m polybot.probe` to discover the current market)")


async def run(config_path: str = "polybot/portfolio.json",
              db_path: str = "polymarket.db"):  # pragma: no cover (needs live network)
    import asyncio, ssl, time, logging
    import aiohttp, websockets
    from .database import Database
    log = logging.getLogger("polybot.live")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    cfg = json.load(open(config_path))
    pf = build_portfolio(cfg)
    db = Database(db_path)
    session_id = f"live-{int(time.time())}"
    log.info("[LIVE] %d strategies, capital $%.0f, session %s", len(pf.strategies), pf.total_cash(), session_id)
    ssl_ctx = ssl.create_default_context(); ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE

    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla"},
                                     connector=aiohttp.TCPConnector(ssl=False)) as session:
        await _connectivity_report(session, log)
        try:
            from .binance import fetch_spot, fetch_klines
        except Exception:
            fetch_spot = fetch_klines = None
        done = set()          # tokens already settled this session -> never re-trade/double-count
        attempts = 0; search_start = time.time(); last_hb = 0.0
        while True:
            now = int(time.time())
            slug = None; last_status = "no-response"
            for cand in predicted_slugs(now):
                if int(cand.split("-")[-1]) > now:        # skip not-yet-STARTED future windows
                    continue
                d = await _get_json(session, GAMMA_API.format(cand))
                last_status = "open" if (d and len(d) > 0 and not d[0].get("closed")) else (
                    "closed/empty" if d else "404/err")
                if d and len(d) > 0 and not d[0].get("closed"):
                    slug = cand; break
            if not slug:
                attempts += 1
                if time.time() - last_hb > 30:            # heartbeat so it's visibly ALIVE
                    log.info("[LIVE] searching for an open BTC 5-min market… %d attempts, %.0fs "
                             "(last gamma: %s, tried %s)", attempts, time.time() - search_start,
                             last_status, predicted_slugs(now)[0])
                    last_hb = time.time()
                await asyncio.sleep(2); continue
            attempts = 0; search_start = time.time(); last_hb = 0.0
            end_ts = int(slug.split("-")[-1]) + WINDOW_SEC
            ev = await _get_json(session, GAMMA_API.format(slug))
            token = extract_token(ev)
            if not token or token in done:                # already traded/settled -> skip
                log.info("[LIVE] %s: no tradable token (or already traded) — skipping", slug)
                await asyncio.sleep(2); continue
            pf.new_market()
            strike = window_open_strike(end_ts, fetch_klines) if fetch_klines is not None else 0.0
            log.info("[LIVE] >>> trading %s  token=%s…  %.0fs left  strike(open)=%.2f  cash=$%.2f",
                     slug, str(token)[:12], end_ts - time.time(), strike, pf.total_cash())
            try:
                async with websockets.connect(WS_URI, ssl=ssl_ctx, ping_interval=25) as ws:
                    await ws.send(json.dumps({"assets_ids": [token], "type": "market"}))
                    log.info("[LIVE]     connected + subscribed; streaming…")
                    await _trade_one_market(
                        pf, end_ts, strike,
                        _live_msg_stream(ws, session, token, end_ts, fetch_spot, time.time),
                        token, db, session_id, done, time.time, log)
            except Exception as e:
                log.info("[LIVE] reconnect (%s): %s", type(e).__name__, e)
                await asyncio.sleep(2)


if __name__ == "__main__":  # pragma: no cover
    import asyncio
    asyncio.run(run())
