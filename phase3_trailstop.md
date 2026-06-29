# Phase 3 — Trailing-Stop Backtest

**Question.** The deployed chop gate avoids *entering* flat rounds, but its proven
blind spot (`date/2026-06-29-20-00.btc.loss.study`,
`2026-06-29-WIN-hours-13-18.study`) is **post-entry reversals**: the favorite clears
the gate at entry, then reverses by settle and we lose the whole stake. A
**trailing stop** would *exit* when the favorite collapses after entry. Does it
actually improve real PnL — beating the hold-to-settle baseline, surviving a
placebo, holding OOS, **and adding on top of the gate we actually deploy**?

**Answer: REJECT for deployment.** The trailing stop produces a positive *headline*
number on the full book (it clears the narrow baseline+placebo+OOS bar), but that
"win" is a **mirage** that three deployment-lens checks dismantle: the edge is
concentrated in the structurally-losing coins, it evaporates under ~1-2¢ of exit
slippage, and **it is NEGATIVE on the band+chop subset we actually trade**. The
favorite-longshot mechanism is intact: value comes from *holding* winners to par;
the stop shakes out more winner-dollars than loser-dollars everywhere the book is
already working.

Run: `python3 phase3_trailstop.py`. Deliverable code: `phase3_trailstop.py`
(imports `backtest_real.py` unmodified; reuses `phase2_entry.VolEnricher` for the
deployed chop gate).

---

## What was built (the new capability)

`backtest_real.py` only does **exact SKIP predicates**. A trailing stop is a
**mid-round EXIT**, which needs the **post-entry price path** of the favorite we
bought. `phase3_trailstop.py` adds that:

- **Entry→timeline map.** `entry_rem = market.end_ts − fill_ts`. Verified clean:
  all entry_rems in **[2.6, 149.1] s**, zero negatives — the bot enters in the first
  ~half of the 5-min round (median ~145 s to settle). Post-entry path = ticks with
  `rem < entry_rem`.
- **Favorite SELL price** (what we could exit into), the mirror of the harness's
  BUY convention: **YES bet → sell YES = `bid_p1`; NO bet → sell NO = `1 − ask_p1`**.
  At the first post-entry tick this sell-bid is on average **−0.013 below our fill**
  (we bought the ask, we sell the bid): an immediate round-trip already loses ~1.3¢
  to spread, and 86% of immediate exits lose money. This is the cost the stop pays
  on every exit.
- **Coverage.** 1129/1150 settled fills have ≥1 usable post-entry sell-bid (median
  ~128 ticks of path). The 21 with none (13 no post-entry tick, 8 no `end_ts`) can't
  be stopped → they hold to settle (= baseline), reported and excluded from any
  claim of improvement. Baseline cross-check: the harness `replay` = **−17.33** and
  this sim's hold-to-settle = **−17.33** (exact).

**Simulation (reuses real fill_price, shares, side, winner):** track the favorite's
sell-bid after entry; `peak` = running max. Trailing exit when `bid ≤ peak − trail`;
fixed-stop exit when `bid ≤ fill_price − stop`. Exit PnL = `shares·(exit_bid −
fill_price)`; if never triggered, hold to settle.

