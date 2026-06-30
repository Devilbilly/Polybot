# Phase-8 — Full 2D Price-Band Sweep: proving the optimal favorite [buy_p, sell_p]

**Verdict: [0.78, 0.90] IS near-optimal — KEEP the deployed band.**

## Question & setup

Polybot buys the **FAVORITE** side of a crypto 5-min up/down market when its price is in **[buy_p, sell_p]** (a chop gate skips flat rounds) and HOLDS to settle. Edge per share = **q(x) − p** (favorite realized win-rate minus price paid). The live deployed band is **[buy_p=0.78, sell_p=0.90]** (sell_p was just raised 0.85→0.90 in phase7). Floor has historically been 0.70/0.76/0.78. Phase-7 only swept the ceiling with the floor fixed at 0.78; **phase-8 sweeps BOTH dimensions** so the floor is no longer assumed, and proves where the realized edge is maximized.

- Archive: `archive/master_polybot.db`, span **2026-06-22 14:47:57.085269 .. 2026-06-30 06:57:53.261423 UTC** (8 days).
- Usable favorites (first tick rem≤150 with a side ≥0.70, capped ≤0.97): **5237/6279** (dropped no_winner=0, no_market_end=0, no_coin=343, no_entry=699).
- By coin: {'btc': 1480, 'sol': 1351, 'xrp': 1073, 'eth': 1333}. Base favorite(≥0.70) win-rate = **0.835**.
- Favorites at/above each candidate floor: {0.7: 5237, 0.74: 3447, 0.76: 2993, 0.78: 2636, 0.8: 2367}.

**Entry (no look-ahead).** First `ticks` row with `rem≤150` where a favorite side (YES ask=`ask_p1`, NO ask=`1−bid_p1`) is ≥0.70. Side + entry price taken there; a favorite already above a candidate ceiling at that tick is **entered at that price** (not skipped). Label = 1 if side==winner. Reuses `phase4_features` (reconstruction klines/spot machinery) and `phase7_sellp` (summarize / EV / bootstrap / chop gates / real-ledger) — both **imported, not modified**.

**EV convention.** Per \$1 staked on a 1-share favorite buy at price *p* paying \$1 on win: `EV/$1 = win%·(1/p) − 1 − COST`, COST=0.003 (fee 0.001 + slippage 0.002). The price-comparable **win−price margin** (`win% − mean price`) is the primary decision metric; **total \$** is realized payoff at \$1/trade.

## The 2D grid — WITH the deployed chop gate (the LIVE config, primary basis)

Deployed gate (`polybot/strategies.py::_not_chop`): keep iff `|spot−strike|/strike ≥ 0.00056` at the entry tick; **NO-OP keep** when the tick had no spot/strike (older box only). Coverage: 1484 favorites had tick spot/strike, 702 passed; kept 4455/5237. **This is the live config**, so it is the primary basis for the decision. The deployed cell **[0.78, 0.90]** is bolded.

**Win−price MARGIN** (rows = buy_p floor, cols = sell_p ceiling)

| floor \ ceiling | 0.85 | 0.88 | 0.90 | 0.93 | 0.97 |
|---:|---:|---:|---:|---:|---:|
| **0.70** | +0.0496 | +0.0466 | +0.0447 | +0.0437 | +0.0389 |
| **0.74** | +0.0493 | +0.0442 | +0.0413 | +0.0403 | +0.0341 |
| **0.76** | +0.0507 | +0.0440 | +0.0405 | +0.0395 | +0.0326 |
| **0.78** | +0.0528 | +0.0439 | **+0.0396** | +0.0387 | +0.0312 |
| **0.80** | +0.0471 | +0.0380 | +0.0341 | +0.0344 | +0.0274 |

**Total \$ @ \$1/trade (8 days)** (rows = buy_p floor, cols = sell_p ceiling)

| floor \ ceiling | 0.85 | 0.88 | 0.90 | 0.93 | 0.97 |
|---:|---:|---:|---:|---:|---:|
| **0.70** | +181.9 | +188.1 | +192.6 | +206.3 | +210.6 |
| **0.74** | +91.3 | +97.5 | +102.0 | +115.7 | +120.0 |
| **0.76** | +70.0 | +76.2 | +80.7 | +94.4 | +98.7 |
| **0.78** | +54.1 | +60.4 | **+64.9** | +78.5 | +82.8 |
| **0.80** | +34.9 | +41.1 | +45.6 | +59.3 | +63.6 |

