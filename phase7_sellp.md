# Phase-7 — Sell_p Ceiling Sweep: should we raise the favorite price ceiling?

**Recommendation: RAISE sell_p 0.85 -> 0.90.**  One-line `polybot/portfolio.json` change to `sell_p` (trivially reversible).

## Question & setup

The live real-money config (`polybot/portfolio.json`, `fav_hold`) trades favorites in **[buy_p=0.78, sell_p=0.85]** + a chop gate. A large share of favorites are priced **>0.85** and get cut by the current ceiling. Phase-2's real-fill bucket table and Phase-4 said the **0.85–0.90 zone is the best part of the book** in win-minus-PRICE terms, while **>0.90 dilutes or goes negative**. We lowered sell_p 0.93→0.85 earlier on an outdated rationale, so this phase VERIFIES (does not assume) where to put the ceiling.

- Archive: `archive/master_polybot.db`, span **2026-06-22 14:48:07.711826 .. 2026-06-30 06:58:11.075856 UTC**.
- Usable favorites (first tick rem≤150 with a side ≥0.78, capped ≤0.97): **5147/6279** (dropped no_winner=0, no_market_end=0, no_coin=343, no_entry=789).
- By coin: {'btc': 1445, 'sol': 1337, 'xrp': 1061, 'eth': 1304}.
- **Base favorite(≥0.78) win-rate = 0.865.**
- **Favorites priced >0.85 (cut by current sell_p): 1835/5147 = 35.7%** — confirms a large strong-favorite zone is currently skipped.

**Entry (no look-ahead).** First ticks row with `rem≤150` where a favorite side (YES ask=`ask_p1`, NO ask=`1−bid_p1`) is ≥0.78. Side + entry price taken there. A favorite already >0.85 at that first qualifying tick is **entered at that price** under a higher ceiling (not skipped). Label = 1 if side==winner. Reuses `phase4_features` reconstruction/stat machinery (imported, **not** modified).

**EV convention.** Per $1 staked on a 1-share favorite buy at price *p* paying $1 on win: `EV/$1 = win%·(1/p) − 1 − COST`, COST=0.003 (fee 0.001 + slippage 0.002). The price-comparable **win−price margin** (`win% − mean price`) is the primary decision metric; total $ is at $1/trade.

## Ceiling sweep — NO chop gate

| sell_p X | n | fire% | win% | mean price | win−price margin | margin 95% CI | EV/$1 (cost-adj) | total $ @ $1/trade |
|---:|---:|---:|---:|---:|---:|:---:|---:|---:|
| **0.85** | 3312 | 64.3% | 82.6% | 0.802 | **+0.0240** | [+0.011, +0.037] | +0.0269 | +89.3 |
| **0.88** | 3744 | 72.7% | 83.3% | 0.810 | **+0.0229** | [+0.011, +0.035] | +0.0254 | +95.1 |
| **0.90** | 4050 | 78.7% | 84.0% | 0.816 | **+0.0234** | [+0.012, +0.035] | +0.0257 | +104.2 |
| **0.93** | 4487 | 87.2% | 85.0% | 0.826 | **+0.0242** | [+0.014, +0.035] | +0.0263 | +118.0 |
| **0.97** | 5147 | 100.0% | 86.5% | 0.843 | **+0.0225** | [+0.013, +0.032] | +0.0241 | +123.8 |

## Marginal slices — what each ceiling-raise ADDS (NO gate)

This is the decisive view: each ceiling raise only adds the **new** price slice.

| marginal slice | n | win% | mean price | win−price margin | margin 95% CI | EV/$1 |
|---|---:|---:|---:|---:|:---:|---:|
| **(0.85, 0.88]** | 432 | 88.4% | 0.870 | **+0.0144** | [-0.016, +0.044] | +0.0135 |
| **(0.88, 0.90]** | 306 | 92.5% | 0.896 | **+0.0293** | [-0.000, +0.056] | +0.0299 |
| **(0.90, 0.93]** | 437 | 95.2% | 0.920 | **+0.0317** | [+0.011, +0.050] | +0.0316 |
| **(0.93, 0.97]** | 660 | 96.7% | 0.955 | **+0.0112** | [-0.004, +0.024] | +0.0087 |

## Ceiling sweep — WITH the deployed chop gate (the LIVE config)

Deployed gate (`polybot/strategies.py::_not_chop`): keep iff `|spot−strike|/strike ≥ 0.00056` at the entry tick; **NO-OP keep** when the tick had no spot/strike (older box only carries it). Coverage: 1451 favorites had tick spot/strike, 761 passed; kept 4457/5147 (rest NO-OP kept). This is the live config, so it is the primary basis for the decision.

