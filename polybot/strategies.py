"""
Strategy registry (the "flexible strategy selector") + the strategy library.
Add a strategy by subclassing Strategy and decorating with @register("name").
Select strategies by name in a portfolio config — no engine changes needed.
"""
from __future__ import annotations
from typing import List
from .core import Strategy, Order, Tick, Position

_REGISTRY = {}


def register(name: str):
    def deco(cls):
        _REGISTRY[name] = cls
        cls.strategy_name = name
        return cls
    return deco


def get_strategy(name: str, params: dict) -> Strategy:
    if name not in _REGISTRY:
        raise KeyError(f"unknown strategy '{name}'. available: {sorted(_REGISTRY)}")
    return _REGISTRY[name](name, params)


def available() -> List[str]:
    return sorted(_REGISTRY)


# --------------------------------------------------------------------------
@register("fav_convergence")
class FavConvergence(Strategy):
    """Buy the FAVORITE side (price in [buy_p, sell_p]) in the late window, hold to
    settlement, exit if it reverses below stop_p (no longer the favorite).
    The validated +EV edge (favorite-longshot bias). Single entry per market (no flip)."""

    def __init__(self, name, params):
        super().__init__(name, params)
        self.lo = params.get("buy_p", 0.70)
        self.hi = params.get("sell_p", 0.93)
        self.start = params.get("time_cutoff", 0.50)   # earliest entry progress
        self.stop = params.get("stop_p", 0.50)         # reversal exit level
        self.max_entries = params.get("max_buy", 1)
        self.bullet_pct = params.get("bullet_pct", 0.02)

    def decide(self, tick: Tick, pos: Position) -> List[Order]:
        orders: List[Order] = []
        # Reversal stop (any time we hold) — exits are never blocked by single-entry cap.
        if self.stop > 0.0:
            if pos.inv_yes >= 1.0 and tick.ws_bid < self.stop:
                orders.append(Order("YES", "SELL"))
            if pos.inv_no >= 1.0 and (1.0 - tick.ws_ask) < self.stop:
                orders.append(Order("NO", "SELL"))
        # Single entry per market, late window, favorite in band.
        if tick.time_progress >= self.start and pos.n_entries < self.max_entries:
            usd = pos.cash * self.bullet_pct
            if self.lo <= tick.ap1 <= self.hi:
                orders.append(Order("YES", "BUY", usd))
            elif self.lo <= tick.no_ask <= self.hi:
                orders.append(Order("NO", "BUY", usd))
        return orders


@register("fav_hold")
class FavHold(FavConvergence):
    """FavConvergence with NO reversal stop (hold to settlement). Baseline for comparison."""
    def __init__(self, name, params):
        params = {**params, "stop_p": 0.0}
        super().__init__(name, params)


@register("noop")
class NoOp(Strategy):
    """Never trades. Sanity baseline (final equity must equal start)."""
    def decide(self, tick: Tick, pos: Position) -> List[Order]:
        return []