**EV per \$1 (cost-adjusted)** (rows = buy_p floor, cols = sell_p ceiling)

| floor \ ceiling | 0.85 | 0.88 | 0.90 | 0.93 | 0.97 |
|---:|---:|---:|---:|---:|---:|
| **0.70** | +0.0632 | +0.0589 | +0.0560 | +0.0539 | +0.0473 |
| **0.74** | +0.0596 | +0.0527 | +0.0487 | +0.0466 | +0.0386 |
| **0.76** | +0.0603 | +0.0515 | +0.0468 | +0.0447 | +0.0360 |
| **0.78** | +0.0620 | +0.0507 | **+0.0451** | +0.0431 | +0.0338 |
| **0.80** | +0.0540 | +0.0426 | +0.0377 | +0.0371 | +0.0286 |

**Trade count n** (rows = buy_p floor, cols = sell_p ceiling)

| floor \ ceiling | 0.85 | 0.88 | 0.90 | 0.93 | 0.97 |
|---:|---:|---:|---:|---:|---:|
| **0.70** | 2878 | 3196 | 3442 | 3827 | 4455 |
| **0.74** | 1532 | 1850 | 2096 | 2481 | 3109 |
| **0.76** | 1161 | 1479 | 1725 | 2110 | 2738 |
| **0.78** | 873 | 1191 | **1437** | 1822 | 2450 |
| **0.80** | 647 | 965 | 1211 | 1596 | 2224 |

### Optimal cells (chop-gated)

- **By realized total \$:** `[0.70, 0.97]` → total **\$+210.6**, margin +0.0389, n=4455, fire 100.0%.
- **By win−price margin (n≥100):** `[0.78, 0.85]` → margin **+0.0528**, total \$+54.1, n=873.
- **Deployed [0.78, 0.90]:** margin +0.0396, total \$+64.9, EV/\$1 +0.0451, n=1437, fire 32.3%.

### Per-cell detail (chop-gated, audit trail)

| cell [floor,ceil] | n | fire% | win% | mean price | margin | margin 95% CI | EV/$1 | total $ |
|---|---:|---:|---:|---:|---:|:---:|---:|---:|
| [0.70,0.85] | 2878 | 64.6% | 80.2% | 0.752 | +0.0496 | [+0.036, +0.065] | +0.0632 | +181.9 |
| [0.70,0.88] | 3196 | 71.7% | 81.1% | 0.764 | +0.0466 | [+0.034, +0.060] | +0.0589 | +188.1 |
| [0.70,0.90] | 3442 | 77.3% | 81.8% | 0.773 | +0.0447 | [+0.032, +0.058] | +0.0560 | +192.6 |
| [0.70,0.93] | 3827 | 85.9% | 83.2% | 0.788 | +0.0437 | [+0.032, +0.056] | +0.0539 | +206.3 |
| [0.70,0.97] | 4455 | 100.0% | 85.1% | 0.812 | +0.0389 | [+0.028, +0.049] | +0.0473 | +210.6 |
| [0.74,0.85] | 1532 | 34.4% | 83.7% | 0.788 | +0.0493 | [+0.030, +0.068] | +0.0596 | +91.3 |
| [0.74,0.88] | 1850 | 41.5% | 84.6% | 0.802 | +0.0442 | [+0.028, +0.061] | +0.0527 | +97.5 |
| [0.74,0.90] | 2096 | 47.0% | 85.4% | 0.813 | +0.0413 | [+0.026, +0.057] | +0.0487 | +102.0 |
| [0.74,0.93] | 2481 | 55.7% | 87.0% | 0.830 | +0.0403 | [+0.027, +0.053] | +0.0466 | +115.7 |
| [0.74,0.97] | 3109 | 69.8% | 88.9% | 0.855 | +0.0341 | [+0.024, +0.045] | +0.0386 | +120.0 |
| [0.76,0.85] | 1161 | 26.1% | 85.3% | 0.802 | +0.0507 | [+0.031, +0.071] | +0.0603 | +70.0 |
| [0.76,0.88] | 1479 | 33.2% | 86.1% | 0.817 | +0.0440 | [+0.026, +0.061] | +0.0515 | +76.2 |
| [0.76,0.90] | 1725 | 38.7% | 86.8% | 0.828 | +0.0405 | [+0.024, +0.057] | +0.0468 | +80.7 |
| [0.76,0.93] | 2110 | 47.4% | 88.4% | 0.845 | +0.0395 | [+0.026, +0.053] | +0.0447 | +94.4 |
| [0.76,0.97] | 2738 | 61.5% | 90.3% | 0.870 | +0.0326 | [+0.021, +0.044] | +0.0360 | +98.7 |
| [0.78,0.85] | 873 | 19.6% | 86.7% | 0.814 | +0.0528 | [+0.029, +0.074] | +0.0620 | +54.1 |
| [0.78,0.88] | 1191 | 26.7% | 87.3% | 0.829 | +0.0439 | [+0.026, +0.063] | +0.0507 | +60.4 |
| **[0.78,0.90] (deployed)** | 1437 | 32.3% | 88.0% | 0.841 | +0.0396 | [+0.022, +0.057] | +0.0451 | +64.9 |
| [0.78,0.93] | 1822 | 40.9% | 89.6% | 0.858 | +0.0387 | [+0.024, +0.053] | +0.0431 | +78.5 |
| [0.78,0.97] | 2450 | 55.0% | 91.4% | 0.883 | +0.0312 | [+0.020, +0.042] | +0.0338 | +82.8 |
| [0.80,0.85] | 647 | 14.5% | 87.2% | 0.825 | +0.0471 | [+0.021, +0.071] | +0.0540 | +34.9 |
| [0.80,0.88] | 965 | 21.7% | 87.8% | 0.840 | +0.0380 | [+0.018, +0.059] | +0.0426 | +41.1 |
| [0.80,0.90] | 1211 | 27.2% | 88.5% | 0.851 | +0.0341 | [+0.017, +0.052] | +0.0377 | +45.6 |
| [0.80,0.93] | 1596 | 35.8% | 90.2% | 0.868 | +0.0344 | [+0.020, +0.048] | +0.0371 | +59.3 |
| [0.80,0.97] | 2224 | 49.9% | 92.0% | 0.893 | +0.0274 | [+0.016, +0.038] | +0.0286 | +63.6 |

