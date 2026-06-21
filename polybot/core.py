"""
Core engine: shared by BOTH backtest and live so they can never diverge.

Design: strategies are PURE DECISION LOGIC. They look at a Tick + their Position and
return Orders. They never touch cash or fills directly. The ExecutionEngine applies fills
(size caps, slippage, fees) identically everywhere. This is the fix for the bt.py<->l.py
3x divergence: there is now exactly one execution path.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import List, Optional


class LookaheadError(Exception):
    """Raised when future data would be used (time goes backward within a market)."""


# ----------------------------- market state -----------------------------
@dataclass
class Tick:
    """One observation of the order book at a moment in a market's life."""
    ts: str
    time_progress: float          # 0..1, derived from REAL seconds remaining (no look-ahead)
    ws_bid: float
    ws_ask: float
    bid_p: tuple = (0.0, 0.0, 0.0)   # L1..L3 bid prices
    bid_s: tuple = (0.0, 0.0, 0.0)   # L1..L3 bid sizes
    ask_p: tuple = (0.0, 0.0, 0.0)
    ask_s: tuple = (0.0, 0.0, 0.0)
    spot: float = 0.0                # BTC spot now (live/synthetic); 0.0 = unknown (historical)
    strike: float = 0.0              # BTC price at window open

    # convenience accessors (L1)
    @property
    def bp1(self): return self.bid_p[0]
    @property
    def bs1(self): return self.bid_s[0]
    @property
    def ap1(self): return self.ask_p[0]
    @property
    def as1(self): return self.ask_s[0]
    @property
    def no_ask(self):  # cost to BUY the NO side via the book
        return 1.0 - self.bp1
    @property
    def no_bid(self):  # proceeds to SELL the NO side
        return 1.0 - self.ap1


# ----------------------------- position -----------------------------
@dataclass
class Position:
    cash: float
    inv_yes: float = 0.0
    inv_no: float = 0.0
    n_entries: int = 0                # total entries this market (yes+no) -> single-entry control
    # per-market accounting for reporting
    yes_bought: float = 0.0
    no_bought: float = 0.0
    yes_sold: float = 0.0
    no_sold: float = 0.0
    start_cash: float = 0.0

    def reset_market(self):
        self.inv_yes = self.inv_no = 0.0
        self.n_entries = 0
        self.yes_bought = self.no_bought = self.yes_sold = self.no_sold = 0.0
        self.start_cash = self.cash

    def equity(self, tick: Tick) -> float:
        return self.cash + self.inv_yes * tick.ws_bid + self.inv_no * (1.0 - tick.ws_ask)


# ----------------------------- orders -----------------------------
@dataclass
class Order:
    side: str          # 'YES' | 'NO'
    kind: str          # 'BUY' | 'SELL'
    usd: float = 0.0   # desired notional for BUY (engine caps by book + cash)


