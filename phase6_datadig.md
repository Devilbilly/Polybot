# Phase-6 Open-Ended Data Dig — new exploitable q−p structure beyond Phase-4

**What this is.** Phase-4 was a single-feature screen; Phase-5 owns the `net_rel` spot-vs-book divergence backtest. This Phase-6 dig goes WIDER/DEEPER across six OTHER lead families to find places where realized win-rate q reliably differs from the price p we pay (edge q−p ≠ 0). Same unforgiving discipline: every claimed cell must (a) beat a label-shuffle placebo (2000 shuffles → empirical p) with a **Bonferroni correction over the number of cells in its family**, AND (b) hold its edge direction on a chronological **TIME-OOS** split (early 60% train / late 40% test). No look-ahead anywhere.

- Archive `archive/master_polybot.db`, span **2026-06-22 14:48:07.711826+00:00 .. 2026-06-30 06:58:11.075856+00:00 UTC**; usable **4009/6279** rounds.
- Base favorite win-rate **0.830**, mean entry price **0.807** → baseline edge **+0.024** (the bar to beat).
- **Total distinct hypotheses / cells tested: H = 64** (reported for honesty; expect most to be noise).

> A cell is **REAL** only if Bonferroni-corrected placebo p < 0.05 **and** the edge direction holds in the late TIME-OOS half. `noise (fails placebo/MT)` = the raw effect didn't survive multiple-testing; `noise (fails OOS)` = survived placebo but flipped/faded out-of-sample.

## Ranked results (all cells, all families)

