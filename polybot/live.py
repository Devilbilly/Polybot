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
                       extract_token_pair, ws_best_bid_ask, best_bid_ask, build_tick_row, carry_book,
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


async def _live_msg_stream(ws, session, token, end_ts, fetch_spot, time_fn, book_ttl=1.0):  # pragma: no cover (network)
    """I/O generator: yield (msg, book, spot, rem) at most once per `book_ttl` seconds until the
    window closes. ALL network lives here so the trading consumer (_trade_one_market) stays pure
    and unit-testable. `rem` is sampled AFTER the blocking recv+REST so time_progress is never stale.

    Mirrors the ORIGINAL collector (l.py `update_l2_cache`, `L2_REFRESH_INTERVAL=1.0`): the REST
    /book is polled on a fixed ~1s cadence DECOUPLED from the WS tick rate, last good book carried
    forward. The redesign had regressed this to a per-WS-tick fetch which (a) rate-limited the CLOB
    endpoint across 4 assets -> ~50% empty books -> stale-0.50 fallback, and (b) wrote ~60 duplicate
    rows/sec/asset (DB bloat). Throttling to one emit per `book_ttl` restores BOTH: clean prices at
    ~1 row/sec/asset — the sampling density the validated historical CSVs were recorded at."""
    import asyncio
    last_book = {}
    last_emit = -1e9
    while end_ts - time_fn() > 0:
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
        except asyncio.TimeoutError:
            continue
        now = time_fn()
        if now - last_emit < book_ttl and last_book:    # throttle to ~1 emit/sec; never before first good book
            continue
        fresh = parse_book(await _get_json(session, CLOB_BOOK.format(token), timeout=3) or {})
        last_book = carry_book(fresh, last_book)
        if not last_book:        # no real REST book yet -> skip (don't emit a stale-WS-0.50 tick); keep trying
            continue
        last_emit = now
        spot = 0.0
        if fetch_spot is not None:
            try:
                spot = fetch_spot()
            except Exception:
                spot = 0.0
        yield (msg, last_book, spot, end_ts - time_fn())


async def _trade_one_market(pf, end_ts, strike, msg_stream, token, db, session_id, done,
                            time_fn, log=None, tag="LIVE", ledger=None, executor=None, no_token=None):
    """Consume a market's (msg, book, spot, rem) stream — same filtering + tick processing as the
    live loop — and ALWAYS settle in `finally` so a mid-market disconnect can never leak the open
    position into the next new_market(). Settlement + DB record + done-mark happen in `finally`
    so they occur even when the stream raises (the exception then propagates for the reconnect
    backoff). Returns the RoundResult or None. Extracted from run() to make this invariant
    unit-testable (run() injects the live _live_msg_stream).

    `ledger` (optional): a polybot.ledger.Ledger. If given, full-fidelity obs is captured by
    DIFFING positions after each tick (fill side/shares/price + book snapshot) and at settle.
    Pure observation — wrapped in try/except so it can NEVER block or break trading."""
    recent_bids = []
    res = None
    ticks = 0; last_hb = 0.0
    coin = (session_id or "").rsplit("-", 1)[-1]
    prev = [(a.n_entries, a.cash, a.inv_yes, a.inv_no) for a in pf.accounts] if ledger else None
    try:
        async for (msg, book, spot, rem) in msg_stream:
            if strike == 0.0 and spot > 0.0:
                strike = spot                       # fallback: first valid spot if no window-open strike
            if rem <= 0:
                break
            wb, wa = best_bid_ask(book, msg, token)  # REST book first (WS best_bid goes stale on long conns)
            if wb <= 0 or wa <= 0:
                continue
            tk = live_tick(rem, wb, wa, book, spot=spot, strike=strike)
            pf.process_tick(tk)
            if ledger is not None and prev is not None:    # full-fidelity FILL capture (fail-safe)
                try:
                    for i, (strat, acc) in enumerate(zip(pf.strategies, pf.accounts)):
                        if acc.n_entries > prev[i][0]:                       # a new entry this tick
                            if acc.inv_yes > prev[i][2] + 1e-9:
                                side, shares = "YES", acc.inv_yes - prev[i][2]
                            else:
                                side, shares = "NO", acc.inv_no - prev[i][3]
                            real_fill, fmode = None, "PAPER"
                            if executor is not None:                         # SHADOW/LIVE: mirror with a 1-share probe
                                quote = tk.ap1 if side == "YES" else (1.0 - tk.bp1)
                                try:
                                    real_fill = executor.place_order(token, side, 1.0, price_hint=quote,
                                                                     no_token=no_token)
                                except Exception:
                                    real_fill = None                          # probe failure never blocks trading
                                fmode = "SHADOW" if executor.dry_run else "LIVE"
                            ledger.record_live_fill(coin=coin, market_id=token, round_no=pf.round_no,
                                                    strategy=strat.name, side=side, shares=shares,
                                                    cost=prev[i][1] - acc.cash, tick=tk,
                                                    real_fill=real_fill, mode=fmode)
                        prev[i] = (acc.n_entries, acc.cash, acc.inv_yes, acc.inv_no)
                except Exception:
                    pass                                   # obs must never break trading
            try:
                db.insert_tick(token, ticks, build_tick_row(rem, wb, wa, book, spot=spot, strike=strike))
            except Exception:
                pass                                       # recording is best-effort; never block trading
            recent_bids.append(wb); ticks += 1
            if log and ticks == 1:
                log.info("[%-4s]     first tick: bid=%.3f ask=%.3f rem=%.0fs", tag, wb, wa, rem)
            elif log and (now := time_fn()) - last_hb > 30:   # in-window heartbeat every ~30s
                log.info("[%-4s]     trading… %d ticks, last bid=%.3f, %.0fs left, cash=$%.2f",
                         tag, ticks, wb, rem, pf.total_cash())
                last_hb = now
    finally:
        if recent_bids:
            win = winner_from_recent(recent_bids)
            res = pf.settle(win == "YES")
            db.log_round(session_id, res, market_id=token, ts=int(time_fn()))
            if ledger is not None:                          # full-fidelity SETTLE capture (fail-safe)
                try:
                    for strat in pf.strategies:
                        ps = res.per_strategy.get(strat.name)
                        if ps:
                            ledger.record_live_settle(coin=coin, market_id=token, round_no=res.round_no,
                                                      strategy=strat.name, winner=win, pnl=ps["pnl"])
                except Exception:
                    pass
            try:
                db.upsert_market(token, token_id=token, end_ts=end_ts, winner=win, n_ticks=ticks)
            except Exception:
                pass                                       # mark the recorded market replayable; best-effort
            done.add(token)
            if log:
                log.info("[%-4s] <<< settled  winner=%-3s  pnl=$%+.2f  cash=$%.2f  (%d ticks)",
                         tag, res.winner, res.total_pnl, res.total_cash, ticks)
        elif log:
            log.info("[%-4s] <<< window ended with no valid ticks (no trade)", tag)
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