# ----------------------------- execution -----------------------------
class ExecutionEngine:
    """Applies fills identically for backtest and live. Caps buys by resting book size and
    (when walk_book) consumes deeper L2/L3 levels at worse prices for size beyond L1 — so
    large bets at a compounded bankroll pay a realistic, worse average price."""
    def __init__(self, fee: float = 0.001, slippage: float = 0.002, cap_fills: bool = True,
                 walk_book: bool = True):
        self.fee = fee
        self.slippage = slippage
        self.cap_fills = cap_fills
        self.walk_book = walk_book

    def execute(self, order: Order, tick: Tick, pos: Position) -> bool:
        if order.kind == "BUY":
            return self._buy(order, tick, pos)
        return self._sell(order, tick, pos)

    def _buy(self, order, tick, pos) -> bool:
        # Price/size ladder for the chosen side (NO ask at level k = 1 - bid_p[k]).
        if order.side == "YES":
            levels = [(tick.ask_p[k], tick.ask_s[k]) for k in range(3)]
        else:
            levels = [(1.0 - tick.bid_p[k], tick.bid_s[k]) for k in range(3)]
        budget = min(order.usd, pos.cash)
        total_tokens = 0.0
        total_cost = 0.0
        for li, (price, size) in enumerate(levels):
            if li > 0 and not self.walk_book:
                break                                  # L1-only mode
            if price <= 0.0 or price >= 1.0 or size <= 0.0:
                continue
            remaining = budget - total_cost
            if remaining <= 0.0:
                break
            exec_p = min(0.9999, price + self.slippage)
            want = remaining / (exec_p * (1.0 + self.fee))
            take = min(want, size) if self.cap_fills else want
            if take <= 0.0:
                continue
            total_tokens += take
            total_cost += take * exec_p * (1.0 + self.fee)
            if not self.cap_fills:
                break                                  # uncapped: took it all at L1
        if total_tokens < 1.0 or total_cost > pos.cash + 1e-9:
            return False
        pos.cash -= total_cost
        pos.n_entries += 1
        if order.side == "YES":
            pos.inv_yes += total_tokens; pos.yes_bought += total_tokens
        else:
            pos.inv_no += total_tokens; pos.no_bought += total_tokens
        return True

    def _sell(self, order, tick, pos) -> bool:
        if order.side == "YES":
            if pos.inv_yes < 1.0:
                return False
            price, avail = tick.bp1, tick.bs1
            exec_p = max(0.0001, price - self.slippage)
            sold = min(pos.inv_yes, avail) if self.cap_fills else pos.inv_yes
            if sold < 1.0:
                return False
            pos.inv_yes -= sold; pos.yes_sold += sold
            pos.cash += sold * exec_p * (1.0 - self.fee)
        else:
            if pos.inv_no < 1.0:
                return False
            price, avail = tick.no_bid, tick.as1
            exec_p = max(0.0001, price - self.slippage)
            sold = min(pos.inv_no, avail) if self.cap_fills else pos.inv_no
            if sold < 1.0:
                return False
            pos.inv_no -= sold; pos.no_sold += sold
            pos.cash += sold * exec_p * (1.0 - self.fee)
        return True


# ----------------------------- strategy base -----------------------------
class Strategy(ABC):
    """Pure decision logic. Implement decide(); never touch cash/fills directly."""
    def __init__(self, name: str, params: dict):
        self.name = name
        self.params = params

    @abstractmethod
    def decide(self, tick: Tick, pos: Position) -> List[Order]:
        ...

    def reset(self):
        pass


# ----------------------------- risk governor -----------------------------
class RiskGovernor:
    """Blocks NEW ENTRIES under three sticky/transient conditions; exits always allowed."""
    def __init__(self, initial_capital: float, kill_switch_dd=0.25, round_loss_limit=0.08,
                 min_capital=50.0, soft_dd=None):
        self.initial_capital = initial_capital
        self.kill_switch_dd = kill_switch_dd
        self.round_loss_limit = round_loss_limit
        self.min_capital = min_capital
        # soft_dd: drawdown at which bet sizing starts shrinking linearly toward 0 at kill_switch_dd.
        # Default = kill_switch_dd -> no gradual de-risking (binary, backward compatible).
        self.soft_dd = kill_switch_dd if soft_dd is None else soft_dd
        self.peak = initial_capital            # MTM high-water (intra-market de-risking only)
        self.realized_peak = initial_capital   # realized/settled high-water (drives the kill-switch)
        self.killed = False
        self.halted = False
        self.round_start_equity = initial_capital

    def size_multiplier(self, equity: float) -> float:
        """Continuous de-risking: 1.0 until drawdown reaches soft_dd, then linearly down to 0
        at kill_switch_dd. Smooths the binary kill-switch and trims the drawdown tail."""
        if self.peak <= 0 or self.soft_dd >= self.kill_switch_dd:
            return 1.0
        dd = (self.peak - equity) / self.peak
        if dd <= self.soft_dd:
            return 1.0
        if dd >= self.kill_switch_dd:
            return 0.0
        return 1.0 - (dd - self.soft_dd) / (self.kill_switch_dd - self.soft_dd)

    def new_market(self, realized_equity: float):
        # The kill-switch is a BETWEEN-market capital-preservation decision evaluated on REALIZED
        # equity (positions are flat here) -- NOT intra-market mark-to-market swings, so a permanent
        # halt cannot fire on an unrealized paper high/low that settles differently.
        self.realized_peak = max(self.realized_peak, realized_equity)
        if self.realized_peak > 0 and (self.realized_peak - realized_equity) / self.realized_peak > self.kill_switch_dd:
            self.killed = True
        self.round_start_equity = realized_equity

    def allow_entries(self, equity: float, total_cash: float) -> bool:
        self.peak = max(self.peak, equity)            # MTM peak feeds size_multiplier (intra-market)
        if self.killed:
            return False
        if total_cash < self.min_capital:
            self.halted = True
            return False
        if self.round_start_equity > 0:
            if (self.round_start_equity - equity) / self.round_start_equity > self.round_loss_limit:
                return False
        return True