## The 2D grid — RAW (no chop gate)

Robustness view without the live gate. Same grid, raw universe.

**Win−price MARGIN (raw)** (rows = buy_p floor, cols = sell_p ceiling)

| floor \ ceiling | 0.85 | 0.88 | 0.90 | 0.93 | 0.97 |
|---:|---:|---:|---:|---:|---:|
| **0.70** | +0.0375 | +0.0362 | +0.0355 | +0.0355 | +0.0324 |
| **0.74** | +0.0378 | +0.0355 | +0.0342 | +0.0345 | +0.0300 |
| **0.76** | +0.0403 | +0.0368 | +0.0351 | +0.0353 | +0.0299 |
| **0.78** | +0.0437 | +0.0384 | **+0.0360** | +0.0361 | +0.0298 |
| **0.80** | +0.0353 | +0.0313 | +0.0299 | +0.0314 | +0.0256 |

**Total \$ @ \$1/trade (raw)** (rows = buy_p floor, cols = sell_p ceiling)

| floor \ ceiling | 0.85 | 0.88 | 0.90 | 0.93 | 0.97 |
|---:|---:|---:|---:|---:|---:|
| **0.70** | +169.8 | +177.6 | +183.8 | +198.3 | +203.2 |
| **0.74** | +81.5 | +89.2 | +95.5 | +110.0 | +114.8 |
| **0.76** | +64.4 | +72.1 | +78.4 | +92.9 | +97.7 |
| **0.78** | +51.1 | +58.8 | **+65.0** | +79.5 | +84.4 |
| **0.80** | +29.1 | +36.8 | +43.1 | +57.5 | +62.4 |

## The 2D grid — Binance preabsmove chop proxy (FULL coverage)

The deployed tick-spot gate only covers the older box; the same-mechanism Binance gate (round-open→entry `|net|/open ≥ 0.00056`, look-ahead-controlled via `phase4.spot_path_feats`) is the wide-coverage robustness check. Kept 3222/5237 (3222 passed).

**Win−price MARGIN (preabs proxy)** (rows = buy_p floor, cols = sell_p ceiling)

