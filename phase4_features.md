# Phase-4 Feature Diagnostic — entry-time predictors of a favorite WIN

**What this is.** A disciplined, single-feature SCREEN (no model) over the FULL archive, asking: which entry-time-observable features actually predict whether the favorite side wins its 5-min round? Every feature is judged by AUC, a label-shuffle PLACEBO (2000 shuffles → empirical p), and a chronological TIME-OOS split (train = earliest 60%, test = latest 40%). A feature is **REAL only if placebo p<0.05 AND its bucket direction holds in the late OOS half.** Everything else is noise — and the prior is that *most* features are noise (we have killed nearly every filter this way).

## Dataset

- Archive: `archive/master_polybot.db`, span **2026-06-22 14:48:07.711826 .. 2026-06-30 06:58:11.075856 UTC** (~8 days).
- Usable rounds: **4009/6279** (dropped: no_winner=0, no_market_end=0, no_coin_attribution=343, no_entry_tick_in_band=1927).
- By coin: {'btc': 1164, 'sol': 1011, 'eth': 1027, 'xrp': 807}.
- **Base favorite win-rate = 0.830**; mean entry price = 0.807 → baseline win-minus-price margin = **+0.024** (the favorite barely beats / roughly matches its own line on average — this is the bar any feature must improve on).

**Entry unit.** Entry tick = first ticks row with `rem<=150` where a favorite side (YES ask=`ask_p1`, or NO ask=`1-bid_p1`) is in [0.78,0.85]; the favorite side + entry price are taken there; label = 1 if that side == winner. All features use ONLY data up to that tick (no look-ahead — the cardinal rule).

> **Non-stationarity caveat.** The 8-day span crosses box switches and config changes (favorite floor 0.70→0.76→0.78, flat experiments). Win-rate is not stationary, which is exactly why the TIME-OOS split is decisive: a feature that shines in the early train half but flips/fades in the late test half is an artifact of the regime, not a real edge.

## Ranked table

Power = |AUC−0.5| (rank-based, robust). OOS = does the train-derived bucket direction (and AUC sign) survive on the held-out late 40%?

