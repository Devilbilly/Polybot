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

    def _reversal_stop(self, tick: Tick, pos: Position) -> List[Order]:
        """Exit a held side if it reversed below stop_p (no longer the favorite)."""
        orders: List[Order] = []
        if self.stop > 0.0:
            if pos.inv_yes >= 1.0 and tick.ws_bid < self.stop:
                orders.append(Order("YES", "SELL"))
            if pos.inv_no >= 1.0 and (1.0 - tick.ws_ask) < self.stop:
                orders.append(Order("NO", "SELL"))
        return orders

    def _can_enter(self, tick: Tick, pos: Position) -> bool:
        return tick.time_progress >= self.start and pos.n_entries < self.max_entries

    def decide(self, tick: Tick, pos: Position) -> List[Order]:
        orders = self._reversal_stop(tick, pos)
        if self._can_enter(tick, pos):
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


@register("momentum_favorite")
class MomentumFavorite(FavConvergence):
    """FavConvergence, but only enter a favorite that is RISING over a lookback window
    (confirmation that it is strengthening toward 1.0, not drifting down into the band)."""
    def __init__(self, name, params):
        super().__init__(name, params)
        self.lookback = params.get("lookback", 30)
        self.min_rise = params.get("min_rise", 0.0)
        self._hist = __import__("collections").deque(maxlen=self.lookback + 1)

    def reset(self):
        self._hist.clear()

    def decide(self, tick: Tick, pos: Position) -> List[Order]:
        self._hist.append(tick.ws_bid)
        orders = self._reversal_stop(tick, pos)
        if self._can_enter(tick, pos) and len(self._hist) > self.lookback:
            rise = tick.ws_bid - self._hist[0]          # change over the lookback window
            usd = pos.cash * self.bullet_pct
            if self.lo <= tick.ap1 <= self.hi and rise >= self.min_rise:
                orders.append(Order("YES", "BUY", usd))      # YES favorite, rising
            elif self.lo <= tick.no_ask <= self.hi and -rise >= self.min_rise:
                orders.append(Order("NO", "BUY", usd))       # NO favorite (YES bid falling)
        return orders


@register("scale_in_favorite")
class ScaleInFavorite(MomentumFavorite):
    """Average INTO a strengthening favorite: commit to one side, then add (same side only,
    no flip) each time it rises another `add_gap`, up to max_buy entries. Reversal-stop exits."""
    def __init__(self, name, params):
        super().__init__(name, params)
        self.add_gap = params.get("add_gap", 0.04)
        self._side = None
        self._last_price = 0.0

    def reset(self):
        super().reset()
        self._side = None
        self._last_price = 0.0

    def decide(self, tick: Tick, pos: Position) -> List[Order]:
        self._hist.append(tick.ws_bid)
        orders = self._reversal_stop(tick, pos)
        if self._can_enter(tick, pos) and len(self._hist) > self.lookback:
            rise = tick.ws_bid - self._hist[0]
            usd = pos.cash * self.bullet_pct
            yes_ok = self.lo <= tick.ap1 <= self.hi and rise >= self.min_rise
            no_ok = self.lo <= tick.no_ask <= self.hi and -rise >= self.min_rise
            if self._side is None:                       # first commitment
                if yes_ok:
                    self._side, self._last_price = "YES", tick.ap1
                    orders.append(Order("YES", "BUY", usd))
                elif no_ok:
                    self._side, self._last_price = "NO", tick.no_ask
                    orders.append(Order("NO", "BUY", usd))
            elif self._side == "YES" and yes_ok and tick.ap1 >= self._last_price + self.add_gap:
                self._last_price = tick.ap1
                orders.append(Order("YES", "BUY", usd))   # add to a strengthening YES
            elif self._side == "NO" and no_ok and tick.no_ask >= self._last_price + self.add_gap:
                self._last_price = tick.no_ask
                orders.append(Order("NO", "BUY", usd))
        return orders


@register("noop")
class NoOp(Strategy):
    """Never trades. Sanity baseline (final equity must equal start)."""
    def decide(self, tick: Tick, pos: Position) -> List[Order]:
        return []
