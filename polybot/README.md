# polybot — a stable, two-edge Polymarket trading engine

One engine, shared by backtest **and** live, so they can never diverge (the old `bt.py` vs
`l.py` split silently diverged 3×). Strategies are pure decision logic; a single
`ExecutionEngine` applies all fills. 14 modules, 160 tests.

## The two edges (the only edges in this market)
1. **Favorite-longshot bias** (behavioural, order-book): the prediction price is biased —
   favorites (price 0.70–0.95) are underpriced. Buy the favorite in the late window, reversal-
   stop at 0.50, hold to settlement. The favorite *price* is a near-sufficient statistic for
   the outcome — confirmed by an ML model (AUC 0.91; extra features add +0.002) — so this is
   the **only** order-book edge. Deployable today on Polymarket alone.
2. **BTC-spot divergence** (informational, external): a fast BTC spot feed leads the prediction
   book. Trade when a spot-model probability diverges from the market price. **Independent** of
   edge #1 (per-market return correlation +0.075), so combining them raises Sharpe. Idles on
   historical data lacking spot; **activates live** once a Binance feed populates `Tick.spot`.

Six hypotheses were tested and **rejected** on out-of-sample evidence (order-flow imbalance,
stop-and-flip, conviction leverage, stability filter, mean-reversion scalp, mean-variance
weights) plus a circular spot-exit caught and discarded. Capacity is real but **small**: the
median favorite book is ~$68 deep, so the edge is best at ~$1k–10k of capital.

## Modules
| module | role |
|---|---|
| `core.py` | `Tick`, `Position`, `Order`, `ExecutionEngine` (walk-book fills), `RiskGovernor` (kill-switch, round-stop, depletion halt, continuous de-risking, look-ahead gate), `Portfolio` |
| `strategies.py` | `Strategy` ABC + `@register` selector + `FavConvergence`, `MomentumFavorite`, `ScaleInFavorite`, `BtcSpotDivergence` (self-calibrating vol), `FavHold`, `NoOp` |
| `backtester.py` | **parallel** per-market sim → sequential compounding → metrics (run_parallel ≡ run_sequential) |
| `paper.py` | stateful paper-trading session (real fill caps); optionally persists each round |
| `live.py` | live trader on the SAME `Portfolio`; fetches Binance spot; persists rounds |
| `recorder.py` | live Polymarket WS → SQLite (replaces brittle log-parsing) |
| `database.py` | SQLite store + shared CSV loader + session track-record (`log_round`, `session_summary`) |
| `monitor.py` | metrics + alerts over persisted sessions (equity, drawdown, attribution, streak) |
| `robustness.py` | Monte-Carlo ordering, cost sensitivity, **capacity curve**, diversification report |
| `btc_model.py` | `prob_up(spot, strike, secs_left, vol)` — lognormal P(close > strike) |
| `synth.py` | synthetic markets from any (real/GBM) price path, with optional lag + favorite bias |
| `binance.py` | BTC spot/kline fetch + `closes_to_windows`, `estimate_vol` |
| `report.py` | `python -m polybot.report` — runs ALL testimony in one artifact |
| `validation.py` | `assert_causal` — proves a strategy uses no future data |

## Configs
- `portfolio.json` — favorites-only (40/40/10/10). Use for **historical backtests** and
  Polymarket-only deployment.
- `portfolio_live.json` — **two-edge** (favorites 70% + spot 30%). Use **live only** (needs a
  BTC spot feed). The spot sleeve idles without `Tick.spot`.

## Usage
```bash
# full validation report (favorite OOS + CV + Monte-Carlo + capacity, spot edge, decorrelation)
python -m polybot.report

# paper-trade the deployed config on recorded data (stateful, realistic fills)
python -m polybot.paper market_data polybot/portfolio.json

# parallel backtest
python -c "import json; from polybot import backtester as bt; \
  print(bt.run_parallel(bt.load_markets('market_data'), json.load(open('polybot/portfolio.json'))))"

# validate the spot edge on REAL Binance BTC paths
python validate_spot_real.py

# record live Polymarket data into a database
python -m polybot.recorder            # -> polymarket.db

# live paper-trade BOTH edges (needs Polymarket + Binance reachable)
python -m polybot.live                # uses portfolio.json; persists a live-<ts> session

# tests (unit + regression + integration)
python -m unittest discover -s tests
```

## Add a strategy
```python
from polybot.core import Strategy, Order
from polybot.strategies import register

@register("my_strat")
class MyStrat(Strategy):
    def decide(self, tick, pos):
        return [Order("YES", "BUY", pos.cash * 0.02)] if tick.time_progress > 0.9 else []
```
Reference `"name": "my_strat"` in a portfolio config. The look-ahead gate and risk governor
apply automatically; `assert_causal` will verify it uses no future data.

## Status & the one open question
Backtest-testable improvements are exhausted (the edge space is closed three ways: ML,
mean-variance optimization, and capacity). The remaining unknown is the **real Polymarket
book-vs-spot lag**, measurable only by running `recorder.py` + `live.py` against a live market.
Everything is staged for it: persistence, monitoring, alerts, the two-edge config, and a
self-calibrating, drawdown-managed risk layer. See the project memory for the full 25-iteration
validation history.
