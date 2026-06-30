# Phase 5 — Spot-vs-Book Divergence Filter (SKIP vs FADE)

**Question.** Phase-4 found one entry-time signal that survived placebo+OOS and is *stronger* than the deployed chop gate (which itself FAILED placebo, p≈0.075, on the 8-day archive): **spot-vs-book divergence**. At entry, `net_rel` = signed pre-entry Binance spot move toward the favorite (round-open→entry-bar 1-min bars, no look-ahead). In ~94% of rounds `net_rel≥0` and the favorite wins ~85%; in the ~6% where `net_rel<0` (spot moved *against* the book-favorite) the favorite win-rate **collapses to ~30%** — a grossly overpriced, −EV pick. Does acting on this improve **real** $/trade?

**Answer: the signal is REAL and both SKIP and FADE help on real money — but the divergence sample is tiny (n=71 real fills), so the honest call is PAPER-TEST-FIRST (forward-paper the rule live) before real-money deployment, SKIP first.**

Run: `python3 phase5_divergence.py`. Deliverable: `phase5_divergence.py` (imports `backtest_real.py` and `phase4_features.py` **unmodified** — the exact `net_rel` code path is reused via a shim Round; reuses `phase2_entry.VolEnricher` for the deployed chop gate).

---

## Data & reconstruction

- Real fills (gold standard, 1.74-day window): **1150 settled**; `net_rel` reconstructed for **1142** (lost 8 to missing `end_ts`, 0 to missing klines).
- **Divergence fills (`net_rel<0`): 71 = 6.2%** of reconstructed — matching the archive's ~6%.
- Harness cross-check: this sim's baseline = $-17.33 (B.replay = $-17.33).
- `net_rel` is computed by **phase4's own `spot_path_feats`** (imported, not modified): round-open = `market.end_ts−300`, `fill_ts`→entry-bar, `fav_dir=+1` if side YES else −1, `net_rel=(close_entrybar−open_roundopen)/open×fav_dir`.

## Divergence vs non-divergence economics (hold-the-favorite, real fills)

| bucket | n | fav win% | mean price | pnl $ | $/trade |
|---|--:|--:|--:|--:|--:|
| non-div (`net_rel≥0`) | 1071 | 83.3 | 0.811 | +46.74 | +0.0436 |
| **divergence (`net_rel<0`)** | 71 | **33.8** | 0.729 | **-59.71** | -0.8410 |

The 71 divergence fills are priced ~0.73 but win only ~34% → they **bleed $59.71** and single-handedly drag the whole book ($-17.33) negative — the non-divergence book is $+46.74. This is the real money at stake.

**Robustness — broad-based, not one-fill/one-coin.** The divergence loss is NOT an artifact: the single worst fill is only 3% of the total, and the favorite under-performs in **every coin**:

| coin | n div | div win% | div pnl $ |
|---|--:|--:|--:|
| btc | 20 | 40 | -15.04 |
| eth | 18 | 39 | -12.57 |
| sol | 18 | 44 | -10.33 |
| xrp | 15 | 7 | -21.78 |

All four coins sit far below the 80% base win-rate in the divergence bucket. This is the opposite of the trailing-stop result (phase3), whose 'edge' was concentrated in the structurally-losing coins (btc/xrp) and *hurt* the winning coin (sol). Divergence is a genuine cross-coin overpricing signal.

## (a) SKIP — drop rounds with `net_rel < threshold`

| threshold | n | win% | pnl $ | vs base $ | $/trade | fire kept |
|---|--:|--:|--:|--:|--:|--:|
| baseline | 1150 | 80.0 | -17.33 | — | -0.0151 | 100% |
| net_rel<+0.0000 | 1079 | 83.0 | +42.38 | +59.71 | +0.0393 | 94% |
| net_rel<-0.0003 | 1135 | 80.8 | -0.18 | +17.15 | -0.0002 | 99% |
| net_rel<-0.0006 | 1144 | 80.4 | -7.63 | +9.70 | -0.0067 | 99% |