def _token_pair_from_row(row):
    """(yes_token, no_token) from an EVENT row (nested markets) OR a flat MARKET row."""
    y, n = extract_token_pair([row])
    if y:
        return (y, n)
    ids = row.get("clobTokenIds")
    if isinstance(ids, str):
        try: ids = json.loads(ids)
        except Exception: ids = []
    if isinstance(ids, list) and ids:
        return (ids[0], ids[1] if len(ids) > 1 else None)
    return (None, None)


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
            # only the CURRENTLY-OPEN window: ends in the future but within one window length
            # (guards against grabbing a market hours/days out, which has rem >> WINDOW_SEC).
            if tok and tok not in done and ets and now < ets <= now + WINDOW_SEC:
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
                # ping_timeout=None: Polymarket's WS doesn't reliably PONG client keepalive pings,
                # so the default 20s timeout was killing the link mid-window (premature settle +
                # idle gap). Data flow + the 60s recv timeout still detect a genuinely dead socket.
                async with websockets.connect(WS_URI, ssl=ssl_ctx, ping_interval=20, ping_timeout=None) as ws:
                    await ws.send(json.dumps({"assets_ids": [token], "type": "market"}))
                    log.info("[%-4s]     connected + subscribed; streaming…", label.split("-")[0])
                    await _trade_one_market(
                        pf, end_ts, strike,
                        _live_msg_stream(ws, session, token, end_ts, fetch_spot, time.time),
                        token, db, session_id, done, time.time, log, tag=label.split("-")[0])
            except Exception as e:
                log.info("[LIVE] reconnect (%s): %s", type(e).__name__, e)
                await asyncio.sleep(2)


# ----------------------------- multi-market (parallel) -----------------------------
# The favorite-longshot edge isn't BTC-specific — it should appear in every crypto up/down market.
# Trading several in parallel deploys idle capital, multiplies opportunities, and diversifies.
ASSET_SLUGS = ("btc", "eth", "sol", "xrp")          # guessed 5-min slug prefixes
_UPDOWN_ASSETS = ("btc", "bitcoin", "eth", "ethereum", "sol", "solana", "xrp", "ripple",
                  "doge", "dogecoin", "ltc", "litecoin", "bnb", "ada", "cardano")


