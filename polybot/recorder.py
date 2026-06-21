"""
Live data recorder: Polymarket WS -> SQLite (replaces brittle log-parsing).

Pure helpers (parse_book, predicted_slugs, build_tick_row) are unit-tested; the async
I/O loop is a thin wrapper around them. Run:  python -m polybot.recorder
"""
from __future__ import annotations
import json
from typing import List, Dict, Optional

WS_URI = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_API = "https://gamma-api.polymarket.com/events?slug={}"
CLOB_BOOK = "https://clob.polymarket.com/book?token_id={}"
WINDOW_SEC = 300


# ----------------------------- pure, testable helpers -----------------------------
def predicted_slugs(now_ts: int) -> List[str]:
    """Candidate slugs for the live 5-min market given a unix timestamp."""
    base = now_ts - (now_ts % WINDOW_SEC)
    return [f"btc-updown-5m-{base}", f"btc-updown-5m-{base + WINDOW_SEC}"]


def parse_book(book: dict) -> Dict[str, float]:
    """CLOB /book JSON -> top-3 levels. Robust to missing/empty sides."""
    rec = {f"{side}_{f}{lvl}": 0.0
           for side in ("bid", "ask") for f in ("p", "s") for lvl in (1, 2, 3)}
    if isinstance(book, dict):
        bids = sorted(book.get("bids", []), key=lambda x: float(x["price"]), reverse=True)
        for lvl in range(min(3, len(bids))):
            rec[f"bid_p{lvl+1}"] = float(bids[lvl]["price"])
            rec[f"bid_s{lvl+1}"] = float(bids[lvl]["size"])
        asks = sorted(book.get("asks", []), key=lambda x: float(x["price"]))
        for lvl in range(min(3, len(asks))):
            rec[f"ask_p{lvl+1}"] = float(asks[lvl]["price"])
            rec[f"ask_s{lvl+1}"] = float(asks[lvl]["size"])
    return rec


def build_tick_row(rem: float, ws_bid: float, ws_ask: float, book: Dict[str, float]) -> Dict[str, float]:
    """Assemble a DB tick row from WS best bid/ask + a parsed L2 book."""
    row = {"rem": rem, "ws_bid": ws_bid, "ws_ask": ws_ask}
    for k in ("bid_p1", "bid_s1", "bid_p2", "bid_s2", "bid_p3", "bid_s3",
              "ask_p1", "ask_s1", "ask_p2", "ask_s2", "ask_p3", "ask_s3"):
        row[k] = book.get(k, 0.0)
    return row


def winner_from_last(ws_bid: float) -> Optional[str]:
    if ws_bid <= 0:
        return None
    return "YES" if ws_bid > 0.5 else "NO"


def extract_token(ev) -> Optional[str]:
    """First CLOB token id from a gamma-api event payload. `clobTokenIds` comes back as either a
    JSON STRING or a list (Polymarket returns both shapes), so handle both; returns None if the
    payload is absent / wrong-shaped / has no token. Shared by live.py and recorder.py so the two
    can't drift."""
    if not ev or not isinstance(ev, (list, tuple)) or not isinstance(ev[0], dict):
        return None
    for m in ev[0].get("markets", []):
        if not isinstance(m, dict):
            continue
        ids = m.get("clobTokenIds")
        if isinstance(ids, str):
            try:
                ids = json.loads(ids)
            except Exception:
                ids = []
        if ids:
            return ids[0]
    return None


def winner_from_recent(recent_bids) -> Optional[str]:
    """Paper-trading settlement proxy kept CONSISTENT with the backtest's determine_winner:
    median of the last up-to-5 valid bids > 0.5 (not a single possibly-blipped last tick).
    Real live settlement is the on-chain oracle; this proxy exists only so live PAPER P&L is
    comparable to the validated backtest. Unlike determine_winner it always returns a side
    (the live loop must settle a position it already holds) — the 0.15-0.85 'unresolved'
    rejection is a backtest data-cleaning step, not a settlement rule."""
    import numpy as np
    vals = [float(b) for b in recent_bids if b is not None and b > 0][-5:]
    if not vals:
        return None
    return "YES" if float(np.median(vals)) > 0.5 else "NO"


# ----------------------------- async I/O loop -----------------------------
async def _get_json(session, url, timeout=5):
    import aiohttp
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            return await r.json(content_type=None)
    except Exception:
        return None


async def record(db_path: str = "polymarket.db"):  # pragma: no cover (needs live network)
    import asyncio, ssl, time
    import aiohttp, websockets
    from .database import Database

    db = Database(db_path)
    ssl_ctx = ssl.create_default_context(); ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla"},
                                     connector=aiohttp.TCPConnector(ssl=False)) as session:
        done = set()
        while True:
            now = int(time.time())
            slug = None
            for cand in predicted_slugs(now):
                if int(cand.split("-")[-1]) > now:        # skip not-yet-started future windows
                    continue
                data = await _get_json(session, GAMMA_API.format(cand))
                if data and len(data) > 0 and not data[0].get("closed"):
                    slug = cand; break
            if not slug:
                await asyncio.sleep(2); continue
            end_ts = int(slug.split("-")[-1]) + WINDOW_SEC
            ev = await _get_json(session, GAMMA_API.format(slug))
            token = extract_token(ev)
            if not token or token in done:
                await asyncio.sleep(2); continue
            seq = 0
            recent_bids = []       # last valid YES bids -> median winner proxy (matches backtest)
            try:
                async with websockets.connect(WS_URI, ssl=ssl_ctx, ping_interval=25) as ws:
                    await ws.send(json.dumps({"assets_ids": [token], "type": "market"}))
                    while end_ts - time.time() > 0:
                        try:
                            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                        except asyncio.TimeoutError:
                            continue
                        items = msg if isinstance(msg, list) else [msg]
                        book = parse_book(await _get_json(session, CLOB_BOOK.format(token), timeout=3) or {})
                        rem = end_ts - time.time()           # re-sample after blocking I/O
                        if rem <= 0:
                            break
                        for it in items:
                            if it.get("event_type") not in ("price_change", "best_bid_ask"):
                                continue
                            wb = float(it.get("best_bid") or 0); wa = float(it.get("best_ask") or 0)
                            if wb <= 0 or wa <= 0:
                                continue
                            recent_bids.append(wb)
                            db.insert_tick(token, seq, build_tick_row(rem, wb, wa, book)); seq += 1
            except Exception:
                await asyncio.sleep(2)
            finally:
                # finalize the market record even on disconnect; winner = median-of-recent proxy
                if seq > 0:
                    db.upsert_market(token, slug=slug, token_id=token, end_ts=end_ts,
                                     winner=winner_from_recent(recent_bids), n_ticks=seq)
                    done.add(token)


if __name__ == "__main__":  # pragma: no cover
    import asyncio
    asyncio.run(record())
