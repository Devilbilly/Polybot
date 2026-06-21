"""
BTC up/down probability model for 5-min markets.

P(spot_end > strike) under a driftless lognormal random walk: with `secs_left` to go and
per-second log-vol `vol`, the log-return to close is ~Normal(0, vol*sqrt(secs_left)). So
  P(up) = Phi( ln(spot/strike) / (vol*sqrt(secs_left)) ).
As secs_left -> 0 it sharpens to 0/1 (the outcome is essentially decided).
"""
from __future__ import annotations
import math


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def prob_up(spot: float, strike: float, secs_left: float, vol: float) -> float:
    """Model probability that BTC closes above the strike. vol = per-sqrt-second log-vol."""
    if spot <= 0.0 or strike <= 0.0:
        return 0.5
    if secs_left <= 0.0 or vol <= 0.0:
        return 1.0 if spot > strike else 0.0
    sigma = vol * math.sqrt(secs_left)
    if sigma <= 1e-12:
        return 1.0 if spot > strike else 0.0
    return _norm_cdf(math.log(spot / strike) / sigma)
