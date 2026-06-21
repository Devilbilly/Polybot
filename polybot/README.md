# polybot — a stable Polymarket trading engine

One engine, shared by backtest **and** live, so they can never diverge (the old `bt.py`
vs `l.py` split silently diverged 3×). Strategies are pure decision logic; a single
`ExecutionEngine` applies all fills.

## Modules
| module | role |
|---|---|
| `core.py` | `Tick`, `Position`, `Order`, `ExecutionEngine`, `RiskGovernor`, `Portfolio` |
| `strategies.py` | `Strategy` ABC + `@register` selector + `FavConvergence`, `FavHold`, `NoOp` |
| `database.py` | SQLite store + the one shared CSV loader (`csv_to_arrays`, `import_csv_dir`) |
| `recorder.py` | live WS → SQLite (replaces brittle log-parsing) |
| `backtester.py` | **parallel** per-market sim → sequential compounding → metrics |
| `live.py` | live trader on the SAME `Portfolio` |

## Usage
```bash
# backtest the configured portfolio on historical CSVs (parallel)
python -c "import json,glob; from polybot import backtester as bt; \
  print(bt.run_parallel(bt.load_markets('market_data'), json.load(open('polybot/portfolio.json'))))"

# record live market data into a database
python -m polybot.recorder            # -> polymarket.db

# paper-trade live on the shared engine
python -m polybot.live

# tests (unit + regression)
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
Then reference `"name": "my_strat"` in `portfolio.json`. No engine changes needed.

## The edge (validated)
Favorite-longshot bias: buy the *favorite* (price 0.70–0.93) in the late window, reversal-stop
at 0.50, hold to settlement. See `../research.py` for the calibration and the project memory
for full validation history.