| sell_p X | n | fire% | win% | mean price | win−price margin | margin 95% CI | EV/$1 (cost-adj) | total $ @ $1/trade |
|---:|---:|---:|---:|---:|---:|:---:|---:|---:|
| **0.85** | 2699 | 60.6% | 83.7% | 0.802 | **+0.0350** | [+0.022, +0.048] | +0.0406 | +109.5 |
| **0.88** | 3105 | 69.7% | 84.3% | 0.811 | **+0.0316** | [+0.019, +0.045] | +0.0363 | +112.6 |
| **0.90** | 3387 | 76.0% | 84.9% | 0.818 | **+0.0312** | [+0.019, +0.043] | +0.0355 | +120.1 |
| **0.93** | 3810 | 85.5% | 86.1% | 0.830 | **+0.0316** | [+0.021, +0.043] | +0.0354 | +134.9 |
| **0.97** | 4457 | 100.0% | 87.6% | 0.848 | **+0.0285** | [+0.019, +0.038] | +0.0314 | +140.1 |

**Marginal slices WITH deployed chop gate:**

| marginal slice | n | win% | mean price | win−price margin | margin 95% CI | EV/$1 |
|---|---:|---:|---:|---:|:---:|---:|
| **(0.85, 0.88]** | 406 | 87.9% | 0.870 | **+0.0094** | [-0.023, +0.039] | +0.0077 |
| **(0.88, 0.90]** | 282 | 92.2% | 0.896 | **+0.0263** | [-0.005, +0.055] | +0.0265 |
| **(0.90, 0.93]** | 423 | 95.5% | 0.920 | **+0.0349** | [+0.015, +0.054] | +0.0350 |
| **(0.93, 0.97]** | 647 | 96.6% | 0.956 | **+0.0104** | [-0.005, +0.024] | +0.0079 |

## Ceiling sweep — WITH Binance preabsmove chop proxy (FULL coverage)

Because the deployed tick-spot gate only covers the older box, we also apply the **same-mechanism** Binance gate (round-open→entry `|net|/open ≥ 0.00056`, look-ahead-controlled via `phase4.spot_path_feats`). Coverage: 5147 favorites had klines, 3415 passed; kept 3415/5147. This is the wide-coverage robustness check on the chop-gated conclusion.

| sell_p X | n | fire% | win% | mean price | win−price margin | margin 95% CI | EV/$1 (cost-adj) | total $ @ $1/trade |
|---:|---:|---:|---:|---:|---:|:---:|---:|---:|
| **0.85** | 1833 | 53.7% | 88.3% | 0.804 | **+0.0788** | [+0.064, +0.094] | +0.0956 | +175.3 |
| **0.88** | 2164 | 63.4% | 89.0% | 0.814 | **+0.0760** | [+0.063, +0.089] | +0.0912 | +197.3 |
| **0.90** | 2409 | 70.5% | 89.6% | 0.823 | **+0.0735** | [+0.062, +0.086] | +0.0875 | +210.7 |
| **0.93** | 2792 | 81.8% | 90.5% | 0.836 | **+0.0690** | [+0.058, +0.079] | +0.0811 | +226.3 |
| **0.97** | 3415 | 100.0% | 91.7% | 0.858 | **+0.0586** | [+0.049, +0.068] | +0.0681 | +232.6 |

**Marginal slices WITH preabsmove proxy:**

| marginal slice | n | win% | mean price | win−price margin | margin 95% CI | EV/$1 |
|---|---:|---:|---:|---:|:---:|---:|
| **(0.85, 0.88]** | 331 | 93.1% | 0.870 | **+0.0605** | [+0.033, +0.087] | +0.0665 |
| **(0.88, 0.90]** | 245 | 94.7% | 0.895 | **+0.0516** | [+0.019, +0.080] | +0.0546 |
| **(0.90, 0.93]** | 383 | 96.1% | 0.921 | **+0.0403** | [+0.020, +0.059] | +0.0409 |
| **(0.93, 0.97]** | 623 | 96.8% | 0.956 | **+0.0124** | [-0.003, +0.026] | +0.0100 |

## Placebo + TIME-OOS on each raise 0.85 → X

Two label-shuffle placebos (×2000, one-sided): **slice-margin** p (does the cumulative (0.85,X] slice beat its own price more than a shuffled label, prices fixed) and **EV-edge** p (does widening the universe [floor,X] add per-$ EV vs the [floor,0.85] base under a price-matched null). OOS = chronological early-60% train / late-40% test; **holds** = the (0.85,X] slice's win−price margin is +ve in BOTH halves (8 days are non-stationary, so OOS is decisive).

**No gate:**