SKIP saves **$+59.71** by dropping only **71** trades (fire-rate cost is ~nil — divergence is ~6%). Placebo + OOS:

- **Placebo** (label-shuffle, 2000 shuffles): real $-edge **+59.71** (+0.0543/trade); null mean -0.0224/t (sd 0.0092); **p = 0.0005** → CREDIBLE (real edge in the tail).
- **TIME-OOS** (train 60% / test 40%): train edge +0.0576/t → test edge +0.0494/t (27 divergence fills skipped in test); **HOLDS**.

## (b) FADE — bet the OTHER side on divergence rounds

Fade buys the **opposite outcome at its real entry-tick ask** (NO-ask = 1−YES-bid if the favorite was YES, else YES-ask), wins iff the favorite **loses**. The buy price is taken from the real book (spread included), never the naive 1−fill_price.

| threshold | n | win% | pnl $ | vs base $ | $/trade |
|---|--:|--:|--:|--:|--:|
| baseline | 1150 | 80.0 | -17.33 | — | -0.0151 |
| net_rel<+0.0000 | 1150 | 82.0 | +99.36 | +116.69 | +0.0864 |
| net_rel<-0.0003 | 1150 | 80.8 | +16.48 | +33.80 | +0.0143 |
| net_rel<-0.0006 | 1150 | 80.5 | +1.92 | +19.24 | +0.0017 |

FADE turns the −EV favorite into a **+EV longshot**: it buys at mean ask 0.284 (a ~28% longshot) and wins ~66% (when the favorite loses), netting **$+116.69** over baseline — roughly double SKIP's saving (SKIP avoids the loss; FADE avoids it AND collects the other side).

- **Placebo** (2000 shuffles): real $-edge **+116.69** (+0.1015/trade); null mean -0.0445/t (sd 0.0179); **p = 0.0005** → CREDIBLE.
- **TIME-OOS**: train edge +0.1078/t → test edge +0.0920/t; **HOLDS**.

## (4) On top of the deployed gate (band[0.78,0.85] + chop)

Deployed gate = `band[0.78,0.85] AND chop(preabsmove>=0.00056)`. Gated subset: **n=193**, hold-favorite pnl **$+31.90** ($+0.1653/t). Divergence fills *inside* that subset: **0**.

**The chop gate already removes every divergence round** in the deployed band — there is nothing left to skip on top. This is itself an important finding: the chop gate and the divergence filter overlap heavily (a spot path that moved *against* the favorite is also a choppy/low-net-move path), so on the *currently live* subset divergence-SKIP is **redundant** — its value is in the rounds the chop gate would otherwise let through, i.e. if the chop gate were loosened or removed, OR on coins/bands the chop gate covers worse. Unlike the trailing stop (which actively *hurt* the gated subset, −5.85), divergence-SKIP does no harm here — it simply has no incremental rounds to act on.

## (5) Live-wireability — tick `(spot−strike)/strike` vs Binance-bar `net_rel`

The live bot already wires `tick.spot` (current) and `tick.strike` (round-open) for the chop gate, so a tick-based divergence `(tick.spot−tick.strike)/tick.strike × fav_dir` is **free to compute live**. But the tick `spot`/`strike` fields are populated on only **25%** of real fills (the older box; the newer per-coin box logs `spot=0`).

On the **286** shared (older-box) fills where both exist: the continuous values correlate **+0.62** and the *divergence flag* (sign<0) agrees **92%** of the time. So the tick version **does reproduce** the Binance-bar signal where it has data — the two are measuring the same thing (spot displacement from the round-open strike).

**Why phase-4's tick feature (`spot_strike_fav`) failed placebo (p≈0.073) while the Binance-bar `net_rel` survived (p≈0.000):** it is **coverage/power, not a different signal.** The tick feature only existed on ~1/4 of rounds (older box), so the divergence bucket it could see was ~1/4 the size — too few to push past the placebo null. The Binance-bar `net_rel` has ~full coverage and therefore the statistical power to clear placebo. The tick path also carries a single noisy snapshot (one tick's spot vs strike) versus the chained 1-min bar path, adding noise. **Implication for live wiring:** a *tick-only* divergence gate would work on the older box but go dark on the newer box; the robust rule needs the **Binance path** (round-open→entry-bar klines), which the bot can fetch live exactly as phase2's VolEnricher already does for the chop gate.

