# Polybot — Testimony (canonical, updated through iter 52)

Numbers from `python -m polybot.report` + the paper trader, AFTER the 5-review + audit campaign
that corrected the metrics. These are the *honest* numbers — earlier reports quoted a Sharpe of
6–9, which was a **t-statistic** (mean/std·√N, inflated by the 1861-market sample size), not a
per-market Sharpe. The corrected per-market Sharpe is modest; the EDGE is real because it is
*consistently* positive across thousands of small independent bets, not because any single
bet is high-Sharpe.

## [0] Cumulative sizing/risk improvement (iters 47–52), OOS on 559 held-out markets
Paper-traded before/after — what the recent work actually bought, out-of-sample:

| config | ROI | maxDD | geo-growth/mkt | Sharpe/mkt |
|---|---|---|---|---|
| baseline (iter44: 2% flat, no de-risk) | +403% | 4.4% | 0.290% | +0.398 |
| **current (2.5% + soft_dd=0.15)** | **+617%** | 6.4% | **0.353%** | +0.396 |

Rising geo-growth at flat Sharpe = the system was betting **below Kelly**; sizing up to a
fractional-Kelly 2.5% compounds ~22% faster for the same edge, the cost being a modestly larger
(still-safe) drawdown. `soft_dd` caps the bad-ordering tail that the larger bets create (free on
the normal path; engages only above 15% DD). **Scope: a small-account effect** — the gain
evaporates above ~$10k as thin-book capacity binds (verified +2345pp@$1k → +15pp@$100k).

## [0b] Foundational significance test — is the bias real, independent of the strategy?
Direct hypothesis test over **1,860 independent** cleanly-resolved markets (raw claim, no
sizing/momentum/compounding machinery):
- **BIAS** (won − mid): edge **+0.0311**, t=+3.36, one-sided **p=3.9e-04** → favorites genuinely underpriced
- **TRADEABLE** (won − ask): edge **+0.0227**, t=+2.46, **p=7.0e-03** → survives the spread (real +EV)
- Calibration: edge concentrated in the **0.70–0.80** band (win 0.814 vs mid 0.744, 95% CI
  [0.776, 0.852] entirely above mid) — the band `fav_main` targets; other bands ~fair.
- Caveat: t-test assumes market independence; regime clustering would shrink effective-n, but the
  bias is *direction-agnostic* (favorites underpriced, not BTC trend), so clustering shifts *which*
  side is favorite, not the bias magnitude.
- **Symmetry check (strengthens the above):** favorite side split ~50/50 (927 up / 933 down) →
  collection period was regime-balanced (no dominant trend → significance not trend-inflated). Bias
  present on BOTH sides (up +2.7pp p=0.02; down +3.5pp p=0.004) with NO significant difference
  (t=−0.43, p=0.67) → structural/behavioural, not a one-sided trend artifact. No directional tilt.

## [1] Favorite edge — the real, backtest-validated edge (1861 real markets)
- OOS paper trade (holdout) : **ROI +225%**, max DD 2.3%, win 68%  *(report.py holdout; the 70/30 split above gives +617% at 2.5%)*
- 5-fold cross-validation   : all folds positive, worst fold **+47%**
- Sequential segments (×6)  : all positive `[47, 48, 38, 53, 53, 52]` — **no temporal decay**
- Monte-Carlo (1000 orderings): **kill-rate 0%**, worst DD 10.4%, **100% positive**
- Cost sensitivity (slip→ROI): 0.002→+1050%, 0.004→+882%, 0.010→+597% — survives realistic costs
- Capacity ($→ROI)          : $1k→+898%, $100k→+71% — **small-capacity edge** (thin books)
- Book depth by price        : 0.60:$32 → 0.90:$82 — deeper near the extremes

## [2] Spot edge — informational, synthetic-only (needs live book-vs-spot lag)
- lag=0 (control)  : ROI **+0.0%** → control passes (no edge fabricated from nothing)
- lag=5s           : ROI −17% (insufficient lag → costs dominate)
- lag=10s          : ROI +160%, win 55% → **edge grows with lag** (monotone, as theory predicts)
- **The magnitude is unknown until measured live** — gamma-api is unreachable in sandbox.

## [3] Two-edge decorrelation (synthetic, both edges)
- between-edge correlation : **+0.111** (near-independent — genuine diversification)
- Sharpe individual→combined: [0.08, 0.10] → **0.12** (combination beats either alone)

## [4] Rejected hypotheses (honest negatives — what does NOT work)
Order-book imbalance scalp (both directions, spread > signal); late-band reweight (OOS null);
tight-spread filter (OOS worse); time-of-day filter (confounded with ~4 collection days,
untestable); maker/limit entry on favorites (adverse selection). The favorite edge is
**regime-robust** — positive across price, time-in-window, spread, and time-of-day — so
selection filters only drop +EV trades. The single deployable lever was **sizing**, not selection.

## Status
- Edge space provably closed 3 ways (ML AUC, MVO, capacity) for order-book signals; 10+ refinements rejected OOS.
- Every module adversarially reviewed (5 reviews) + a pattern-completeness audit; every finding fixed.
- **Live path now tested for faithfulness, not just hardened** (iters 51–52, `tests/test_live_parity.py`):
  tick assembly is byte-identical to the backtest on real markets; the paper settlement proxy
  matches the backtest's (median-of-5, ≥99% agreement, blip-robust); the spot strike is anchored
  to the true window-open price; spot-feed outages degrade gracefully. 179 unit+regression tests.
- **The one open frontier — live forward paper trading to measure the real spot-lag magnitude —
  is blocked by sandbox network access, not by missing code.** The live path that will carry it
  is now both hardened and parity-tested against the validated backtest.