| floor \ ceiling | 0.85 | 0.88 | 0.90 | 0.93 | 0.97 |
|---:|---:|---:|---:|---:|---:|
| **0.70** | +0.0987 | +0.0932 | +0.0885 | +0.0818 | +0.0685 |
| **0.74** | +0.0868 | +0.0810 | +0.0761 | +0.0694 | +0.0555 |
| **0.76** | +0.0809 | +0.0755 | +0.0706 | +0.0643 | +0.0504 |
| **0.78** | +0.0862 | +0.0782 | **+0.0721** | +0.0646 | +0.0495 |
| **0.80** | +0.0596 | +0.0596 | +0.0567 | +0.0526 | +0.0401 |

**Total \$ @ \$1/trade (preabs proxy)** (rows = buy_p floor, cols = sell_p ceiling)

| floor \ ceiling | 0.85 | 0.88 | 0.90 | 0.93 | 0.97 |
|---:|---:|---:|---:|---:|---:|
| **0.70** | +223.5 | +242.2 | +253.1 | +268.2 | +274.1 |
| **0.74** | +113.2 | +131.9 | +142.7 | +157.9 | +163.8 |
| **0.76** | +81.6 | +100.3 | +111.2 | +126.3 | +132.2 |
| **0.78** | +68.4 | +87.1 | **+98.0** | +113.1 | +119.0 |
| **0.80** | +36.0 | +54.7 | +65.6 | +80.8 | +86.6 |

## Placebo + TIME-OOS on the headline cells (chop-gated)

For the deployed cell, the total-\$-optimal cell, the margin-optimal cell, and the deployed cell's grid neighbors: a label-shuffle **placebo** (×2000, one-sided — does the cell's win−price margin beat a price-blind reshuffle of win labels across the whole ≥0.70 favorite universe) and a chronological **TIME-OOS** (early-60% train / late-40% test; **holds** = +margin in BOTH halves). The 8 days are non-stationary across box/config switches, so OOS is decisive.

| cell [floor,ceil] | n | margin | total \$ | placebo p | OOS train margin | OOS test margin | OOS holds? |
|---|---:|---:|---:|---:|---:|---:|:---:|
| **[0.78,0.90] (deployed)** | 1437 | +0.0396 | +64.9 | 0.0005 | +0.053 | +0.020 | **yes** |
| [0.70,0.97] | 4455 | +0.0389 | +210.6 | 1.0000 | +0.046 | +0.028 | **yes** |
| [0.78,0.85] | 873 | +0.0528 | +54.1 | 0.0795 | +0.077 | +0.016 | **yes** |
| [0.76,0.88] | 1479 | +0.0440 | +76.2 | 0.1109 | +0.057 | +0.024 | **yes** |
| [0.76,0.90] | 1725 | +0.0405 | +80.7 | 0.0065 | +0.053 | +0.021 | **yes** |
| [0.76,0.93] | 2110 | +0.0395 | +94.4 | 0.0005 | +0.050 | +0.023 | **yes** |
| [0.78,0.88] | 1191 | +0.0439 | +60.4 | 0.0085 | +0.058 | +0.023 | **yes** |
| [0.78,0.93] | 1822 | +0.0387 | +78.5 | 0.0005 | +0.050 | +0.022 | **yes** |
| [0.80,0.88] | 965 | +0.0380 | +41.1 | 0.0055 | +0.048 | +0.023 | **yes** |
| [0.80,0.90] | 1211 | +0.0341 | +45.6 | 0.0005 | +0.044 | +0.019 | **yes** |
| [0.80,0.93] | 1596 | +0.0344 | +59.3 | 0.0005 | +0.043 | +0.022 | **yes** |

## Real-ledger cross-check (the binding evidence)

Realized win-rate / win−price margin by **real LIVE fill** price bucket (`archive/ledger.db`, FILL→SETTLE joined by trade_id). The floor zone is split into `[0.70,0.78)` (the lower band of the floor sweep) so the buy_p decision has its own real-money evidence. This is what caught the phase-7 **paper-vs-real reversal** (>0.90 is +EV on paper but **−0.0485 in real money**). Where paper and real DISAGREE, **real wins**.

| real fill bucket | n | win% | mean price | win−price margin |
|---|---:|---:|---:|---:|
| [0.70,0.78) | 383 | 71.8% | 0.752 | **-0.0342** |
| [0.78,0.85) | 291 | 86.6% | 0.818 | **+0.0479** |
| [0.85,0.90) | 151 | 92.7% | 0.879 | **+0.0478** |
| [0.90,0.93) | 107 | 86.9% | 0.918 | **-0.0485** |
| >=0.93 | 37 | 100.0% | 0.945 | **+0.0548** |
| <0.70 (out of scope) | 71 | 54.9% | 0.660 | -0.1110 |