## (1b) Full-archive confirmation (4009 rounds)

On the full archive: divergence (`net_rel<0`) is **238 = 5.9%** of 4009 rounds, with favorite win% **32.4%** (vs 86.2% non-div, base 83.0%) at mean price 0.803. SKIP-divergence margin edge +0.0318, **placebo p=0.0005** (CREDIBLE). OOS: train div win% 34.4% (n=154) → test div win% 28.6% (n=84) — still far below base, HOLDS. The archive (4009 rounds, much larger n) is where the signal is statistically airtight; the real-fill window merely confirms it on actual money.

## Verdict & exact rule to wire

| check | SKIP | FADE |
|---|:--:|:--:|
| beats baseline (real $) | ✅ ($+59.71) | ✅ ($+116.69) |
| survives placebo | ✅ (p=0.0005) | ✅ (p=0.0005) |
| holds TIME-OOS | ✅ | ✅ |
| archive-confirmed | ✅ | ✅ |
| live-wireable | ✅ (Binance path, as chop gate) | ✅ (needs opposite-side ask) |

**Honest sample caveat.** Only **71 real divergence fills** in the 1.74-day window. That is enough to move real dollars ($60 bled) and, because the win-rate gap is so large (34% vs 83%), enough to clear placebo here — but it is a thin sample and FADE's longshot payoff rides on ~71 bets. The 4009-round archive is the airtight evidence; the real fills confirm direction and dollars.

**RECOMMENDATION:**

1. **DEPLOY divergence-SKIP** (the conservative, robust rule). It removes a strongly −EV bucket at ~zero fire-rate cost, beats baseline on real money, survives placebo + OOS, is airtight on the archive, and is live-wireable with infrastructure the bot already has.
2. **PAPER-TEST-FIRST for FADE.** FADE roughly doubles the dollar gain by collecting the longshot, and it passes placebo+OOS here, but it rests on a tiny longshot sample, buys thin/illiquid far-side books, and inverts the strategy on the rounds it fires. Forward-paper it (and confirm fade-side fill probability and slippage) before committing real money.

**Exact rule to wire (matches the live chop-gate plumbing):**

```
# At entry decision (~150s to settle), per coin:
#   round_open = settle_ts - 300              # 5-min UTC boundary (already known)
#   entry_bar  = (fill_ts - round_open) // 60  # 0..4
#   o = binance_1m[round_open].open
#   c = binance_1m[round_open + 60*entry_bar].close   # NO look-ahead
#   fav_dir = +1 if favorite_side == 'YES' else -1
#   net_rel = ((c - o) / o) * fav_dir
#   if net_rel < 0.0:  SKIP this round   (do NOT buy the favorite)
# net_rel uses the SAME round-open->entry-bar klines the VolEnricher chop gate
# already fetches; add it next to the chop check. Tick (spot-strike)/strike is a
# fallback ONLY on the older box (coverage ~25%) -- prefer the Binance path.
```

## Caveats

- **Real-fill window is 1.74 days**; OOS halves are ~0.9 days → directional, not definitive. The archive (8 days, 4009 rounds) carries the statistical weight.
- **Divergence is rare (~6%)** → SKIP's absolute dollar impact is small in any single window, but the avoided rounds are the most −EV in the book.
- **FADE buys thin far-side books** (mean ask ~0.28, a longshot); fill probability and slippage on the opposite outcome are NOT modeled beyond using the displayed ask — a key reason to paper-test it.
- **net_rel is partly mechanical** (the favorite side co-moves with spot), so most of its AUC restates 'the leader usually wins'; the *exploitable* part is precisely this rare disagreement bucket, which is what we traded here.