| rank | feature | group | coverage | AUC | power | placebo p | TIME-OOS holds? | regime captured | verdict |
|----:|---------|-------|---------:|----:|------:|----------:|:---------------:|-----------------|---------|
| 1 | `net_rel` | spot-path | 4009 | 0.707 | 0.207 | 0.000 | **yes** | Signed spot move TOWARD the favorite side up to entry (already-in-the-money drift) | **REAL** |
| 2 | `absmove` | spot-path | 4009 | 0.659 | 0.159 | 0.000 | **yes** | Absolute spot move magnitude up to entry (big move vs flat) | **REAL** |
| 3 | `eff_ratio` | spot-path | 4009 | 0.646 | 0.146 | 0.000 | **yes** | Binance efficiency ratio |net|/sum|per-min| (trend vs chop) up to entry | **REAL** |
| 4 | `btc_net_rel` | cross-coin | 3583 | 0.629 | 0.129 | 0.000 | **yes** | BTC's pre-entry net move toward THIS round's favorite side | **REAL** |
| 5 | `dir_changes` | spot-path | 4009 | 0.443 | 0.057 | 0.000 | **yes** | Number of per-min direction flips up to entry (choppiness) | **REAL** |
| 6 | `entry_price` | book | 4009 | 0.553 | 0.053 | 0.000 | **yes** | The favorite entry price itself within [0.78,0.85] (cheaper vs richer fav) | **REAL** |
| 7 | `spot_range` | spot-path | 4009 | 0.543 | 0.043 | 0.001 | **yes** | Spot high-low range up to entry (intrabar churn) | **REAL** |
| 8 | `regime_overall` | regime | 3989 | 0.540 | 0.040 | 0.001 | **yes** | Win-rate of last 20 rounds overall BEFORE this one (hot/cold streak) | **REAL** |
| 9 | `price_traj` | book | 4009 | 0.462 | 0.038 | 0.003 | **yes** | Favorite price RISING(+)/FALLING(-) into the band over last ~5 ticks | **REAL** |
| 10 | `secs_to_settle` | time | 4009 | 0.534 | 0.034 | 0.004 | **yes** | Seconds-to-settle at entry (how late we caught the band) | **REAL** |
| 11 | `regime_coin` | regime | 3969 | 0.530 | 0.030 | 0.019 | **yes** | Win-rate of last N rounds for THIS coin BEFORE this one (per-coin streak) | **REAL** |
| 12 | `btc_agree` | cross-coin | 3583 | 0.567 | 0.067 | 0.000 | no | Does this coin's pre-entry direction agree with BTC's that round (BTC leads) | **noise (fails OOS)** |
| 13 | `spot_strike_fav` | tick-spot | 1170 | 0.537 | 0.037 | 0.073 | **yes** | Signed spot-vs-strike toward favorite at entry tick (older-box only) | **noise (fails placebo)** |
| 14 | `chop_gate_dist` | tick-spot | 1170 | 0.537 | 0.037 | 0.076 | **yes** | Deployed chop gate |spot-strike|/strike at entry tick (older-box only) | **noise (fails placebo)** |
| 15 | `utc_hour` | time | 4009 | 0.530 | 0.030 | 0.015 | no | UTC hour-of-day at settle (session/regime time-of-day) | **noise (fails OOS)** |
| 16 | `fav_imbalance` | book | 4009 | 0.508 | 0.008 | 0.528 | no | Book size imbalance toward the favorite side at entry | **noise** |
| 17 | `l1_depth` | book | 4009 | 0.506 | 0.006 | 0.638 | no | L1 bid+ask size at entry (book thickness) | **noise** |
| 18 | `spread` | book | 4009 | 0.497 | 0.003 | 0.786 | no | YES book bid-ask spread at entry (liquidity/uncertainty) | **noise** |
| 19 | `rvol` | spot-path | 4009 | 0.502 | 0.002 | 0.894 | no | Realized per-min vol of spot up to entry (volatile vs calm) | **noise** |

## Survivors

**11 feature(s) survive placebo AND TIME-OOS:**

### `net_rel` (spot-path) — REAL

- Signed spot move TOWARD the favorite side up to entry (already-in-the-money drift)
- AUC 0.707 (power 0.207), placebo p=0.000.
- In-sample quintiles (win% | n | win-minus-price margin): Q1:64%(n802,m-0.166) Q2:82%(n802,m+0.020) Q3:87%(n802,m+0.066) Q4:88%(n802,m+0.070) Q5:94%(n801,m+0.128)
- TIME-OOS: train AUC 0.715 (top−bottom win spread +0.311) → test AUC 0.698 (spread +0.314); direction **holds** on n_test=1604.

### `absmove` (spot-path) — REAL

- Absolute spot move magnitude up to entry (big move vs flat)
- AUC 0.659 (power 0.159), placebo p=0.000.
- In-sample quintiles (win% | n | win-minus-price margin): Q1:70%(n802,m-0.106) Q2:80%(n802,m-0.003) Q3:86%(n802,m+0.047) Q4:87%(n802,m+0.064) Q5:93%(n801,m+0.117)
- TIME-OOS: train AUC 0.658 (top−bottom win spread +0.224) → test AUC 0.661 (spread +0.248); direction **holds** on n_test=1604.

### `eff_ratio` (spot-path) — REAL

- Binance efficiency ratio |net|/sum|per-min| (trend vs chop) up to entry
- AUC 0.646 (power 0.146), placebo p=0.000.
- In-sample quintiles (win% | n | win-minus-price margin): Q1:69%(n802,m-0.113) Q2:81%(n802,m+0.004) Q3:87%(n815,m+0.058) Q4:88%(n789,m+0.071) Q5:91%(n801,m+0.098)
- TIME-OOS: train AUC 0.664 (top−bottom win spread +0.228) → test AUC 0.623 (spread +0.199); direction **holds** on n_test=1604.

### `btc_net_rel` (cross-coin) — REAL