- **Real margin over the DEPLOYED [0.78, 0.90] span** (pooling overlapping real buckets): **+0.0478** (n=442).
- **Real margin over the total-\$-optimal cell [0.70, 0.97] span:** +0.0050 (n=969).

**Reading of the real buckets.** `[0.78,0.85)` and `[0.85,0.90)` are clearly +margin in REAL money — the core of the deployed band is real-money-good. `[0.70,0.78)` is the floor question: see its real margin above (a negative real margin there argues AGAINST lowering buy_p below 0.78). `[0.90,0.93)` is the phase-7 trap — paper-positive, real-NEGATIVE — which is why ceilings that reach into it are rejected on real-money grounds.

## Decision logic

Is **[0.78, 0.90]** near-optimal? A challenger cell only DISPLACES the deployed band if it is **materially** richer in realized total \$ (≥ \$15 over the 8 days) AND survives the full discipline: paper margin +ve, **placebo p<0.05**, **OOS holds**, AND the **real-ledger margin over its price span is non-negative**. The real ledger is the decisive tiebreaker — idealized paper flatters the >0.90 tail (paper-+EV but −0.0485 real). 'Near-optimal' = the deployed cell is itself disciplined and within a small \$ band of the best disciplined cell.

- DEPLOYED [0.78,0.90]: paper margin +0.0396 (n=1437, total $+64.9); placebo p=0.0005; OOS HOLDS (tr +0.053 / te +0.020); real core-span margin +0.0478 (n=442) OK.
- No grid cell is simultaneously (a) paper +margin, (b) placebo p<0.05, (c) OOS-holding, (d) every ADDED real bucket non-negative, AND materially (≥$15/8d) richer in realized total$ than deployed. Cells that score higher on raw paper total$ get there by VOLUME — extending the ceiling into the >0.90 zone or the floor into [0.70,0.78), both of which the MARGINAL real test rejects.
- REJECTED on real money: [0.76,0.93] paper total $+94.4 (+$29.5 vs dep) — added bucket(s) lose real money: [0.70,0.78) real -0.0342 (n=383); [0.90,0.93) real -0.0485 (n=107).
- REJECTED on real money: [0.76,0.90] paper total $+80.7 (+$15.9 vs dep) — added bucket(s) lose real money: [0.70,0.78) real -0.0342 (n=383).

## Verdict

### [0.78, 0.90] IS near-optimal — KEEP the deployed band

The deployed band **[buy_p=0.78, sell_p=0.90]** is near-optimal. No grid cell is simultaneously paper-+margin, placebo-significant, OOS-holding, real-ledger non-negative, AND materially richer in realized total \$. Cells that score higher on raw paper total \$ do so by extending the ceiling into the **>0.90 zone that real money loses on** (the phase-7 reversal, real [0.90,0.93) margin ≈ −0.048), so they are rejected by the binding real-ledger gate. Lowering buy_p below 0.78 pulls in the cheaper `[0.70,0.78)` favorites whose real-money margin does not justify the added adverse-selection. **Keep [0.78, 0.90].**

## Honest caveats

- **Paper labels = market winner; fills idealized.** Slippage/partial-fill is NOT modeled in the paper grid (COST=0.003 cushion only). The real-ledger cross-check is what disciplines the high-price tail, and it OVERRIDES paper on disagreement.
- **Non-stationarity.** 8 days cross box/config switches (floor 0.70→0.76→0.78, the sell_p 0.93→0.85→0.90 history, box moves). TIME-OOS is weighted heavily; a cell that is +margin in-sample but flips in the late test half is rejected.
- **The >0.90 paper trap.** Raw paper total \$ keeps rising as the ceiling extends to 0.93/0.97 because high-priced favorites win often — but you pay nearly the full dollar, the margin is thin, and **real money goes negative there** (adverse selection / thin offer). Do not chase paper total \$ into that zone.
- **Floor (buy_p) below 0.78.** Lower floors add the `[0.70,0.78)` favorites; judge them on the real `[0.70,0.78)` bucket margin above, not the paper grid alone.
- **Deployed chop gate has partial tick-spot coverage** (older box only); the preabsmove proxy grid is the wide-coverage cross-check on the same mechanism.
- **Trivially reversible.** Any change is two numbers (`buy_p`, `sell_p`) in `portfolio.json`.