def _is_updown_market(row):
    """Any crypto up/down market (any asset / short timeframe). Loose on purpose — discovery is
    verified by --probe-all before trading."""
    blob = json.dumps(row).lower()
    asset = any(a in blob for a in _UPDOWN_ASSETS)
    updown = ("updown" in blob) or ("up-or-down" in blob) or ("up or down" in blob) \
        or (("up" in blob) and ("down" in blob))
    return asset and updown


async def discover_all_markets(session, done, log=None, assets=ASSET_SLUGS):  # pragma: no cover (network)
    """All currently-OPEN crypto up/down 5-min markets -> [(token, end_ts, label)]. Per-asset
    guessed slug (fast) + the listing fallback. Only windows ending within one window length."""
    import time
    now = int(time.time()); base = now - (now % WINDOW_SEC)
    out = []; seen = set()
    for a in assets:                                     # fast path: guessed per-asset 5-min slug
        for ts in (base, base - WINDOW_SEC):
            if ts > now:
                continue
            ev = await _get_json(session, GAMMA_API.format(f"{a}-updown-5m-{ts}"))
            if ev and len(ev) > 0 and not ev[0].get("closed"):
                tok, no_tok = extract_token_pair(ev); ets = ts + WINDOW_SEC
                if tok and tok not in done and tok not in seen and now < ets <= now + WINDOW_SEC:
                    seen.add(tok); out.append((tok, no_tok, ets, f"{a}-updown-5m-{ts}"))
    for url in DISCOVERY_LISTINGS:                       # fallback: catch other assets/slug formats
        for row in _listing_rows(await _get_json(session, url)):
            if not _is_updown_market(row):
                continue
            tok, no_tok = _token_pair_from_row(row); ets = market_end_ts(row.get("slug", ""), row)
            if tok and tok not in done and tok not in seen and ets and now < ets <= now + WINDOW_SEC:
                seen.add(tok); out.append((tok, no_tok, ets, row.get("slug", "listing")))
    return out


async def _connect_and_trade(pf, token, end_ts, strike, session, db, session_id, done,
                             fetch_spot_fn, ssl_ctx, log, label="", ledger=None, executor=None,
                             no_token=None):  # pragma: no cover (network)
    """One market's WS connect + trade+record, settle-in-finally. Used per-market by run_multi
    (and mirrors what run() does inline). Exceptions are contained so one market can't kill others."""
    import time, websockets
    tag = (label.split("-")[0] or token[:6])
    try:
        async with websockets.connect(WS_URI, ssl=ssl_ctx, ping_interval=20, ping_timeout=None) as ws:
            await ws.send(json.dumps({"assets_ids": [token], "type": "market"}))
            await _trade_one_market(
                pf, end_ts, strike,
                _live_msg_stream(ws, session, token, end_ts, fetch_spot_fn, time.time),
                token, db, session_id, done, time.time, log, tag=tag, ledger=ledger, executor=executor,
                no_token=no_token)
    except Exception as e:
        if log:
            log.info("[%-4s] reconnect: %s", tag, type(e).__name__)


