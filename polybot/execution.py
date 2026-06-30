#!/usr/bin/env python3
"""Execution layer — abstracts order placement so PAPER / SHADOW / LIVE share ONE interface.
Strategies decide; an Executor turns a decision into a (possibly real) order + Fill.

    Executor.place_order(market_id, side, shares, price_hint) -> Fill | None

MODES (live.py picks via real_mode):
  paper  -> executor = None. Sim engine fills only (today's behaviour). Default; box untouched.
  shadow -> MockExecutor. Mirrors each paper entry with a 1-SHARE order that is NEVER sent;
            returns a simulated 'real' fill so we exercise the full pipeline + capture real-vs-paper
            into the ledger. ZERO real money.
  live   -> ClobExecutor (real Polymarket CLOB, 1-share probes). STUB until wallet creds + the
            py-clob-client are wired — intentionally raises so it can't be enabled by accident.

The 1-share probe is the SMALLEST real order (≈ favourite price, $0.7–0.9), the right size for
Stage-1 fill measurement (it sidesteps the bullet_pct × 1-share-minimum conflict at small capital).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class Fill:
    order_id: str
    side: str
    shares: float
    price: float                 # average fill price actually obtained
    fee: float = 0.0
    tx_hash: Optional[str] = None
    status: str = "FILLED"       # FILLED | PARTIAL | REJECTED


class Executor:
    dry_run = True               # True = sends nothing real
    def place_order(self, token_id: str, side: str, shares: float,
                    price_hint: Optional[float] = None, no_token: Optional[str] = None) -> Optional[Fill]:
        raise NotImplementedError


class MockExecutor(Executor):
    """Pretends to fill `shares` at price_hint + a small taker slippage. SENDS NOTHING.
    Lets us validate the decision→order→fill→ledger pipeline with no creds and no risk."""
    dry_run = True

    def __init__(self, slippage: float = 0.005):
        self.slippage = slippage
        self._n = 0

    def place_order(self, token_id, side, shares, price_hint=None, no_token=None):
        self._n += 1
        px = price_hint if (price_hint and price_hint > 0) else 0.80
        fill_px = min(0.999, px + self.slippage)     # taker pays a touch more than quoted
        return Fill(order_id=f"mock-{str(token_id)[:8]}-{self._n}", side=side, shares=shares,
                    price=fill_px, fee=0.0, tx_hash=None, status="FILLED")


class ClobExecutor(Executor):
    """REAL Polymarket CLOB taker orders via **py-clob-client-v2**.

    Why v2: the archived v1 SDK signs the OLD EIP-712 order schema, which the exchange now rejects
    with `order_version_mismatch` ("invalid order version"). v2 signs domain "2". And the funder
    (0x7D31...) is a Polymarket DEPOSIT WALLET -> signature_type=3 (POLY_1271), not the type-1 Magic
    proxy (which gives "maker address not allowed, please use the deposit wallet flow").

    Safety rails (real money):
      * Fixed ~`min_usd` order size (default $1) via INTEGER shares — Polymarket requires a marketable
        BUY >= $1, and the maker amount must be <= 2 decimals, so price(0.01-tick) x integer-shares
        stays on whole cents. HARD `max_shares` cap clamps the upside.
      * BUY-only (fav_hold holds to settlement; binaries auto-resolve, no SELL needed).
      * Only favorites (px >= `min_price`); longshots are skipped.
      * ADVERSE-SELECTION / FLOOR GATE (`max_slip_below`): refuse when the live marketable price has
        dropped > max_slip_below below the strategy's decision (`price_hint`). A marketable BUY only
        fills cheaper when the favorite is COLLAPSING, so those fills are adversely selected and win far
        less (measured live: ~66% vs ~82% for fills that drifted under the 0.76 floor) -> don't chase it.
      * Side->token: YES buys ids[0] (Up); NO buys ids[1] (Down), each priced from a live REST /book;
        a STALE-BOOK GATE refuses a NO whose paper price (1-up_bid) disagrees with the real Down ask.
      * `dry_run=True` builds+signs only (create_order), posts nothing. dry_run=False -> create_and_post_order FOK.
      * Every path returns a Fill (never raises into the trader). Creds load from 0600 files; key never logged.
    Imports are LAZY (inside methods) so this module imports fine where py-clob-client-v2 isn't installed.
    """

    def __init__(self, key_path: str, funder_path: str, host: str = "https://clob.polymarket.com",
                 chain_id: int = 137, max_shares: float = 5.0, dry_run: bool = True,
                 price_buffer_ticks: int = 3, desync_tol: float = 0.05,
                 min_usd: float = 1.0, min_price: float = 0.5, signature_type: int = None,
                 max_slip_below: float = 0.02, max_fill_price: float = 0.90):
        from eth_account import Account
        from py_clob_client_v2 import ClobClient
        key = open(key_path).read().strip()
        self.funder = open(funder_path).read().strip()
        eoa = Account.from_key(key).address
        # 0x7D31 is a deposit wallet -> POLY_1271 (sig type 3). EOA==funder would be a bare EOA (0).
        self.signature_type = signature_type if signature_type is not None else (
            0 if eoa.lower() == self.funder.lower() else 3)
        self.client = ClobClient(host, chain_id, key=key,
                                 signature_type=self.signature_type, funder=self.funder)
        self.client.set_api_creds(self.client.derive_api_key())     # v2 L2 auth
        self.max_shares = float(max_shares)
        self.dry_run = bool(dry_run)
        self.price_buffer_ticks = int(price_buffer_ticks)
        self.desync_tol = float(desync_tol)
        self.min_usd = float(min_usd)
        self.min_price = float(min_price)
        self.max_slip_below = float(max_slip_below)
        self.max_fill_price = float(max_fill_price)   # HARD CEILING: never let the spread-cross buffer
        self._tick_cache = {}                         # push a fill into the -EV >sell_p zone

    def _tick(self, tok):
        if tok not in self._tick_cache:
            self._tick_cache[tok] = float(self.client.get_tick_size(str(tok)))
        return self._tick_cache[tok]

    def _book_ask(self, tok):
        ob = self.client.get_order_book(str(tok))            # v2 returns a dict
        asks = ob.get("asks") if isinstance(ob, dict) else getattr(ob, "asks", None)
        prices = []
        for x in (asks or []):
            p = x.get("price") if isinstance(x, dict) else getattr(x, "price", None)
            if p is not None:
                prices.append(float(p))
        return min(prices) if prices else None

    def _size_for(self, limit):
        import math
        n = max(1, math.ceil(self.min_usd / limit))          # integer shares >= $1 of notional
        return float(min(n, int(self.max_shares)))

    def place_order(self, token_id, side, shares, price_hint=None, no_token=None):
        from py_clob_client_v2.clob_types import OrderArgs
        try:
            from py_clob_client_v2.order_builder.constants import BUY
        except Exception:
            BUY = "BUY"
        try:
            tok = str(token_id) if side == "YES" else str(no_token or token_id)
            real_ask = self._book_ask(tok)
            if side == "NO" and real_ask and price_hint and abs(real_ask - float(price_hint)) > self.desync_tol:
                return Fill("", side, 0.0, 0.0,
                            status=f"REJECTED:stale(paper{float(price_hint):.2f}/book{real_ask:.2f})")
            px = real_ask or price_hint
            if not px or not (0.0 < float(px) < 1.0):
                return Fill("", side, 0.0, 0.0, status="REJECTED:price")
            if float(px) < self.min_price:                   # favorites only; never chase longshots
                return Fill("", side, 0.0, 0.0, status=f"REJECTED:longshot({float(px):.2f})")
            # ADVERSE-SELECTION / FLOOR GATE: the strategy decided to buy at `price_hint` (>= the floor,
            # e.g. 0.76). A marketable BUY fills below the decision ONLY when the favorite is collapsing
            # -> those fills are adversely selected (win ~66% vs ~82%). If the live price has dropped
            # > max_slip_below below the decision, the favorite is reversing -> skip, don't chase it down.
            if price_hint and float(px) < float(price_hint) - self.max_slip_below:
                return Fill("", side, 0.0, 0.0,
                            status=f"REJECTED:slipped(hint{float(price_hint):.2f}/ask{float(px):.2f})")
            # CEILING GATE: refuse to BUY a favorite whose LIVE marketable ask is already above the
            # ceiling (max_fill_price = sell_p). The >sell_p zone is -EV in REAL money (Phase 7/8:
            # [0.90,0.93) real margin -0.0485 -- slippage/adverse-selection on thin >0.90 books). The
            # favorite can drift up between the strategy's decision and execution; this stops us paying
            # for it. (Without this, the +3-tick spread-cross buffer let fills reach 0.91-0.92.)
            if self.max_fill_price and float(px) > self.max_fill_price + 1e-9:
                return Fill("", side, 0.0, 0.0,
                            status=f"REJECTED:ceiling(ask{float(px):.2f}>{self.max_fill_price:.2f})")
            # Snap the limit to >= 1-cent granularity: integer-shares x a 2-decimal price keeps the
            # maker amount on whole cents (Polymarket caps maker at 2 decimals). A 1c price is valid on
            # every market tick (0.1/0.01/0.001/0.0001), so this also fixes "invalid amounts" on fine-tick markets.
            tick = max(self._tick(tok), 0.01)
            raw = min(1.0 - tick, float(px) + self.price_buffer_ticks * tick)   # marketable: cross the spread
            if self.max_fill_price:                          # cap the limit too: the buffer must NOT push
                raw = min(raw, self.max_fill_price)          # the fill above the ceiling on a deeper book
            limit = round(round(raw / tick) * tick, 2)
            order_shares = self._size_for(limit)             # integer shares ~ $1
            args = OrderArgs(token_id=tok, price=limit, size=order_shares, side=BUY)
            if self.dry_run:
                self.client.create_order(args)               # builds + SIGNS, posts nothing
                return Fill("dryrun", side, order_shares, limit, status="DRYRUN")
            # FAK (fill-and-kill / IOC): take whatever is immediately available, cancel the rest. FOK
            # (all-or-nothing) was killing ~5% of orders as "couldn't be fulfilled" on thin 5-min books.
            resp = self.client.create_and_post_order(args, order_type="FAK") or {}
            ok = bool(resp.get("success"))
            making = float(resp.get("makingAmount") or 0.0)  # USDC spent
            taking = float(resp.get("takingAmount") or 0.0)  # shares received
            fill_px = (making / taking) if taking else limit
            txs = resp.get("transactionsHashes") or []
            status = (resp.get("status") or "FILLED") if ok else \
                str(resp.get("errorMsg") or resp.get("status") or "REJECTED")
            return Fill(order_id=resp.get("orderID") or "", side=side,
                        shares=(taking or order_shares), price=fill_px, fee=0.0,
                        tx_hash=(txs[0] if txs else None), status=status)
        except Exception as e:
            # shares=0.0 (NOT the requested size): a failed order filled NOTHING. Returning the
            # requested shares with price=0 created "phantom fills" that the P&L scorer counted as
            # free $1 winners (fabricated profit). A failed order must report zero filled shares.
            return Fill(order_id="", side=side, shares=0.0, price=0.0,
                        status=f"ERR:{type(e).__name__}:{str(e)[:80]}")


def make_executor(real_mode: str = "paper", key_path=None, funder_path=None,
                  max_shares: float = 5.0, **kw) -> Optional[Executor]:
    """Factory used by live.run_multi.
      paper  -> None (unchanged)
      shadow -> MockExecutor (no creds, sends nothing)
      dryrun -> ClobExecutor(dry_run=True): real client builds+signs but never posts
      live   -> ClobExecutor(dry_run=False): real ~$1 orders (v2/sig3; min $1, integer shares)
    Creds default to the box 0600 files."""
    if real_mode == "paper":
        return None
    if real_mode == "shadow":
        return MockExecutor(**{k: v for k, v in kw.items() if k == "slippage"})
    if real_mode in ("dryrun", "live"):
        import os
        kp = key_path or os.path.expanduser("~/.config/polybot-clob.key")
        fp = funder_path or os.path.expanduser("~/.config/polybot-clob.funder")
        return ClobExecutor(kp, fp, max_shares=max_shares, dry_run=(real_mode == "dryrun"),
                            max_slip_below=float(kw.get("max_slip_below", 0.02)),
                            max_fill_price=float(kw.get("max_fill_price", 0.90)))
    raise ValueError(f"unknown real_mode {real_mode!r} (paper|shadow|dryrun|live)")