# ----------------------------- portfolio -----------------------------
@dataclass
class RoundResult:
    round_no: int
    winner: str
    total_pnl: float
    total_cash: float
    per_strategy: dict


class Portfolio:
    """Runs N strategies on shared ticks via one ExecutionEngine + RiskGovernor."""
    def __init__(self, strategies: List[Strategy], weights: List[float],
                 total_capital: float = 1000.0, engine: Optional[ExecutionEngine] = None,
                 risk: Optional[RiskGovernor] = None, strict_time: bool = True):
        self.strategies = strategies
        self.engine = engine or ExecutionEngine()
        self.risk = risk or RiskGovernor(total_capital)
        self.accounts = [Position(cash=total_capital * w) for w in weights]
        self.round_no = 0
        self.strict_time = strict_time   # gate: forbid feeding non-chronological (future) ticks
        self._last_tp = -1.0

    def total_cash(self) -> float:
        return sum(a.cash for a in self.accounts)

    def equity(self, tick: Tick) -> float:
        return sum(a.equity(tick) for a in self.accounts)

    def new_market(self, opening_tick: Optional[Tick] = None):
        for s in self.strategies:
            s.reset()
        for a in self.accounts:
            a.reset_market()
        self.risk.new_market(self.total_cash())
        self._last_tp = -1.0

    def process_tick(self, tick: Tick):
        # Look-ahead gate: time must move forward. A backward jump means future/out-of-order
        # data is being fed (e.g. the old i/total_ticks bug) -> refuse rather than cheat.
        if self.strict_time and tick.time_progress + 1e-9 < self._last_tp:
            raise LookaheadError(
                f"time_progress went backward ({tick.time_progress:.4f} < {self._last_tp:.4f}); "
                f"future/out-of-order data")
        self._last_tp = tick.time_progress
        equity = self.equity(tick)
        allow = self.risk.allow_entries(equity, self.total_cash())
        scale = self.risk.size_multiplier(equity) if allow else 0.0
        for strat, pos in zip(self.strategies, self.accounts):
            for order in strat.decide(tick, pos):
                if order.kind == "BUY":
                    if scale <= 0.0:
                        continue
                    order.usd *= scale            # continuous de-risking near the drawdown limit
                self.engine.execute(order, tick, pos)

    def settle(self, winner_yes: bool) -> RoundResult:
        self.round_no += 1
        per = {}
        total_pnl = 0.0
        for strat, pos in zip(self.strategies, self.accounts):
            payout = pos.inv_yes if winner_yes else pos.inv_no
            pos.cash += payout
            pnl = pos.cash - pos.start_cash
            total_pnl += pnl
            per[strat.name] = {"pnl": pnl, "cash": pos.cash,
                               "yes_bought": pos.yes_bought, "no_bought": pos.no_bought}
            pos.reset_market()
        return RoundResult(self.round_no, "YES" if winner_yes else "NO",
                           total_pnl, self.total_cash(), per)
