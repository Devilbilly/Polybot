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
                       ws_best_bid_ask, WS_URI, GAMMA_API, CLOB_BOOK, WINDOW_SEC, _get_json)


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
            wb, wa = ws_best_bid_ask(msg, token)    # best bid/ask for OUR token (price_changes schema)
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


# ----------------------------- market discovery -----------------------------
# Fallback listing endpoints, hunted when the guessed timestamp-slug 404s (e.g. the slug format
# changed since the data was collected). Field shapes below are Polymarket's common ones; the pure
# parsing helpers are unit-tested, so an API rename is a localised fix, not a silent hang.
DISCOVERY_LISTINGS = [
    "https://gamma-api.polymarket.com/events?closed=false&limit=400&order=startDate&ascending=false",
    "https://gamma-api.polymarket.com/events?closed=false&limit=400&tag_slug=crypto",
    "https://gamma-api.polymarket.com/markets?closed=false&limit=500&order=startDate&ascending=false",
]


def _listing_rows(data):
    """Normalise a gamma/clob listing response to a list of dict rows."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for k in ("data", "events", "markets"):
            if isinstance(data.get(k), list):
                return [r for r in data[k] if isinstance(r, dict)]
    return []


def _is_btc_5m(row):
    """Does a listing row look like a BTC up/down 5-minute market?"""
    blob = json.dumps(row).lower()
    btc = ("btc" in blob) or ("bitcoin" in blob)
    direction = ("up" in blob) or ("down" in blob)
    five = any(k in blob for k in ("5m", "5-min", "5 min", "updown", "up-or-down"))
    return btc and direction and five


def _token_from_row(row):
    """First CLOB token id from an EVENT row (nested markets) OR a flat MARKET row."""
    tok = extract_token([row])                      # event shape: row["markets"][].clobTokenIds
    if tok:
        return tok
    ids = row.get("clobTokenIds")                   # flat-market shape
    if isinstance(ids, str):
        try: ids = json.loads(ids)
        except Exception: ids = []
    return ids[0] if isinstance(ids, list) and ids else None


def market_end_ts(slug, row):
    """Window-END unix ts: prefer a unix timestamp embedded in the slug (historical format), else
    an ISO end-date field on the row. None if neither is present."""
    try:
        tail = str(slug).rsplit("-", 1)[-1]
        if tail.isdigit() and len(tail) >= 9:
            return int(tail) + WINDOW_SEC
    except Exception:
        pass
    if isinstance(row, dict):
        for k in ("endDate", "end_date_iso", "endDateIso", "end", "endTime"):
            v = row.get(k)
            if v:
                try:
                    from datetime import datetime
                    return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())
                except Exception:
                    pass
    return None


async def discover_market(session, done, log=None):  # pragma: no cover (network glue; helpers tested)
    """Find the current open BTC 5-min market -> (token, end_ts, label) or None. Tries the guessed
    timestamp-slug first (original fast path), then FALLS BACK to hunting listing endpoints so a
    changed slug format no longer silently blinds the bot. Only endpoint choice is network; all
    parsing is in the unit-tested helpers above."""
    import time
    now = int(time.time())
    for cand in predicted_slugs(now):                        # 1) fast path: the guessed slug
        if int(cand.split("-")[-1]) > now:
            continue
        ev = await _get_json(session, GAMMA_API.format(cand))
        if ev and len(ev) > 0 and not ev[0].get("closed"):
            tok = extract_token(ev)
            if tok and tok not in done:
                return tok, int(cand.split("-")[-1]) + WINDOW_SEC, cand
    for url in DISCOVERY_LISTINGS:                            # 2) fallback: hunt listings
        for row in _listing_rows(await _get_json(session, url)):
            if not _is_btc_5m(row):
                continue
            tok = _token_from_row(row); ets = market_end_ts(row.get("slug", ""), row)
            if tok and tok not in done and ets and ets > now:
                if log:
                    log.info("[LIVE] discovered via listing: slug=%s end_ts=%s", row.get("slug"), ets)
                return tok, ets, row.get("slug", "listing")
    return None


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
    log.info("[LIVE] (if gamma is not HTTP 200 it's likely geo/network-blocked or the API changed; "
             "run `python -m polybot.live --probe` to see what discovery finds)")


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
            found = await discover_market(session, done, log)
            if not found:
                attempts += 1
                if time.time() - last_hb > 30:            # heartbeat so it's visibly ALIVE
                    log.info("[LIVE] searching for an open BTC 5-min market… %d attempts, %.0fs "
                             "(guessed slug %s + %d listing fallbacks, none matched)",
                             attempts, time.time() - search_start,
                             predicted_slugs(int(time.time()))[0], len(DISCOVERY_LISTINGS))
                    last_hb = time.time()
                await asyncio.sleep(2); continue
            token, end_ts, label = found
            attempts = 0; search_start = time.time(); last_hb = 0.0
            pf.new_market()
            strike = window_open_strike(end_ts, fetch_klines) if fetch_klines is not None else 0.0
            log.info("[LIVE] >>> trading %s  token=%s…  %.0fs left  strike(open)=%.2f  cash=$%.2f",
                     label, str(token)[:12], end_ts - time.time(), strike, pf.total_cash())
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


async def probe():  # pragma: no cover (network)
    """`python -m polybot.live --probe`: one-shot diagnosis — connectivity report + a single
    discovery attempt — then exit. Replaces the old standalone probe script."""
    import ssl, logging, aiohttp
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("polybot.live")
    ssl_ctx = ssl.create_default_context(); ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla"},
                                     connector=aiohttp.TCPConnector(ssl=False)) as session:
        await _connectivity_report(session, log)
        log.info("[PROBE] attempting market discovery (guessed slug + %d listing fallbacks)…",
                 len(DISCOVERY_LISTINGS))
        found = await discover_market(session, set(), log)
        if found:
            tok, ets, label = found
            log.info("[PROBE] FOUND: slug/label=%s  token=%s…  end_ts=%s", label, str(tok)[:16], ets)
        else:
            log.info("[PROBE] no live BTC 5-min market found. Either none is open now, Polymarket is "
                     "geo/network-blocked from here, or the API shape changed (paste this output).")


if __name__ == "__main__":  # pragma: no cover
    import asyncio, sys
    asyncio.run(probe() if "--probe" in sys.argv else run())