async def run_multi(config_path: str = "polybot/portfolio.json", db_path: str = "polymarket.db",
                    capital_per_market: float = 1000.0,
                    assets=ASSET_SLUGS, ledger_path: str = "ledger.db",
                    real_mode: str = "paper"):  # pragma: no cover (needs live network)
    """Trade + record MANY crypto up/down markets in parallel (one Portfolio each). Defaults to the
    FAVORITES-ONLY config: live data (iter: 69 windows/asset) showed the btc_spot_divergence sleeve
    is a net drag (BTC -7.8% trading 8x more often at 52% win, vs favorites-only ETH/SOL/XRP all
    green) — the spot edge validated only synthetically does NOT hold live. BTC spot is still
    FETCHED + RECORDED (for future spot research/replay) but not traded on. Pass
    config_path='polybot/portfolio_live.json' to re-enable the spot sleeve. capital_per_market is
    the notional PER market (aggregate exposure = capital_per_market x concurrently-open markets)."""
    import asyncio, ssl, time, logging
    import aiohttp
    from .database import Database
    log = logging.getLogger("polybot.live")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg = json.load(open(config_path))
    db = Database(db_path)
    try:
        from .ledger import Ledger
        ledger = Ledger(ledger_path)
        log.info("[MULTI] obs ledger -> %s (full-fidelity paper recording)", ledger_path)
    except Exception as e:
        ledger = None
        log.info("[MULTI] ledger disabled (%s); trading unaffected", e)
    try:
        from .execution import make_executor
        executor = make_executor(real_mode)
        log.info("[MULTI] real_mode=%s executor=%s (paper=no real orders; shadow=mock 1-share probes)",
                 real_mode, type(executor).__name__ if executor else None)
    except Exception as e:
        executor = None
        log.info("[MULTI] executor disabled (%s); paper trading unaffected", e)
    session_id = f"multi-{int(time.time())}"
    ssl_ctx = ssl.create_default_context(); ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE
    log.info("[MULTI] config=%s  $%.0f/market  assets=%s  session=%s",
             config_path, capital_per_market, ",".join(assets), session_id)
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla"},
                                     connector=aiohttp.TCPConnector(ssl=False)) as session:
        await _connectivity_report(session, log)
        try:
            from .binance import fetch_spot, fetch_klines
        except Exception:
            fetch_spot = fetch_klines = None
        done = set(); portfolios = {}; tasks = {}; last_hb = 0.0; last_total = 0.0; last_bak = time.time()
        while True:
            try:
                db.conn.commit()                                # flush recorded ticks every loop (~3s) -> durable
            except Exception:
                pass
            if time.time() - last_bak > 1800:                   # rolling 30-min DB backup (data safety)
                try:
                    db.backup(db_path + ".bak")
                    log.info("[MULTI] db backed up -> %s.bak", db_path)
                except Exception as e:
                    log.info("[MULTI] backup failed: %s", e)
                last_bak = time.time()
            for a in [a for a, k in tasks.items() if k.done()]:     # reap finished windows
                tasks.pop(a, None)
            for token, no_token, end_ts, label in await discover_all_markets(session, done, log, assets):
                asset = label.split("-")[0]
                if asset in tasks or token in done:                 # one live window per asset at a time
                    continue
                pf = portfolios.get(asset)
                if pf is None:                                      # persistent per-asset Portfolio
                    pf = build_portfolio(cfg, capital_per_market); portfolios[asset] = pf
                pf.new_market()                                     # COMPOUNDS across this asset's windows
                is_btc = asset in ("btc", "bitcoin")
                strike = window_open_strike(end_ts, fetch_klines) if (is_btc and fetch_klines) else 0.0
                spot_fn = fetch_spot if is_btc else None            # spot model only valid for BTC
                log.info("[%-4s] >>> %s  %4.0fs left  strike=%.2f  cash=$%.2f",
                         asset, label, end_ts - time.time(), strike, pf.total_cash())
                tasks[asset] = asyncio.create_task(
                    _connect_and_trade(pf, token, end_ts, strike, session, db, f"{session_id}-{asset}", done,
                                       spot_fn, ssl_ctx, log, label, ledger=ledger, executor=executor,
                                       no_token=no_token))
            if portfolios and time.time() - last_total > 60:        # running aggregate across assets
                tot = sum(p.total_cash() for p in portfolios.values())
                per = "  ".join(f"{a}=${p.total_cash():.2f}" for a, p in sorted(portfolios.items()))
                log.info("[MULTI] === TOTAL $%.2f across %d assets  (%s) ===", tot, len(portfolios), per)
                last_total = time.time()
            if not tasks and time.time() - last_hb > 30:
                log.info("[MULTI] no open markets right now; searching… (assets=%s)", ",".join(assets))
                last_hb = time.time()
            await asyncio.sleep(3)


async def _record_one_market(token, end_ts, strike, session, db, done, fetch_spot_fn,
                             ssl_ctx, log, tag=""):  # pragma: no cover (network)
    """Record ONE market's tick stream to the DB — NO trading. The monitor's per-market worker,
    deliberately separate from the trader's _trade_one_market so data collection is independent."""
    import time, websockets
    recent = []; seq = 0
    try:
        async with websockets.connect(WS_URI, ssl=ssl_ctx, ping_interval=20, ping_timeout=None) as ws:
            await ws.send(json.dumps({"assets_ids": [token], "type": "market"}))
            async for (msg, book, spot, rem) in _live_msg_stream(ws, session, token, end_ts,
                                                                 fetch_spot_fn, time.time):
                if rem <= 0:
                    break
                if strike == 0.0 and spot > 0.0:
                    strike = spot
                wb, wa = best_bid_ask(book, msg, token)
                if wb <= 0 or wa <= 0:
                    continue
                try:
                    db.insert_tick(token, seq, build_tick_row(rem, wb, wa, book, spot=spot, strike=strike))
                    seq += 1
                except Exception:
                    pass
                recent.append(wb)
    except Exception as e:
        if log:
            log.info("[%-4s rec] reconnect: %s", tag, type(e).__name__)
    finally:
        if seq > 0:
            win = winner_from_recent(recent)
            try:
                db.upsert_market(token, token_id=token, end_ts=end_ts, winner=win, n_ticks=seq)
            except Exception:
                pass
            done.add(token)
            if log:
                log.info("[%-4s] recorded %d ticks  winner=%s", tag, seq, win)


