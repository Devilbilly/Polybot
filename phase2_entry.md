# Phase 2 — Entry-Quality Gates (placebo + OOS)

Built on the validated Phase-1 harness (`backtest_real.py`, imported, **not**
modified). All gates are SKIP-type predicates → they reuse the **real fill price,
real shares, real winner**, so the counterfactual P&L is **EXACT** (zero simulation
error; foundation §4). No entry was re-priced (the tick sim is ~0.08 too cheap and
research-only).

Run: `python3 phase2_entry.py`. Binance 1-min klines were pulled once over the full
ledger span and cached to `boxdata/binance_klines_cache.json` (offline thereafter).

**Data.** 1150 settled fills, **only 1.74 days** (2026-06-27 16:47 → 06-29 10:28
UTC). Chronological 60/40 split → train n=690 / test n=460. Because the window is so
short, every survivor is additionally re-checked at 50/60/70% split points.

**Metric.** The headline −$17.33 economic loss is an accounting artifact (won-but-
unredeemed positions, foundation §3), so gates are judged by **per-trade margin**:
`pnl/trade` and the **win-minus-price margin** = `win% − mean_entry_price` (>0 ==
beating the favorite's own line). Baseline (no gate): pnl/t = **−0.0151**,
win-price margin = **−0.0061** (80% win at 0.806 avg ≈ break-even by construction).

**Acceptance rule (mandated).** A gate is REAL only if it (a) beats its 200-seed
label-shuffle placebo (p<0.05) **AND** (b) holds OOS on the held-out test half.

---

## Headline finding — the brief's price thesis is WRONG at the realized level

The brief assumed the >0.85 tail is a "no-edge zone." The realized data says the
**opposite**. Win-minus-price margin by entry-price bucket (full sample):

| entry bucket | n | win% | avg price | win−price margin | pnl/trade |
|---|---:|---:|---:|---:|---:|
| [0.00, 0.76) | 348 | 68% | 0.725 | **−0.044** | −0.100 |
| [0.76, 0.80) | 237 | 76% | 0.781 | **−0.026** | −0.054 |
| [0.80, 0.85) | 264 | 86% | 0.827 | **+0.037** | +0.076 |
| [0.85, 0.90) | 157 | 93% | 0.879 | **+0.051** | +0.106 |
| [0.90, 1.01) | 144 | 90% | 0.925 | **−0.022** | −0.045 |

The losers are the **cheap "favorites" below 0.80** (62–69% win — they were *not*
real favorites) and the **extreme >0.90 tail**. The 0.80–0.90 zone is the **best**
part of the book. A price *ceiling* cuts the wrong end. This is why TEST 1 fails and
TEST 2 (a *two-sided band* that keeps the good middle and cuts both bad ends) wins.

---

## TEST 1 — PRICE-CAP (skip above a ceiling)  →  **REJECT**

| cap | fire% | win% | pnl/trade | win−price | in-sample edge |
|---:|---:|---:|---:|---:|---:|
| 0.88 | 82% | 77% | −0.020 | −0.008 | −0.005 |
| 0.85 | 74% | 76% | −0.032 | −0.014 | −0.017 |
| 0.82 | 61% | 74% | −0.043 | −0.019 | −0.028 |
| 0.80 | 52% | 72% | −0.071 | −0.032 | −0.055 |

- **In-sample edge: NEGATIVE at every ceiling.** Capping *removes winners* — the
  0.85–0.90 bucket (93% win) is the most profitable, so cutting it lowers the margin.
- **Placebo: p = 1.000** (real edge sits *below* the placebo null — worse than a
  coin-flip; the gate is destroying value, not noise).
- **OOS: FAILS** (train picks cap=0.88, test edge −0.006).
- **Verdict: REJECT.** A price ceiling is exactly backwards. The expensive
  favorites are fine; the cheap ones and the extreme tail are the problem.

---

## TEST 2 — BAND [0.78, 0.84]  →  **ACCEPT (survives placebo AND OOS)**

| | n | fire% | win% | entry | pnl/trade | win−price |
|---|---:|---:|---:|---:|---:|---:|
| in-sample | 314 | 27% | 86% | 0.815 | **+0.087** | **+0.042** |
| train | 152 | 22% | 86% | 0.813 | +0.100 | +0.049 |
| **test (OOS)** | 162 | 35% | 85% | 0.817 | **+0.074** | **+0.035** |

- **In-sample edge +0.102. Placebo p = 0.005** — re-confirms Phase-1 exactly.
- **OOS: HOLDS.** Held-out test edge **+0.076**, win-price margin delta **+0.036**.
  Phase-1's in-sample candidate is **not** overfit — it survives the held-out half.
- Robust across 50/60/70% splits (edge +0.086 / +0.076 / +0.079).
- **Fire-rate cost: ~27–35%** (keeps roughly a third of trades).
- **Verdict: ACCEPT.** Real, persistent. The mechanism is the bucket table above:
  cut the cheap-fake-favorite end (<0.78, negative margin) and the over-priced end
  (>0.84), keep the 0.80-ish sweet spot where favorites genuinely beat their line.

---

## TEST 3 — CHOP / LOW-VOL  →  **ACCEPT (decision-honest; survives placebo AND OOS)**

**Look-ahead control (critical).** We enter a **median 145 s before settle**, i.e.
~2.5 min into the 5-min round. The full-window 5-min move therefore contains ~2.4
min of price action **after** entry — using it is look-ahead (it silently selects
"rounds that already resolved in the favorite's direction"). The naive full-window
version scored a huge in-sample edge **for that reason**. The gate of record uses
**only the 1-min bars from round-start up to the bar we entered in** (median 3 bars);
nothing after the fill. This is the overfit trap the brief warned about — caught.

Two decision-honest metrics, skip rounds whose **pre-entry** move < threshold:

`preabsmove` = |close@entry − open@start| / open  (net directional, **primary**):

| thr | fire% | win% | pnl/trade | win−price | in-sample edge |
|---:|---:|---:|---:|---:|---:|
| 0.00038 | 70% | 87% | +0.090 | +0.044 | +0.105 |
| 0.00046 | 61% | 88% | +0.108 | +0.053 | +0.123 |
| **0.00056** | 51% | 90% | +0.126 | +0.061 | **+0.141** |

- **In-sample edge +0.141. Placebo p = 0.005** (CREDIBLE).
- **OOS: HOLDS.** Train picks thr=0.00056 (edge +0.174), frozen on test →
  **edge +0.101**, win-price margin delta **+0.049**, test win% 88%.
- `prerange` (realized range up to entry) also passes: in-sample edge +0.037,
  placebo **p=0.005**, OOS test edge **+0.028** — weaker but genuine and confirms
  the effect isn't a single-metric fluke.
- Robust across 50/60/70% splits (preabsmove edge +0.091 / +0.101 / +0.107).
- **Fire-rate cost: ~51% in-sample / ~62% on test** (keeps roughly two-thirds —
  much cheaper than the band).
- **Mechanism (real, not identity).** "Skip flat chop" means *don't buy a favorite
  in a round that has shown no directional conviction yet.* When the underlying has
  already moved with conviction before entry, the leading favorite holds (88–90%
  win). It is **not** just re-deriving price: held at a *fixed* price level inside
  the band (see TEST 4 independence check), chop still adds ~+0.10 pnl/trade.
- **Verdict: ACCEPT.** This is the lever with a real mechanism, and it survives the
  honest decision-time test.

---

## TEST 4 — COMBINED  (band [0.78,0.84]  AND  chop preabsmove≥0.00056)  →  **ACCEPT**

The two survivors are **largely independent** (Jaccard 0.22) and **additive**.
Chop adds edge *inside* the band-kept set (price held in [0.78,0.84]):

| | pnl/trade | win−price |
|---|---:|---:|
| band alone | +0.087 | +0.042 |
| **band AND chop** | **+0.188** | **+0.091** |

OOS (held-out test half):

| | n | fire% | win% | pnl/trade | win−price | OOS edge |
|---|---:|---:|---:|---:|---:|---:|
| test base | 460 | — | 81% | −0.003 | −0.001 | — |
| **test combo** | 106 | **23%** | **92%** | **+0.202** | **+0.097** | **+0.204** |

- **Placebo p = 0.005. OOS edge +0.204**, win-price margin delta **+0.098**.
- Robust across 50/60/70% splits (combo edge +0.175 / +0.204 / +0.203).
- **Fire-rate cost: ~23%** — you trade roughly 1 round in 4.
- **Verdict: ACCEPT** as the highest-margin configuration, with the explicit caveat
  that the 23% fire rate is a real cost: it converts a high-volume break-even book
  into a low-volume, high-margin one. Whether that's worth it depends on whether
  fixed costs / opportunity cost dominate (out of scope here).

---

## Bottom line

| gate | in-sample edge | placebo p | OOS test edge | win−price Δ (OOS) | fire% | verdict |
|---|---:|---:|---:|---:|---:|---|
| PRICE-CAP | −0.005…−0.055 | 1.000 | −0.006 | −0.003 | 52–88% | **REJECT** |
| BAND [0.78,0.84] | +0.102 | **0.005** | **+0.076** | +0.036 | 27–35% | **ACCEPT** |
| CHOP (preabsmove≥5.6e-4) | +0.141 | **0.005** | **+0.101** | +0.049 | 51–62% | **ACCEPT** |
| CHOP (prerange) | +0.037 | **0.005** | +0.028 | +0.014 | 70–79% | accept (weaker) |
| **COMBINED band AND chop** | +0.188 | **0.005** | **+0.204** | +0.098 | **23%** | **ACCEPT (best margin)** |

**Two entry gates survive placebo AND OOS** — a **two-sided price band [0.78,0.84]**
and a **decision-honest pre-entry chop filter** — and they are independent and
additive. The brief's *price-ceiling* thesis is **rejected**: the realized edge
lives in the 0.80–0.90 favorites; the losers are sub-0.80 fake-favorites and the
>0.90 extreme tail, so a one-sided cap cuts the wrong end.

**Caveats.** (1) Only **1.74 days** of data — survivors hold at 50/60/70% splits but
this is a short window; treat as a strong candidate to forward-test, not a closed
case. (2) Both gates raise mean entry price somewhat, but the within-band
independence check shows chop is not merely a price proxy. (3) Margins are per-trade;
at ~$1.66 notional the absolute $ are tiny — the value is the **+0.05 to +0.10
win-price margin** turning a break-by-construction book into one that beats its own
line. (4) The chop look-ahead trap (full-window vs pre-entry) was real and is the
single most important methodological catch here — the naive version would have
massively overstated the edge.