| family | cell | n | win% (q) | price | edge q−p | placebo p | Bonferroni p | OOS holds | verdict |
|--------|------|--:|---------:|------:|---------:|----------:|-------------:|:---------:|---------|
| L3 calibration | price[0.78,0.80) x divergent(net_rel<0) | 112 | 29% | 0.783 | -0.489 | 0.000 | 0.007 | **yes** | **REAL** |
| L6 loss-tail | divergent & choppy (known-axes combo, whole deck) | 209 | 34% | 0.803 | -0.463 | 0.000 | 0.000 | **yes** | **REAL** |
| L3 calibration | price[0.82,0.85) x divergent(net_rel<0) | 71 | 42% | 0.835 | -0.412 | 0.000 | 0.007 | **yes** | **REAL** |
| L1 interaction | net_rel x eff_ratio (drift x clean-trend) [BAD-confluence (skip?)] | 888 | 67% | 0.803 | -0.128 | 0.000 | 0.008 | **yes** | **REAL** |
| L1 interaction | net_rel x dir_changes (drift x chop) [BAD-confluence (skip?)] | 626 | 68% | 0.801 | -0.124 | 0.000 | 0.008 | **yes** | **REAL** |
| L1 interaction | net_rel x price_traj (drift x firming) [GOOD-confluence] | 388 | 91% | 0.806 | +0.107 | 0.000 | 0.008 | **yes** | **REAL** |
| L1 interaction | net_rel x eff_ratio (drift x clean-trend) [GOOD-confluence] | 697 | 92% | 0.812 | +0.105 | 0.000 | 0.008 | **yes** | **REAL** |
| L1 interaction | net_rel x dir_changes (drift x chop) [GOOD-confluence] | 883 | 92% | 0.813 | +0.103 | 0.000 | 0.008 | **yes** | **REAL** |
| L3 calibration | price[0.78,0.80) x eff:high(trend) | 504 | 89% | 0.784 | +0.103 | 0.000 | 0.007 | **yes** | **REAL** |
| L1 interaction | absmove x dir_changes (bigmove x chop) [GOOD-confluence] | 881 | 91% | 0.813 | +0.095 | 0.000 | 0.008 | **yes** | **REAL** |
| L1 interaction | entry_price x net_rel (cheap-fav x drift) [GOOD-confluence] | 565 | 93% | 0.836 | +0.095 | 0.000 | 0.008 | **yes** | **REAL** |
| L1 interaction | absmove x dir_changes (bigmove x chop) [BAD-confluence (skip?)] | 610 | 71% | 0.801 | -0.094 | 0.000 | 0.008 | **yes** | **REAL** |
| L1 interaction | ALIGNED & CLEAN-TREND (net_rel>=0, top-eff-quintile) [SIZE-UP cell] | 755 | 90% | 0.810 | +0.094 | 0.000 | 0.000 | **yes** | **REAL** |
| L1 interaction | eff_ratio x dir_changes (chop confluence) [GOOD-confluence] | 1060 | 90% | 0.809 | +0.087 | 0.000 | 0.008 | **yes** | **REAL** |
| L1 interaction | net_rel x price_traj (drift x firming) [BAD-confluence (skip?)] | 364 | 73% | 0.810 | -0.085 | 0.001 | 0.016 | **yes** | **REAL** |
| L1 interaction | entry_price x eff_ratio (cheap-fav x chop) [GOOD-confluence] | 524 | 91% | 0.835 | +0.079 | 0.000 | 0.008 | **yes** | **REAL** |
| L3 calibration | price[0.82,0.85) x eff:high(trend) | 524 | 91% | 0.835 | +0.079 | 0.000 | 0.007 | **yes** | **REAL** |
| L1 interaction | eff_ratio x dir_changes (chop confluence) [BAD-confluence (skip?)] | 715 | 73% | 0.803 | -0.073 | 0.000 | 0.008 | **yes** | **REAL** |
| L3 calibration | price[0.82,0.85) x eff:mid | 482 | 90% | 0.836 | +0.064 | 0.000 | 0.007 | **yes** | **REAL** |
| L1 interaction | price_traj x eff_ratio (firming x trend) [BAD-confluence (skip?)] | 388 | 75% | 0.816 | -0.063 | 0.001 | 0.024 | **yes** | **REAL** |
| L3 calibration | price[0.82,0.85) x aligned(net_rel>=0) | 1336 | 89% | 0.836 | +0.058 | 0.000 | 0.007 | **yes** | **REAL** |
| L3 calibration | price[0.80,0.82) x divergent(net_rel<0) | 55 | 25% | 0.804 | -0.549 | 0.000 | 0.007 | — | **noise (fails OOS)** |
| L2 cross-coin | BTC strongly AGAINST fav & OWN start flat/toward (skip?) | 56 | 89% | 0.808 | +0.085 | 0.204 | 0.408 | — | **noise** |
| L5 time/session | xrp x hourblock3(18-24UTC) | 200 | 89% | 0.807 | +0.083 | 0.013 | 0.260 | **yes** | **noise (fails placebo/MT)** |
| L1 interaction | entry_price x net_rel (cheap-fav x drift) [BAD-confluence (skip?)] | 678 | 70% | 0.784 | -0.083 | 0.004 | 0.064 | **yes** | **noise (fails placebo/MT)** |
| L3 calibration | price[0.80,0.82) x eff:low(chop) | 304 | 73% | 0.804 | -0.074 | 0.010 | 0.157 | **yes** | **noise (fails placebo/MT)** |
| L1 interaction | entry_price x eff_ratio (cheap-fav x chop) [BAD-confluence (skip?)] | 631 | 71% | 0.784 | -0.072 | 0.028 | 0.456 | **yes** | **noise (fails placebo/MT)** |
| L3 calibration | price[0.78,0.80) x eff:low(chop) | 632 | 71% | 0.784 | -0.072 | 0.032 | 0.487 | **yes** | **noise (fails placebo/MT)** |
| L3 calibration | price[0.80,0.82) x eff:high(trend) | 308 | 87% | 0.804 | +0.069 | 0.024 | 0.360 | **yes** | **noise (fails placebo/MT)** |
| L5 time/session | sol x hourblock0(00-06UTC) | 227 | 88% | 0.808 | +0.069 | 0.027 | 0.550 | **yes** | **noise (fails placebo/MT)** |
| L1 interaction | price_traj x eff_ratio (firming x trend) [GOOD-confluence] | 298 | 87% | 0.806 | +0.067 | 0.023 | 0.376 | **yes** | **noise (fails placebo/MT)** |
| L3 calibration | price[0.78,0.80) x eff:mid | 558 | 85% | 0.784 | +0.065 | 0.109 | 1.000 | no | **noise** |
| L3 calibration | price[0.78,0.80) x aligned(net_rel>=0) | 1582 | 85% | 0.784 | +0.062 | 0.023 | 0.352 | **yes** | **noise (fails placebo/MT)** |
| L3 calibration | price[0.82,0.85) x eff:low(chop) | 401 | 78% | 0.836 | -0.060 | 0.004 | 0.067 | **yes** | **noise (fails placebo/MT)** |
| L5 time/session | sol x hourblock3(18-24UTC) | 253 | 86% | 0.807 | +0.054 | 0.101 | 1.000 | **yes** | **noise (fails placebo/MT)** |
| L5 time/session | eth x hourblock2(12-18UTC) | 296 | 86% | 0.805 | +0.053 | 0.109 | 1.000 | **yes** | **noise (fails placebo/MT)** |
| L5 time/session | eth x hourblock0(00-06UTC) | 220 | 86% | 0.808 | +0.051 | 0.139 | 1.000 | **yes** | **noise (fails placebo/MT)** |
| L5 time/session | sol (all hours) | 1011 | 86% | 0.807 | +0.049 | 0.007 | 0.140 | **yes** | **noise (fails placebo/MT)** |
| L5 time/session | btc x hourblock0(00-06UTC) | 261 | 76% | 0.805 | -0.046 | 0.167 | 1.000 | **yes** | **noise (fails placebo/MT)** |
| L5 time/session | sol x hourblock2(12-18UTC) | 290 | 85% | 0.807 | +0.041 | 0.219 | 1.000 | **yes** | **noise (fails placebo/MT)** |
| L3 calibration | price[0.80,0.82) x aligned(net_rel>=0) | 853 | 84% | 0.804 | +0.040 | 0.121 | 1.000 | **yes** | **noise (fails placebo/MT)** |
| L5 time/session | sol x hourblock1(06-12UTC) | 241 | 84% | 0.806 | +0.036 | 0.366 | 1.000 | **yes** | **noise (fails placebo/MT)** |
| L2 cross-coin | BTC strongly toward fav & OWN start flat/against | 61 | 77% | 0.806 | -0.035 | 0.621 | 1.000 | no | **noise** |
| L5 time/session | eth (all hours) | 1027 | 84% | 0.806 | +0.033 | 0.188 | 1.000 | **yes** | **noise (fails placebo/MT)** |
| L5 time/session | eth x hourblock3(18-24UTC) | 259 | 84% | 0.806 | +0.032 | 0.410 | 1.000 | **yes** | **noise (fails placebo/MT)** |
| L5 time/session | xrp x hourblock0(00-06UTC) | 179 | 78% | 0.807 | -0.031 | 0.385 | 1.000 | **yes** | **noise (fails placebo/MT)** |
| L5 time/session | xrp x hourblock1(06-12UTC) | 179 | 83% | 0.808 | +0.024 | 0.567 | 1.000 | no | **noise** |
| L5 time/session | btc x hourblock2(12-18UTC) | 311 | 83% | 0.805 | +0.021 | 0.631 | 1.000 | no | **noise** |
| L5 time/session | xrp (all hours) | 807 | 83% | 0.808 | +0.021 | 0.560 | 1.000 | no | **noise** |
| L6 loss-tail | secs_to_settle worst-quintile WITHIN aligned+trending | 666 | 90% | 0.816 | +0.019 | 0.105 | 0.422 | no | **noise** |
| L3 calibration | price[0.80,0.82) x eff:mid | 296 | 82% | 0.804 | +0.017 | 0.740 | 1.000 | no | **noise** |
| L6 loss-tail | total_depth3 worst-quintile WITHIN aligned+trending | 467 | 87% | 0.809 | -0.014 | 0.333 | 1.000 | **yes** | **noise (fails placebo/MT)** |
| L1 interaction | ALIGNED & CHOP (net_rel>=0, bottom-eff-quintile) [SKIP cell] | 755 | 79% | 0.805 | -0.013 | 0.860 | 0.860 | no | **noise** |
| L6 loss-tail | l1_depth worst-quintile WITHIN aligned+trending | 477 | 87% | 0.809 | -0.011 | 0.419 | 1.000 | **yes** | **noise (fails placebo/MT)** |
| L4 late-entry | late faded (drift<0) -> skip-candidate: edge AT LATE PRICE | 980 | 47% | 0.476 | -0.008 | 1.000 | 1.000 | no | **noise** |
| L5 time/session | xrp x hourblock2(12-18UTC) | 249 | 82% | 0.809 | +0.006 | 0.881 | 1.000 | no | **noise** |
| L5 time/session | eth x hourblock1(06-12UTC) | 252 | 80% | 0.807 | -0.005 | 0.914 | 1.000 | **yes** | **noise (fails placebo/MT)** |
| L5 time/session | btc (all hours) | 1164 | 80% | 0.806 | -0.005 | 0.990 | 1.000 | no | **noise** |
| L6 loss-tail | imb3_fav worst-quintile WITHIN aligned+trending | 491 | 89% | 0.814 | +0.005 | 0.750 | 1.000 | **yes** | **noise (fails placebo/MT)** |
| L5 time/session | btc x hourblock3(18-24UTC) | 334 | 80% | 0.805 | -0.003 | 0.975 | 1.000 | no | **noise** |
| L4 late-entry | late firm-up (drift>0): edge AT LATE PRICE | 2724 | 96% | 0.954 | +0.002 | 1.000 | 1.000 | no | **noise** |
| L5 time/session | btc x hourblock1(06-12UTC) | 258 | 81% | 0.809 | +0.001 | 1.000 | 1.000 | no | **noise** |

