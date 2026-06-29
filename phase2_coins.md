# Phase 2 -- Coin-Allocation Tests (on the validated real-money harness)

**Script:** [`phase2_coins.py`](phase2_coins.py) (imports `backtest_real.py`, does not modify it).
**Basis:** 1150 settled LIVE fills, artifacts excluded -- identical to the Phase-1
baseline (`$-17.33`, 80% win; btc -11.5, eth +0.6, sol +13.3, xrp -19.7).
**Bars (all must pass):** in-sample edge > 0 **and** placebo p < 0.05 **and** chronological
OOS test_edge > 0 (train 60% earliest / test 40% latest by ts).

## Headline

| # | Change | in-sample edge/trade | placebo p | OOS test edge/trade | Verdict |
|---|--------|---------------------:|----------:|--------------------:|---------|
| 1 | **drop xrp** | +0.0177 | **0.19** (0.26 @1000) | +0.0155 | **NOISE** |
| 3 | **ex-ante depth tilt** (keep deep books) | +0.0368 train | 0.31 | **-0.0314 (flips!)** | **NOISE** |
| 4 | **thin-book gate** (drop<p25 depth, all coins) | +0.0186 | **0.072** (0.072 @1000) | +0.0159 | **NOISE (borderline)** |

**No coin-allocation change beats placebo AND holds OOS. Recommendation: HOLD EQUAL WEIGHT.**

## Test 1 -- Drop xrp (re-confirmed)
Re-confirms Phase 1. In-sample drop-xrp looks good ($-17.33 -> +$2.37) and the edge is
even **positive and stable out-of-sample** (train +0.019, test +0.016) -- but the placebo
says **p=0.19** (0.26 at 1000 seeds): removing xrp is statistically indistinguishable from
removing a random same-size subset of trades. The OOS "survival" is illusory because xrp is
*persistently* the loss-tail leader (test-window: xrp -5.9, btc -4.7 vs sol +5.8, eth +3.7),
so dropping it always helps in-sample-style accounting -- but that is selection on coin
identity, which the placebo correctly refuses to credit as a per-trade edge. **Stopping xrp
live was conservative, not edge-justified.**

## Test 2 -- Per-coin margins (the crux)
Bootstrap 95% CI on pnl/trade. **Every coin's CI crosses zero, and 0/4 coins are
distinguishable from the pooled other three.** The apparent ranking (sol +0.044 > eth +0.002
> btc -0.040 > xrp -0.074) is entirely inside sampling noise.

| coin | n | win% | avg entry | pnl/trade | 95% CI | crosses 0? | vs pooled-rest distinguishable? |
|------|--:|-----:|----------:|----------:|--------|:----------:|:-------------------------------:|
| btc | 288 | 78.5 | 0.803 | -0.040 | [-0.140, +0.056] | yes | no |
| eth | 295 | 81.0 | 0.806 | +0.002 | [-0.087, +0.092] | yes | no |
| sol | 302 | 82.5 | 0.803 | +0.044 | [-0.046, +0.131] | yes | no |
| xrp | 265 | 77.7 | 0.813 | -0.074 | [-0.178, +0.027] | yes | no |

At ~0.80 entry each trade is a +0.2/-0.8 lottery; ~280 trades give CIs ~±0.10 wide, swamping
the ~±0.06 spread between coins. **Per-coin differences are sampling noise.**

## Test 3 -- Ex-ante "sol-tilt" (honest version)
Tilt by a metric knowable BEFORE the trade: median early-round (rem 180-300s) L1 book depth.
This cleanly recovers the structural fact (**btc 82.8, eth 20.0, sol 15.0, xrp 6.8** -- xrp
thinnest, matches the brief). But weighting toward deep-book rounds **flips sign OOS**
(train +0.037 -> test **-0.031**) and fails placebo (p=0.31). The in-sample depth-edge is
non-monotone in the threshold (p25 helps, p40 hurts) -- the signature of curve-fitting noise.
Note: depth correlates with *coin* (btc deep, xrp thin), so an "ex-ante depth tilt" is mostly
"drop xrp" in disguise, and inherits its non-edge.

## Test 4 -- Thin-book gate (principled "drop xrp", per-round, all coins)
The cleanest candidate: drop each ROUND below the p25 ex-ante depth threshold (~10 L1 shares),
across all coins, so a deep xrp round is kept and a thin btc round is dropped. This is the only
test that is **positive in-sample (+0.019), positive on TRAIN (+0.023), positive OOS (+0.016)**
-- but the placebo gives **p=0.072** (stable: 0.075 @200, 0.072 @1000), just over the 0.05 bar.
**Borderline, does not clear the threshold.** It is the most promising direction if anything,
but on this sample it is not separable from a same-size random round-drop.

## Verdict
**EQUAL WEIGHT. Per-coin selection is noise.** None of drop-xrp, sol-tilt, or the depth gate
clears placebo + OOS together. Per-coin realized P&L spread (sol +13 vs xrp -20) is loss-tail
variance on ~280 trades/coin, not a real per-coin edge -- Test 2's CIs make this explicit
(0/4 coins distinguishable). The honest conclusion is to stop per-coin tuning.

**Caveats / what could change this:** ~280 trades/coin is small; a real ~±0.06 edge would
need ~3-4x the data to separate from the ±0.10 noise floor. The **thin-book depth gate (p=0.072)**
is the single direction worth revisiting once more fills accumulate -- it is principled
(per-round, ex-ante, all-coins) and never went negative -- but on today's data it does not
beat placebo and should NOT be deployed.