**Stated caveats.** (1) Exits fill at the **displayed bid — no exit slippage** (the
brief's "mildly optimistic" assumption). Size is tiny (median 2.07, max 20.1 shares)
so the book *probably* absorbs us, but the displayed bid is top-of-book and this
optimism flatters the stop — quantified in §4c below. (2) **1.74-day window**
(2026-06-27 → 06-29); OOS halves are ~0.9 days, so OOS is **directional, not
definitive**.

---

## 1+2. PnL table and the losers-saved / winners-shaken tradeoff

Baseline (hold-to-settle, n=1150): **−17.33**. Exit fills at displayed bid (optimistic).

| TRAIL | n_exit | PnL $ | vs base | losers-saved | winners-shaken | $saved | $lost | **net $** |
|------:|------:|------:|-------:|-----:|-----:|------:|------:|------:|
| 0.03 | 1001 | −23.35 | −6.02 | 224 | 777 | 332.98 | 339.00 | **−6.02** |
| 0.05 | 895 | −19.33 | −2.00 | 223 | 672 | 324.14 | 326.14 | **−2.00** |
| 0.08 | 811 | −18.47 | −1.15 | 223 | 588 | 313.92 | 315.07 | **−1.15** |
| 0.10 | 739 | −7.36 | +9.97 | 223 | 516 | 307.54 | 297.57 | **+9.97** |
| **0.15** | 597 | **+1.88** | **+19.21** | 223 | 374 | 280.22 | 261.01 | **+19.21** |

| FIXED stop | n_exit | PnL $ | vs base | losers-saved | winners-shaken | net $ |
|------:|------:|------:|-------:|-----:|-----:|------:|
| 0.05 | 746 | −22.02 | −4.69 | 224 | 522 | −4.69 |
| 0.08 | 642 | −6.72 | +10.60 | 223 | 419 | +10.60 |
| 0.10 | 594 | −2.57 | +14.76 | 223 | 371 | +14.76 |
| 0.15 | 499 | +5.83 | +23.16 | 222 | 277 | +23.16 |
| 0.20 | 426 | +11.84 | +29.17 | 222 | 204 | +29.17 |

**The tradeoff, read off the table.** `net$` is exactly delta-vs-baseline, split
into `$saved` (loser exits) minus `$lost` (winner shake-outs). Two facts decide
everything:

- **Losers-saved is capped at ~223 and flat across all levels** — there are only so
  many genuinely-collapsing losers to catch; even the loosest 3¢ trail finds them
  all. The dollars saved (~280–333) are also roughly flat.
- **Winners-shaken explodes as the trail tightens** (374 → 777) and the dollars lost
  with them (261 → 339). A *looser* trail (0.15) helps **only because it shakes out
  fewer winners**, not because it saves more losers.

So the wider the trail, the better — i.e. the rule works best when it does the
*least* stopping. That is the favorite-longshot signature: the optimum is "barely
ever exit." The exits that do fire are still **62.6% winners** (vs 80% overall);
held positions are 98.7% winners. The collapse signal is real but weak — it is
mostly shaking out winners.

---

## 3. Placebo (label-shuffle null)

Edge = `total_PnL(trail) − total_PnL(baseline)` in $. The price path is tied to the
market (left intact); only the settlement winner is shuffled (`B.placebo_shuffle`,
preserving prices/shares/win-rate). Best level **trail 0.15**:

```
real edge vs baseline = +19.21 $
placebo null (200 shuffles): mean −199.21  sd 13.11  range [−233.72, −165.65]
upper-tail p-value = 0.005  (real $-edge in the positive tail)
```

**Passes — but read it correctly.** Under random labels, the *same* exit timing is
catastrophic (−199): exits hit the base 80% win-rate, so you shake out winners
wholesale. The real labels beat that because post-entry price-collapse genuinely
predicts losses (62.6% < 80% win-rate among exits). The placebo confirms the signal
is **real and causal**, not a shuffle artifact. It does **not** certify the dollar
edge is *robust* or *deployable* — that's what §4b/4c/5 test, and where it fails.

---

## 4. Out-of-sample (train 60% / test 40% by ts)

Pick the trail on the train half, freeze, report on the held-out test half.

```
train baseline −16.19 (n=690)   test baseline −1.13 (n=460)
train sweep: 0.15 maximises train PnL (delta +15.68)  -> PICKED 0.15
FROZEN 0.15 on TEST: PnL +2.39 vs baseline −1.13 -> delta +3.53
  test losers-saved 88 / winners-shaken 165 / net$ +3.53
OOS VERDICT: HOLDS (beats baseline on test)
```

**Holds, weakly.** The frozen level beats the test baseline by **+$3.53** — but note
**88 losers saved vs 165 winners shaken**: even on the test half it shakes out
nearly 2× as many winners as losers, and survives only because the test baseline is
itself slightly negative. Caveat the 0.9-day test window.

---

## 4b. Per-coin attribution — the edge is in the LOSING coins

| coin | n | base $ | trail $ | delta | what it's doing |
|------|--:|-----:|-----:|-----:|------|
| btc | 288 | −11.54 | +1.21 | **+12.76** | cuts a structurally-LOSING coin |
| eth | 295 | +0.59 | +0.59 | −0.01 | ~neutral |
| sol | 302 | **+13.32** | +9.20 | **−4.12** | **HURTS the WINNING coin** |
| xrp | 265 | −19.70 | −9.12 | **+10.58** | cuts a structurally-LOSING coin |

The **entire** +$19 lives in **btc (+12.76) and xrp (+10.58)** — the two coins the
favorite book was *already bleeding money on*. On **sol**, the one coin where the
strategy actually works (+13.32 baseline), the stop **costs −4.12** by shaking
winners out. **This is not alpha; it's damage-mitigation on a losing book** — and a
plain coin-drop (xrp was the −$20 disaster) achieves the loss-cut without touching
the winners.

---

## 4c. Exit-slippage sensitivity — the edge is fragile

The base case fills exits at the **displayed bid (no slippage)**. The measured
round-trip spread is already ~1.3¢. Subtract a per-exit haircut to stress that
optimism (trail 0.15):

| exit haircut | trail PnL | vs baseline |
|---:|---:|---:|
| 0¢ | +1.88 | **+19.21** |
| 1¢ | −10.57 | +6.76 |
| 2¢ | −23.03 | **−5.70** |
| 3¢ | −35.48 | −18.15 |

**The entire edge evaporates within ~1-2¢ of exit slippage.** Polymarket's 1¢ tick
plus thin top-of-book on stop-driven exits (everyone's stop fires when price
collapses → the bid is thinnest exactly when we sell) make 1-2¢ realistic. The
optimistic "fill at displayed bid" assumption is doing **all** the work.

---

## 5. On top of the deployed gate — it HURTS (the real deployment question)

The chop gate already excludes flat rounds. What ships is a stop **on top of** the
band[0.78,0.84] ∧ chop(preabsmove≥0.00056) subset (reusing `phase2_entry.VolEnricher`,
look-ahead-controlled).

Gated subset: **n=163, hold-to-settle = +30.69** (the gate works — it's already
profitable).

| TRAIL on gated subset | n_exit | PnL $ | vs gated base | losers-saved | winners-shaken | net $ |
|------:|------:|------:|-------:|-----:|-----:|------:|
| 0.03 | 138 | +10.07 | −20.62 | 15 | 123 | −20.62 |
| 0.05 | 112 | +16.52 | −14.17 | 15 | 97 | −14.17 |
| 0.08 | 98 | +19.05 | −11.64 | 15 | 83 | −11.64 |
| 0.10 | 85 | +21.82 | −8.87 | 15 | 70 | −8.87 |
| 0.15 | 62 | +24.83 | **−5.85** | 15 | 47 | −5.85 |

**Every level is NEGATIVE.** On the rounds we actually trade, the stop saves only 15
losers but shakes out 47–123 winners, **cutting +30.69 down to at best +24.83**. The
chop gate already removed the flat rounds where reversals concentrate; what's left is
a clean favorite book, and a stop on it just **destroys winner-dollars**. This is the
decisive deployment result: **adding a trailing stop to the live config makes it
worse.**

---

## Verdict

| Check | Pass? | Detail |
|---|:--:|---|
| [1] Beats baseline (full book) | ✅ | best trail 0.15: +1.88 vs −17.33 |
| [2] Survives placebo | ✅ | p = 0.005 (signal real, not a shuffle artifact) |
| [3] Holds OOS | ✅ (weak) | frozen 0.15 test delta +3.53 (88 saved / 165 shaken) |
| [4] Robust to ~2¢ exit slip | ❌ | delta +19.21 @0¢ → **−5.70 @2¢** |
| [5] Adds on top of deployed gate | ❌ | **−5.85** on band+chop subset (HURTS) |
| [6] Edge not concentrated in losing coins | ❌ | all +edge in btc/xrp; sol (winner) −4.12 |

**RECOMMENDATION: REJECT — do not ship.** It clears the brief's narrow
baseline+placebo+OOS bar *on the full book*, but that headline is an artifact of the
losing coins (btc/xrp) dominating the aggregate. Under the deployment lens it fails
on every count that matters: it **hurts the gated subset we actually trade
(−5.85)**, the edge is **slippage-fragile** (gone at 2¢, and the round-trip spread is
already ~1.3¢), and it is **concentrated entirely in coins that should be dropped,
not stop-managed** — while it costs us on the one coin (sol) the book wins on.

This confirms the prior the brief warned of: favorite-longshot value comes from
**holding winners to par**. Post-entry collapse *is* weakly predictive (the placebo
proves it), but the exits are still 62% winners, so a stop shakes out more
winner-dollars than the loser-dollars it saves on any subset where the book is
already working. The blind spot (post-entry reversals) is better addressed
**pre-entry** — drop the losing coins, or strengthen the entry gate — than by a
mid-round exit that bleeds the spread and the winners.