## Interpretation — most 'survivors' RESTATE Phase-4's axes; only one is NEW

**Read the 21-survivor count with extreme suspicion.** A cell can pass placebo+Bonferroni+OOS and still be *non-novel* if it merely inherits a known axis. Almost every survivor here is a re-slicing of the SAME two Phase-4 / Phase-5 axes, confirmed two ways:

1. **The divergence axis (Phase-5's territory, NOT new).** Every `divergent(net_rel<0)` calibration cell shows a huge negative edge (q−p ≈ −0.41…−0.55). That is exactly Phase-5's spot-vs-book divergence FADE/SKIP, re-found here per price-bin. It is real but it is *not* a Phase-6 discovery — it is the thing Phase-5 owns. We surface it only to confirm it is stable across the [0.78,0.85] price range.
2. **The 'GOOD-confluence' interaction cells are mechanically confounded.** A cell like *net_rel-high × eff_ratio-high* wins more because `net_rel` alone wins more — the incremental probe shows the product's within-`net_rel`-tercile power (≈0.105) is HALF its marginal power (≈0.206), i.e. the interaction mostly *restates* the single axis. These cells are +EV but tautological; they do not add a lever beyond conditioning on `net_rel` and `eff_ratio` separately (which Phase-4 already told us).

**The ONE genuinely new, exploitable result** is the conditional chop lever, which is the only finding that survives *after* the divergence axis is held neutral:

> **Within the ALIGNED regime (`net_rel ≥ 0` — book and spot agree, so Phase-5's divergence signal says nothing), the favorite's trend-vs-chop quality still cleanly separates +EV from break-even trades.** Clean-trend favorites (top eff-ratio quintile): win **90.3%** at price 0.810 → edge **+0.094**. Chop favorites (bottom eff-ratio quintile): win **79.2%** at price 0.805 → edge **−0.013** (≈break-even / slightly −EV). The **q−p SPREAD = +0.106**, label-shuffle placebo **p≈0.0005** (2000 shuffles), and it **holds TIME-OOS** (train +0.129 → test +0.075). `eff_ratio` restricted to the aligned subset has AUC 0.594, placebo p<0.001, OOS holds.

Why this is incremental to Phase-5: Phase-5's divergence edge lives in the ~6–7% of rounds where spot contradicts the book. The chop lever lives in the OTHER ~94% (aligned) rounds, slicing them into a +EV clean-trend bucket and a ~break-even chop bucket. The two are near-orthogonal (Phase-4: corr eff_ratio↔net_rel ≈ +0.40, and here eff_ratio works with net_rel held ≥0). **Actionable rule:** in the aligned regime, SIZE UP clean-trend favorites and SKIP / down-weight chop favorites. This is the deployed static chop gate done as a *conditional, price-aware* lever rather than a single global threshold.

Everything else (L2 cross-coin, L4 late-entry, L5 time/session, L6 new loss markers) is NOISE — see per-family notes. Two clean negative results worth stating: **(L2)** BTC does NOT lead the alts in an exploitable way (same-minute BTC↔alt return corr ≈ +0.81; the alts already co-move, leaving no tradable lead-lag; BTC's own marginal predictive power on alts is weaker than the alt's own move and barely clears placebo). **(L4)** Entering LATER does not help: by rem≈30s the favorite has firmed up, so the per-share edge erodes from +0.024 (original price) to +0.003 (late price) — price convergence eats the favorite-longshot margin. Waiting is strictly worse.

## Survivors (raw list — but see Interpretation above: most are NON-NOVEL)

**21 cell(s) pass placebo+Bonferroni AND TIME-OOS. Of these, exactly ONE — `ALIGNED & CLEAN-TREND` (with its companion SKIP cell and the spread test) — is a NEW Phase-6 lever; the rest restate the divergence and chop/price axes already known from Phase-4/5.**

### L1 interaction — `eff_ratio x dir_changes (chop confluence) [BAD-confluence (skip?)]`
- win 73% [70-76], price 0.803
- n=715, q−p edge **-0.073**, placebo p=0.000, Bonferroni p=0.008.
- TIME-OOS: train edge -0.067 (n=424) → test edge -0.080 (n=291); direction **holds**.

### L1 interaction — `eff_ratio x dir_changes (chop confluence) [GOOD-confluence]`
- win 90% [88-91], price 0.809
- n=1060, q−p edge **+0.087**, placebo p=0.000, Bonferroni p=0.008.
- TIME-OOS: train edge +0.100 (n=627) → test edge +0.068 (n=433); direction **holds**.

### L1 interaction — `net_rel x eff_ratio (drift x clean-trend) [BAD-confluence (skip?)]`
- win 67% [64-70], price 0.803
- n=888, q−p edge **-0.128**, placebo p=0.000, Bonferroni p=0.008.
- TIME-OOS: train edge -0.134 (n=553) → test edge -0.118 (n=335); direction **holds**.

### L1 interaction — `net_rel x eff_ratio (drift x clean-trend) [GOOD-confluence]`
- win 92% [89-94], price 0.812
- n=697, q−p edge **+0.105**, placebo p=0.000, Bonferroni p=0.008.
- TIME-OOS: train edge +0.108 (n=417) → test edge +0.100 (n=280); direction **holds**.

### L1 interaction — `net_rel x dir_changes (drift x chop) [BAD-confluence (skip?)]`
- win 68% [64-71], price 0.801
- n=626, q−p edge **-0.124**, placebo p=0.000, Bonferroni p=0.008.
- TIME-OOS: train edge -0.124 (n=367) → test edge -0.124 (n=259); direction **holds**.

### L1 interaction — `net_rel x dir_changes (drift x chop) [GOOD-confluence]`
- win 92% [90-93], price 0.813
- n=883, q−p edge **+0.103**, placebo p=0.000, Bonferroni p=0.008.
- TIME-OOS: train edge +0.108 (n=543) → test edge +0.097 (n=340); direction **holds**.

### L1 interaction — `entry_price x eff_ratio (cheap-fav x chop) [GOOD-confluence]`
- win 91% [89-94], price 0.835
- n=524, q−p edge **+0.079**, placebo p=0.000, Bonferroni p=0.008.
- TIME-OOS: train edge +0.090 (n=297) → test edge +0.064 (n=227); direction **holds**.

### L1 interaction — `entry_price x net_rel (cheap-fav x drift) [GOOD-confluence]`
- win 93% [91-95], price 0.836
- n=565, q−p edge **+0.095**, placebo p=0.000, Bonferroni p=0.008.
- TIME-OOS: train edge +0.098 (n=322) → test edge +0.090 (n=243); direction **holds**.

### L1 interaction — `absmove x dir_changes (bigmove x chop) [BAD-confluence (skip?)]`
- win 71% [67-74], price 0.801
- n=610, q−p edge **-0.094**, placebo p=0.000, Bonferroni p=0.008.
- TIME-OOS: train edge -0.089 (n=352) → test edge -0.102 (n=258); direction **holds**.

### L1 interaction — `absmove x dir_changes (bigmove x chop) [GOOD-confluence]`
- win 91% [89-93], price 0.813
- n=881, q−p edge **+0.095**, placebo p=0.000, Bonferroni p=0.008.
- TIME-OOS: train edge +0.102 (n=541) → test edge +0.085 (n=340); direction **holds**.

### L1 interaction — `price_traj x eff_ratio (firming x trend) [BAD-confluence (skip?)]`
- win 75% [71-79], price 0.816
- n=388, q−p edge **-0.063**, placebo p=0.001, Bonferroni p=0.024.
- TIME-OOS: train edge -0.048 (n=223) → test edge -0.085 (n=165); direction **holds**.

### L1 interaction — `net_rel x price_traj (drift x firming) [BAD-confluence (skip?)]`
- win 73% [68-77], price 0.810
- n=364, q−p edge **-0.085**, placebo p=0.001, Bonferroni p=0.016.
- TIME-OOS: train edge -0.040 (n=217) → test edge -0.151 (n=147); direction **holds**.

### L1 interaction — `net_rel x price_traj (drift x firming) [GOOD-confluence]`
- win 91% [88-94], price 0.806
- n=388, q−p edge **+0.107**, placebo p=0.000, Bonferroni p=0.008.
- TIME-OOS: train edge +0.109 (n=218) → test edge +0.104 (n=170); direction **holds**.

### L1 interaction — `ALIGNED & CLEAN-TREND (net_rel>=0, top-eff-quintile) [SIZE-UP cell]`
- win 90% at price 0.810 -> edge q-p=+0.094 (genuinely +EV; SIZE-UP candidate)
- n=755, q−p edge **+0.094**, placebo p=0.000, Bonferroni p=0.000.
- TIME-OOS: train edge +0.111 (n=427) → test edge +0.071 (n=328); direction **holds**.

### L3 calibration — `price[0.78,0.80) x eff:high(trend)`
- win 89% [86-91], edge q-p=+0.103
- n=504, q−p edge **+0.103**, placebo p=0.000, Bonferroni p=0.007.
- TIME-OOS: train edge +0.107 (n=283) → test edge +0.098 (n=221); direction **holds**.

### L3 calibration — `price[0.78,0.80) x divergent(net_rel<0)`
- win 29% [22-38], edge q-p=-0.489
- n=112, q−p edge **-0.489**, placebo p=0.000, Bonferroni p=0.007.
- TIME-OOS: train edge -0.467 (n=76) → test edge -0.534 (n=36); direction **holds**.

### L3 calibration — `price[0.82,0.85) x eff:mid`
- win 90% [87-92], edge q-p=+0.064
- n=482, q−p edge **+0.064**, placebo p=0.000, Bonferroni p=0.007.
- TIME-OOS: train edge +0.072 (n=275) → test edge +0.053 (n=207); direction **holds**.

### L3 calibration — `price[0.82,0.85) x eff:high(trend)`
- win 91% [89-94], edge q-p=+0.079
- n=524, q−p edge **+0.079**, placebo p=0.000, Bonferroni p=0.007.
- TIME-OOS: train edge +0.090 (n=297) → test edge +0.064 (n=227); direction **holds**.

### L3 calibration — `price[0.82,0.85) x aligned(net_rel>=0)`
- win 89% [88-91], edge q-p=+0.058
- n=1336, q−p edge **+0.058**, placebo p=0.000, Bonferroni p=0.007.
- TIME-OOS: train edge +0.067 (n=755) → test edge +0.046 (n=581); direction **holds**.

### L3 calibration — `price[0.82,0.85) x divergent(net_rel<0)`
- win 42% [31-54], edge q-p=-0.412
- n=71, q−p edge **-0.412**, placebo p=0.000, Bonferroni p=0.007.
- TIME-OOS: train edge -0.371 (n=41) → test edge -0.468 (n=30); direction **holds**.

### L6 loss-tail — `divergent & choppy (known-axes combo, whole deck)`
- LOSS 66% vs base 17% — known-axes control; the bar a NEW marker must beat
- n=209, q−p edge **-0.463**, placebo p=0.000, Bonferroni p=0.000.
- TIME-OOS: train edge -0.443 (n=137) → test edge -0.502 (n=72); direction **holds**.

## Per-family notes (what was tested, what was found)

### L1 FEATURE INTERACTIONS

- L1 interactions: 4009 rounds with all components; 8 pairs x 2 tails = 16 interaction cells tested.
-   incremental probe net_rel*eff_ratio: marginal power 0.206 vs within-net_rel-tercile cond power 0.105 (cond<<marg => product just restates net_rel).
-   *** INCREMENTAL CHOP TEST (the key L1 result): within the ALIGNED subset (net_rel>=0, n=3771, win 86.2%) — where the divergence signal is gone — eff_ratio STILL predicts: AUC=0.594, placebo p=0.000, OOS HOLDS (train AUC 0.611 -> test 0.574). => chop/trend is a lever INDEPENDENT of divergence.
-   *** HEADLINE NEW LEVER: within ALIGNED, the q-p edge SPREAD (clean-trend +0.094 minus chop -0.013) = +0.106; placebo p=0.0005; OOS train +0.129 -> test +0.075 (HOLDS). This separates +EV clean-trend favorites from ~break-even chop favorites IN THE REGIME WHERE DIVERGENCE IS SILENT — incremental to Phase-5.
- **Family verdict:** 14 REAL / 18 cells. Survivor(s): eff_ratio x dir_changes (chop confluence) [BAD-confluence (skip?)]; eff_ratio x dir_changes (chop confluence) [GOOD-confluence]; net_rel x eff_ratio (drift x clean-trend) [BAD-confluence (skip?)]; net_rel x eff_ratio (drift x clean-trend) [GOOD-confluence]; net_rel x dir_changes (drift x chop) [BAD-confluence (skip?)]; net_rel x dir_changes (drift x chop) [GOOD-confluence]; entry_price x eff_ratio (cheap-fav x chop) [GOOD-confluence]; entry_price x net_rel (cheap-fav x drift) [GOOD-confluence]; absmove x dir_changes (bigmove x chop) [BAD-confluence (skip?)]; absmove x dir_changes (bigmove x chop) [GOOD-confluence]; price_traj x eff_ratio (firming x trend) [BAD-confluence (skip?)]; net_rel x price_traj (drift x firming) [BAD-confluence (skip?)]; net_rel x price_traj (drift x firming) [GOOD-confluence]; ALIGNED & CLEAN-TREND (net_rel>=0, top-eff-quintile) [SIZE-UP cell]

### L2 CROSS-COIN LEAD-LAG

- L2 cross-coin (alt rounds n=2845): AUC btc_m0=0.529 (placebo p=0.051, OOS holds) vs AUC own_m0=0.550 (placebo p=0.002).
-   same-minute raw-return corr(BTC, alt) = +0.81 (high => alts already co-move with BTC; little independent BTC lead).
- **Family verdict:** 0 REAL / 2 cells. All noise under placebo+MT+OOS.

### L3 CALIBRATION SURFACE

- L3 calibration: grid 3 price-bins x 5 conditions = 15 cells (Bonferroni over 15).
- **Family verdict:** 6 REAL / 15 cells. Survivor(s): price[0.78,0.80) x eff:high(trend); price[0.78,0.80) x divergent(net_rel<0); price[0.82,0.85) x eff:mid; price[0.82,0.85) x eff:high(trend); price[0.82,0.85) x aligned(net_rel>=0); price[0.82,0.85) x divergent(net_rel<0)

### L4 LATE MICROSTRUCTURE

- L4 late (n=3794 could-wait rounds, decision_rem~30s): AUC late_drift->win=0.901 (placebo p=0.000, OOS holds).
-   same rounds: q=83.1%; edge@orig-price=+0.024, edge@late-price=+0.003 (late worse if favorite firmed up; better only if it cheapened).
- **Family verdict:** 0 REAL / 2 cells. All noise under placebo+MT+OOS.

### L5 TIME / SESSION

- L5 time/session: 20 cells (4 coins x [all + 4 hour-blocks]); de-trended via q - price; Bonferroni over 20.
- **Family verdict:** 0 REAL / 20 cells. All noise under placebo+MT+OOS.

### L6 LOSS TAIL

- L6 loss tail: base LOSS rate = 17.0% over 4009 rounds.
-   aligned+trending control stratum: n=2643, win-rate 88.5% (the conditional null base). New markers below are tested for EXTRA loss concentration BEYOND this.
-   NOTE: `ws_gap` (websocket-vs-book mid) and `spread` are near-constant columns in this archive (ws fields ~unpopulated; spread pinned at the 0.01 min tick) — they carry no information and were excluded, not tested.
- **Family verdict:** 1 REAL / 5 cells. Survivor(s): divergent & choppy (known-axes combo, whole deck)

## Recommendation

**Worth a dedicated follow-up backtest (on the real-fill harness):** the **conditional chop lever** — in the aligned regime (`net_rel ≥ 0`), down-weight / skip favorites in the bottom `eff_ratio` quintile and (optionally) size up the top quintile. It is the one Phase-6 result that is (a) +EV in q−p terms (~+0.09 on the clean-trend side, ~0/−0.01 on the chop side, ~+0.11 spread), (b) placebo-real (p≈0.0005), (c) OOS-stable, and (d) genuinely incremental to Phase-5's divergence fade (it operates in the disjoint, ~94% agree-regime). Implementation note: this is a `predicate=`-style SKIP/down-weight gate on `backtest_real.py`, which BACKTEST_FOUNDATION §4 marks as EXACT (no new-entry simulation needed) — the chop skip only *removes* trades, reusing real fills/shares/winners. Run it through `gate_delta_test` + an OOS split before believing the dollar number.

**Do NOT pursue:** cross-coin BTC-lead (L2, no tradable lead-lag), late/later entry (L4, convergence eats the edge), coin×hour timing (L5, all noise after de-trending), and new book-microstructure loss markers (L6, nothing beyond the known divergence× chop combo; `ws_gap`/`spread` are degenerate columns in this archive). The divergence calibration cells are Phase-5's to backtest, not a new lead.

## Honest caveats

- **Multiple testing is corrected but the prior still dominates.** We tested H = 64 cells; Bonferroni within each family is applied, and the family count itself is small (6). A cell at Bonferroni p just under 0.05 should still be treated as a *candidate for a dedicated OOS backtest*, not a deploy decision.
- **'Survivor' count is inflated by confounding, not by discovery.** Most survivors re-slice the divergence and chop/price axes; collapsing them, Phase-6 yields exactly ONE new lever (conditional chop within the aligned regime). See Interpretation.
- **The chop lever's chop-side edge is ~break-even, not strongly negative.** Its value is the +EV clean-trend bucket and the *spread*; a pure 'skip chop' gate saves you from ≈0-EV trades (variance reduction), while 'size up clean-trend' is where the +0.09 q−p actually is. Size accordingly.
- **No look-ahead.** Spot/cross-coin features use round-open→entry-bar klines; book/late features use only ticks at/before the stated decision rem. The outcome-deciding post-entry move is invisible to every cell.
- **Late-entry (L4) is a research probe, not a fill model.** It measures q at a later book price but does NOT model fill probability or slippage of actually entering at rem~30s (per BACKTEST_FOUNDATION §4, new-entry simulation is unfaithful). Treat any L4 edge as an upper bound pending a real-fill check.
- **Paper labels.** Win/loss is the archived market winner vs the reconstructed favorite side, not realized fills.
- **Phase-5 boundary.** The `net_rel<0` divergence FADE/SKIP is deliberately NOT the headline here; where divergence appears (L3 condition, L6 benchmark) it is used only to locate OTHER structure or as a known-axis control.