async def record_multi(db_path: str = "market_data.db",
                       assets=ASSET_SLUGS):  # pragma: no cover (needs live network)
    """MONITOR: continuously record ALL crypto up/down markets to the DB — independent of any
    trader. Run as its own process (`--record`) so data collection survives trader restarts/tuning.
    Writes to a SEPARATE db (market_data.db) so it never contends with the trader's polymarket.db."""
    import asyncio, ssl, time, logging
    import aiohttp
    from .database import Database
    log = logging.getLogger("polybot.rec")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    db = Database(db_path)
    ssl_ctx = ssl.create_default_context(); ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE
    log.info("[REC] monitor recording %s -> %s", ",".join(assets), db_path)
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla"},
                                     connector=aiohttp.TCPConnector(ssl=False)) as session:
        await _connectivity_report(session, log)
        try:
            from .binance import fetch_spot, fetch_klines
        except Exception:
            fetch_spot = fetch_klines = None
        done = set(); tasks = {}; last_bak = time.time(); last_hb = 0.0
        while True:
            try:
                db.conn.commit()                                # flush recorded ticks every loop (~3s) -> durable
            except Exception:
                pass
            if time.time() - last_bak > 1800:                   # rolling 30-min backup (data safety)
                try:
                    db.backup(db_path + ".bak"); log.info("[REC] db backed up -> %s.bak", db_path)
                except Exception:
                    pass
                last_bak = time.time()
            for a in [a for a, k in tasks.items() if k.done()]:
                tasks.pop(a, None)
            for token, no_token, end_ts, label in await discover_all_markets(session, done, log, assets):
                asset = label.split("-")[0]
                if asset in tasks or token in done:
                    continue
                is_btc = asset in ("btc", "bitcoin")
                strike = window_open_strike(end_ts, fetch_klines) if (is_btc and fetch_klines) else 0.0
                spot_fn = fetch_spot if is_btc else None
                log.info("[%-4s] >>> recording %s  %4.0fs left", asset, label, end_ts - time.time())
                tasks[asset] = asyncio.create_task(
                    _record_one_market(token, end_ts, strike, session, db, done, spot_fn, ssl_ctx, log, asset))
            if not tasks and time.time() - last_hb > 30:
                log.info("[REC] no open markets; searching… (assets=%s)", ",".join(assets))
                last_hb = time.time()
            await asyncio.sleep(3)


async def probe_all(assets=ASSET_SLUGS):  # pragma: no cover (network)
    """`--probe-all`: list every currently-open up/down market discovery finds, across assets —
    run this FIRST to verify what's tradeable before starting run_multi."""
    import ssl, logging, time, aiohttp
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("polybot.live")
    ssl_ctx = ssl.create_default_context(); ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla"},
                                     connector=aiohttp.TCPConnector(ssl=False)) as session:
        await _connectivity_report(session, log)
        mkts = await discover_all_markets(session, set(), log, assets)
        log.info("[PROBE-ALL] %d open up/down markets found (assets tried: %s):", len(mkts), ",".join(assets))
        for tok, no_tok, ets, label in mkts:
            log.info("   %-30s ends_in=%4.0fs  token=%s…", label, ets - time.time(), str(tok)[:14])
        if not mkts:
            log.info("   none found — only BTC may be live now, or other assets use a different slug.")


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
    if "--probe-all" in sys.argv:
        asyncio.run(probe_all())          # list all open up/down markets (verify before --multi)
    elif "--probe" in sys.argv:
        asyncio.run(probe())              # single-market connectivity + discovery check
    elif "--record" in sys.argv:
        asyncio.run(record_multi())       # MONITOR: record all markets, no trading (separate process)
    elif "--multi" in sys.argv:
        # real_mode: paper (default, no real orders) | shadow (mock 1-share probes, records
        # real-vs-paper, ZERO real money) | live (real CLOB probes — needs creds, not yet built)
        mode = ("shadow" if "--shadow" in sys.argv else "dryrun" if "--dryrun" in sys.argv
                else "live" if "--live" in sys.argv else "paper")
        asyncio.run(run_multi(real_mode=mode))   # PLAYER: trade many markets in parallel (favorites-only)
    else:
        asyncio.run(run())                # single BTC market, favorites-only (default, proven)