- BTC's pre-entry net move toward THIS round's favorite side
- AUC 0.629 (power 0.129), placebo p=0.000.
- In-sample quintiles (win% | n | win-minus-price margin): Q1:69%(n718,m-0.110) Q2:84%(n716,m+0.034) Q3:83%(n716,m+0.021) Q4:86%(n719,m+0.050) Q5:89%(n714,m+0.087)
- TIME-OOS: train AUC 0.650 (top−bottom win spread +0.234) → test AUC 0.595 (spread +0.206); direction **holds** on n_test=1434.

### `dir_changes` (spot-path) — REAL

- Number of per-min direction flips up to entry (choppiness)
- AUC 0.443 (power 0.057), placebo p=0.000.
- In-sample quintiles (win% | n | win-minus-price margin): Q1:84%(n2443,m+0.036) Q4:82%(n1208,m+0.017) Q5:77%(n358,m-0.038)
- TIME-OOS: train AUC 0.437 (top−bottom win spread -0.078) → test AUC 0.450 (spread -0.080); direction **holds** on n_test=1604.

### `entry_price` (book) — REAL

- The favorite entry price itself within [0.78,0.85] (cheaper vs richer fav)
- AUC 0.553 (power 0.053), placebo p=0.000.
- In-sample quintiles (win% | n | win-minus-price margin): Q1:81%(n1026,m+0.035) Q2:80%(n667,m+0.011) Q3:81%(n909,m+0.005) Q4:85%(n652,m+0.025) Q5:89%(n755,m+0.042)
- TIME-OOS: train AUC 0.554 (top−bottom win spread +0.065) → test AUC 0.553 (spread +0.086); direction **holds** on n_test=1604.

### `spot_range` (spot-path) — REAL

- Spot high-low range up to entry (intrabar churn)
- AUC 0.543 (power 0.043), placebo p=0.001.
- In-sample quintiles (win% | n | win-minus-price margin): Q1:79%(n802,m-0.008) Q2:83%(n802,m+0.029) Q3:83%(n802,m+0.020) Q4:83%(n802,m+0.023) Q5:86%(n801,m+0.054)
- TIME-OOS: train AUC 0.535 (top−bottom win spread +0.068) → test AUC 0.553 (spread +0.077); direction **holds** on n_test=1604.

### `regime_overall` (regime) — REAL

- Win-rate of last 20 rounds overall BEFORE this one (hot/cold streak)
- AUC 0.540 (power 0.040), placebo p=0.001.
- In-sample quintiles (win% | n | win-minus-price margin): Q1:81%(n1142,m+0.002) Q2:81%(n538,m+0.001) Q3:84%(n786,m+0.031) Q4:85%(n808,m+0.048) Q5:85%(n715,m+0.043)
- TIME-OOS: train AUC 0.539 (top−bottom win spread +0.037) → test AUC 0.537 (spread +0.041); direction **holds** on n_test=1596.

### `price_traj` (book) — REAL

- Favorite price RISING(+)/FALLING(-) into the band over last ~5 ticks
- AUC 0.462 (power 0.038), placebo p=0.003.
- In-sample quintiles (win% | n | win-minus-price margin): Q1:87%(n822,m+0.046) Q2:83%(n837,m+0.026) Q3:82%(n829,m+0.023) Q4:82%(n746,m+0.023) Q5:81%(n775,m-0.000)
- TIME-OOS: train AUC 0.441 (top−bottom win spread -0.086) → test AUC 0.489 (spread -0.021); direction **holds** on n_test=1604.

### `secs_to_settle` (time) — REAL

- Seconds-to-settle at entry (how late we caught the band)
- AUC 0.534 (power 0.034), placebo p=0.004.
- In-sample quintiles (win% | n | win-minus-price margin): Q1:83%(n802,m+0.023) Q2:79%(n802,m-0.012) Q3:84%(n802,m+0.036) Q4:82%(n802,m+0.017) Q5:87%(n801,m+0.054)
- TIME-OOS: train AUC 0.554 (top−bottom win spread +0.062) → test AUC 0.508 (spread +0.029); direction **holds** on n_test=1604.