| raise 0.85→X | slice-margin placebo p | EV-edge placebo p | OOS train margin | OOS test margin | OOS holds? |
|---:|---:|---:|---:|---:|:---:|
| 0.88 | 0.1344 | 0.0005 | +0.013 | +0.016 | **yes** |
| 0.90 | 0.0015 | 0.0005 | +0.026 | +0.012 | **yes** |
| 0.93 | 0.0005 | 0.0005 | +0.031 | +0.015 | **yes** |
| 0.97 | 0.0005 | 0.0005 | +0.029 | +0.007 | **yes** |

**WITH deployed chop gate (live config):**

| raise 0.85→X | slice-margin placebo p | EV-edge placebo p | OOS train margin | OOS test margin | OOS holds? |
|---:|---:|---:|---:|---:|:---:|
| 0.88 | 0.4713 | 0.0045 | +0.009 | +0.009 | **yes** |
| 0.90 | 0.0485 | 0.0005 | +0.022 | +0.008 | **yes** |
| 0.93 | 0.0005 | 0.0005 | +0.028 | +0.017 | **yes** |
| 0.97 | 0.0005 | 0.0005 | +0.027 | +0.008 | **yes** |

## Real-ledger cross-check (the brief's requested check)

Realized win-rate / win−price margin by **real LIVE fill** price bucket (`archive/ledger.db`, FILL→SETTLE joined by trade_id). The earlier 0.93/0.96 ceiling means we have actual money on both sides of the 0.85 line:

| real fill bucket | n | win% | mean price | win−price margin |
|---|---:|---:|---:|---:|
| <0.78 | 454 | 69.2% | 0.738 | **-0.0462** |
| [0.78,0.85) | 291 | 86.6% | 0.818 | **+0.0479** |
| [0.85,0.90) | 151 | 92.7% | 0.879 | **+0.0478** |
| [0.90,0.93) | 107 | 86.9% | 0.918 | **-0.0485** |
| >=0.93 | 37 | 100.0% | 0.945 | **+0.0548** |

## Decision logic

Walk the ceiling raises in order; **raise** while the marginal paper slice is +EV **AND** the REAL-ledger bucket covering that zone is non-negative; **stop** at the first step that fails either. The real ledger is the decisive tiebreaker: idealized archive fills flatter the high-price tail (no slippage), so where paper and real DISAGREE, real wins. Every raise also clears the EV-edge placebo (p≤0.005, both gated and ungated) and holds TIME-OOS — those are necessary but not the binding constraint here; the binding constraint is the real-money sign at >0.90.

- raise→0.88: paper slice margin +0.009 (+EV) AND real [0.85,0.90) margin +0.048 (n=151) (non-negative) → RAISE
- raise→0.90: paper slice margin +0.026 (+EV) AND real [0.85,0.90) margin +0.048 (n=151) (non-negative) → RAISE
- raise→0.93: REAL money LOSES here (real [0.90,0.93) margin -0.048 (n=107)) → STOP

## Recommendation

### RAISE sell_p 0.85 -> 0.90

**One-line config change** in `polybot/portfolio.json` (`strategies[0].params.sell_p`):

```json
"sell_p": 0.85   ->   "sell_p": 0.90
```

**Expected effect (live config, with chop gate):** fire-rate 60.6% → 76.0% (**+15.4pp**, ~1.25× the trades); win−price margin +0.0350 → +0.0312; EV/$1 +0.0406 → +0.0355; total $ @ $1/trade +109.5 → +120.1. Per-trade margin dips slightly (you add genuinely-good but slightly-cheaper-edge strong favorites) while total realized $ RISES. The added (0.85,0.90] zone is +EV in BOTH paper AND real money (real [0.85,0.90) margin +0.048); the next zone up (0.90,0.93] is +EV on paper but **−0.048 in real money** (slippage/adverse-selection on the thin >0.90 offer) — so stop at 0.90.

## Honest caveats

- **Paper labels = market winner; fills idealized.** Strong favorites >0.85 sit on liquid books so fills are realistic, but slippage/partial-fill is NOT modeled here (engine cushion COST=0.003 only). The real-ledger cross-check partly covers this — and it confirms the archive thesis.
- **Non-stationarity.** 8 days cross box/config switches; the TIME-OOS is weighted heavily and is the decisive test. A slice that is +EV in-sample but flips in the late test half is rejected.
- **Deployed chop gate has partial tick-spot coverage** (older box only); the preabsmove proxy is the wide-coverage cross-check on the same mechanism.
- **>0.90 small-sample luck.** The >0.93 bucket can show a high win-rate on tiny n (a 100%-win streak of ~37 fills); do not over-read it — its margin is dominated by the price you pay, and its CI is wide.
- **It is one line, trivially reversible.** `sell_p` in `portfolio.json`.