### `regime_coin` (regime) — REAL

- Win-rate of last N rounds for THIS coin BEFORE this one (per-coin streak)
- AUC 0.530 (power 0.030), placebo p=0.019.
- In-sample quintiles (win% | n | win-minus-price margin): Q1:80%(n940,m-0.012) Q2:84%(n770,m+0.031) Q3:85%(n922,m+0.042) Q4:83%(n722,m+0.026) Q5:84%(n615,m+0.037)
- TIME-OOS: train AUC 0.545 (top−bottom win spread +0.067) → test AUC 0.502 (spread +0.007); direction **holds** on n_test=1588.

## Critical synthesis — the 11 'survivors' collapse to ~3 axes (and one caveat)

A naive read says *eleven* features survive. That over-counts. Pairwise correlation (computed separately, pairwise-complete) shows the survivors are mostly the SAME thing measured differently. **Read the count as ~3 independent axes, not 11.**

**Axis 1 — "spot has already moved toward the favorite" (the dominant signal).** `net_rel`, `absmove`, `spot_range`, `btc_net_rel` are one cluster (corr `net_rel`↔`absmove` = **+0.94**, ↔`spot_range` +0.65/+0.75, ↔`btc_net_rel` +0.55/+0.59). They all encode: at entry, has the 1-min Binance spot path already drifted in the direction the favorite needs? This is the strongest result (AUC up to 0.71) — but its power is concentrated in a RARE divergence regime, not spread evenly:

  - In **~94%** of rounds the book-favorite *is* the side spot has already moved toward (book and spot agree) → win rate **~87%** (train) / **~85%** (test).
  - In the **~6%** of rounds where pre-entry spot moved AGAINST the book-favorite (`net_rel<0`), win rate **collapses to ~34% (train, n=154) / ~29% (test, n=84)**. That divergence bucket is the actionable, OOS-stable edge: *when the order book calls a favorite but the spot path disagrees, the book is usually wrong.* This is the single most useful finding for a dynamic model.
  - **Honest framing:** `net_rel`'s high AUC is partly mechanical — the favorite side is itself chosen from the book, which co-moves with spot — so most of the AUC just restates "the pre-entry leader usually wins." That is still an entry-time-observable, no-look-ahead signal (all bars are round-open→entry), but the *exploitable* part is the rare disagreement, not the common agreement.

**Axis 2 — trend vs chop of the pre-entry path (your PRIOR, confirmed).** `eff_ratio` (|net|/sum|per-min|) and `dir_changes` are one axis (corr **−0.57**). High efficiency / few direction flips → favorite wins more; choppy reversal paths → favorite loses more. This matches the prior (WIN rounds efficiency≈0.48, LOSS≈0.27) and is the same thing the deployed static chop gate (`|spot-strike|/strike < 0.00056`) gropes at with a single threshold. It survives placebo+OOS. **Note it is partly redundant with Axis 1** (eff_ratio↔net_rel +0.40): a clean trending move is both efficient AND toward the favorite.

**Axis 3 — weak, genuinely-independent stragglers (corr ~0 with Axes 1–2).** `entry_price`, `price_traj`, `regime_overall`, `regime_coin`, `secs_to_settle`. These pass the bar but their power is small (AUC 0.53–0.55) and each comes with a catch:

  - **`entry_price` is market efficiency, NOT alpha.** Richer favorites win more (0.78→81% … 0.83–0.85→89%) — but that is the *line being correctly priced*; the win-minus-price margin barely moves. It predicts the label without giving you an edge over the price you pay. Do not mistake it for a tradable signal.
  - **`regime_overall`/`regime_coin` mostly track the non-stationary win-rate drift.** The quintile spread is thin (~80%→85%) and "holds OOS" largely because a drifting win-rate is autocorrelated. Treat hot/cold-streak persistence as weak and regime-contaminated, not a robust lever.
  - **`price_traj` / `secs_to_settle`** are small effects (a favorite still *rising* into the band, or caught slightly later, wins marginally more) — keep as minor conditioning features at most.

**Pairwise-complete correlation among survivors** (|c|>0.5 ⇒ treat as the same signal):

| | `net_rel` | `absmove` | `spot_range` | `btc_net_rel` | `eff_ratio` | `dir_changes` | `entry_price` | `price_traj` | `regime_overall` |
|---|---|---|---|---|---|---|---|---|---|
| `net_rel` | +1.00 | +0.94 | +0.65 | +0.59 | +0.40 | -0.18 | +0.13 | -0.08 | +0.03 |
| `absmove` | +0.94 | +1.00 | +0.75 | +0.55 | +0.38 | -0.16 | +0.13 | -0.07 | +0.02 |
| `spot_range` | +0.65 | +0.75 | +1.00 | +0.39 | -0.06 | -0.02 | +0.12 | -0.08 | +0.03 |
| `btc_net_rel` | +0.59 | +0.55 | +0.39 | +1.00 | +0.25 | -0.13 | +0.10 | -0.07 | +0.02 |
| `eff_ratio` | +0.40 | +0.38 | -0.06 | +0.25 | +1.00 | -0.57 | +0.08 | -0.15 | +0.01 |
| `dir_changes` | -0.18 | -0.16 | -0.02 | -0.13 | -0.57 | +1.00 | -0.09 | +0.24 | -0.00 |
| `entry_price` | +0.13 | +0.13 | +0.12 | +0.10 | +0.08 | -0.09 | +1.00 | -0.15 | -0.02 |
| `price_traj` | -0.08 | -0.07 | -0.08 | -0.07 | -0.15 | +0.24 | -0.15 | +1.00 | +0.01 |
| `regime_overall` | +0.03 | +0.02 | +0.03 | +0.02 | +0.01 | -0.00 | -0.02 | +0.01 | +1.00 |

## Recommendation

**Build a future dynamic / regime-aware model on at most 2–3 axes, not 11 features:**

1. **Spot-vs-book divergence (Axis 1)** — the signed pre-entry spot move toward the favorite (`net_rel`, with `btc_net_rel` as a cross-coin confirmation). The model's biggest lever is the rare divergence bucket (skip / fade when spot contradicts the book-favorite). This is new relative to the deployed gate and OOS-stable.
2. **Pre-entry trend-vs-chop (Axis 2)** — `eff_ratio` (equivalently `dir_changes`). This is the principled generalization of the existing static chop gate; a dynamic threshold here is well-motivated.
3. *(Optional, weak)* a single regime/time conditioner — but expect little and watch for non-stationarity contamination.

**Drop everything else.** `rvol`, `spread`, `l1_depth`, `fav_imbalance`, `utc_hour`, `btc_agree`, and the tick-spot chop features (`chop_gate_dist`, `spot_strike_fav`) are noise here — book microstructure and hour-of-day died to placebo (consistent with every prior filter study), and the tick-spot chop gate did **not** clear placebo on the older-box subset (p≈0.07), so even the deployed gate is, on this archive, marginal at best.

**Bottom line:** the only signals worth a dynamic model are the **pre-entry spot path** — its *direction relative to the favorite* (Axis 1, dominant) and its *trend-vs-chop quality* (Axis 2, = the chop gate done right). Everything else is noise or restated market efficiency.

## Honest caveats

- **Non-stationarity dominates.** 8 days, multiple box/config regimes; the TIME-OOS is the right test and it is unforgiving by design.
- **Paper, not real money.** Labels come from the archived market winner and the reconstructed favorite side, not realized fills; entry-price slippage and fill probability are not modeled here.
- **Single-feature screen.** This is feature SELECTION, not a joint model. Interactions are not tested; a feature that is individually noise could still matter conditionally (but that is a much higher bar and not what we screened).
- **Tick-spot features (`chop_gate_dist`, `spot_strike_fav`) cover only the older box** (~the markets carrying non-zero tick spot/strike); their coverage is smaller and their split spans fewer regimes — read their OOS with extra caution.
- **Look-ahead control.** Spot-path features use only round-open→entry-bar klines; book/regime features use only ticks/rounds at or before entry. The decisive post-entry move is, correctly, invisible to every feature.

