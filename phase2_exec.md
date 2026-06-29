# Phase 2 — The Execution Ceiling of the Polybot Strategy

**Question.** Of the paper(+157.8) → real(−17.3) gap, how much P&L is recoverable by
**filling better** (an execution fix) versus how much is **fundamental** (near-zero
edge at the ~0.80 prices we trade)?

**Method.** Reuse the validated Phase-1 harness `backtest_real.py` unmodified
(predicate-mode replay = exact: real fill, real shares, real winner). The brief's
per-fill book fields live on the paired **INTENT** row, not the FILL row, joined 1:1
by `trade_id`:

| field | row | meaning |
|---|---|---|
| `intended_price`, `intended_shares`, `book_bid/ask`, `book_bid_sz/ask_sz` | INTENT | decision-time book (YES-token coords) |
| `fill_price`, `fill_shares`, `paper_price` | FILL | what we got |

Verified convention: `intended_price == favorite ask` (YES → `book_ask`, NO → `1−book_bid`).
The bot **crosses the spread to take the favorite ask.** So slippage `= fill_price − intended_price`.
Run: `python3 phase2_exec.py`.

**Baseline reproduced:** settled n=1150, pnl **−17.33**, win% **80%**, stake $1928 (~$1.68/trade).

---

## 1. Slippage decomposition — it is *strategy*, not *execution*

`slip = fill_price − intended_price` (favorite coords), n=1150:

- mean **−0.0008**, median **0.000**, sd 0.034. **49% exact**, 31% overpaid, 21% underpaid → **symmetric, mean-zero noise.**
- `intended_price` mean **0.807** ≈ `fill_price` mean **0.806**. The 0.80 entry is the **bot's INTENT**, not drift introduced at fill.
- Slippage only *widens* the fill distribution around the intent (in-band 0.76–0.85: intent 61% → fill 44%), at **~zero net $ cost**.
- **$ impact = −1.76** net over 1149 normal fills (overpay-leg +15.3 vs underpay-leg −17.0, ~cancel). (One pathological `eth NO fill=0.083, 20.1sh` row is unsettled and excluded; it is the only fill >5 shares.)

**Verdict: the entry drift to 0.80 is a STRATEGY CHOICE, not a slippage defect.**
There is essentially no slippage dollar to recover. The lever is the *intended* price (entry-quality agent's turf), not execution.

## 2. No-fill adverse selection — neither cost nor feature (immaterial)

96 FAK no-fills (all settled). Would-have-won **86%** vs filled **80%**. Counterfactual P&L
had they filled at intended (2.08 sh) = **+13.7 over 96 trades (+0.143/trade)** vs the fills
we took at **−0.015/trade**. So the no-fills lean *slightly WINNER* — FAK is mildly
*costing* us, not saving us. **But the magnitude is trivial** (+$13.7 over 96 trades on a
$1928 book; binomial P(wins≤83|p=0.80)=0.96, i.e. not significantly different). **7.4% no-fill
is NOT a material P&L lever** in either direction.

## 3. Fill adverse selection — losers do NOT cluster on bad books

Winners (920) vs losers (230): **spread identical** (median 0.010 both), **depth similar**
(median ask_sz 29 vs 31), **book imbalance flat**. Win% is **flat across spread buckets
(78–83%) and across ask-depth quartiles (78–81%)**. The *only* separator is **price**:
losers' median fill **0.77** vs winners' **0.81**. Win% tracks price almost exactly
(0.76–0.82→80%, 0.82–0.88→87%, 0.88+→92%). **No microstructure adverse selection** — the
favorite is fairly priced and loss is the fair coin-flip the price implies.

## 4. Recoverable ceiling — execution-recoverable ≈ $0

| counterfactual | Δ vs base −17.33 | note |
|---|---:|---|
| **zero slippage** (fill at intended) | **−16.2** | slippage is mean-zero noise; "fixing" it does nothing real (sign is artifact of which side over/underpaid) |
| **price CAP** (drop fill > 0.85) | **−9.5** | **WRONG SIGN** — the high tail is the *winners*; capping HURTS |
| **price FLOOR** (drop fill < 0.78) | **+34.9** (in-sample) | the real lever, but it's an **entry-price** gate, not execution |
| fundamental | the −17.33 itself | net per-share edge **−0.6¢ ≈ 0** (win% 80.0 vs price 80.6) |

**The naive "we get filled too high" intuition is BACKWARDS.** Losses concentrate at the
**CHEAP** end: fills < 0.76 lose **−34.7** (−0.10/trade); fills > 0.88 *make* +2.3. Reason
(monotone, OOS-stable, all 4 coins): **cheap favorites are OVERPRICED** — we pay ~0.70 for
something that wins only 62%; win% − price is **−0.074** below 0.74 and **−0.032** in
0.74–0.78, turning **positive** (+0.054) only in 0.78–0.82.

## 5. Rules → placebo (label-shuffle, 300 seeds) + OOS (time split)

| rule | real edge/trade | placebo p | OOS train→test | verdict |
|---|---:|---:|---|---|
| EXEC max_slip ≤ 0.02 (deployed-ish) | −0.0205 | 1.000 | −0.018 → −0.023 | **dead** (consistently hurts) |
| EXEC max_slip ≤ 0.00 | −0.0522 | 1.000 | −0.042 → −0.062 | **dead** |
| EXEC min ask depth ≥ 20 | −0.0055 | 0.834 | −0.005 → −0.006 | **dead** |
| EXEC max spread ≤ 0.02 | −0.0003 | 0.708 | +0.001 → −0.001 | **dead** |
| EXEC price cap ≤ 0.85 | −0.0101 | 1.000 | −0.025 → +0.002 | **dead/unstable** |
| **PRICE floor fill ≥ 0.78** | **+0.0685** | **0.003** | **+0.093 → +0.047** | **CREDIBLE + OOS-stable** |

**Every execution-native rule (slip / depth / spread / cap) is NOISE or worse** —
negative real edge, placebo p ≈ 1.0. The deployed `max_slip_below` has already harvested
the (nil) slippage win; tightening it further only removes good trades. The **only**
surviving rule is the **price floor ≥ 0.78**, which is an **entry-quality / selection**
gate, not an execution one (it skips systematically overpriced cheap favorites).

---

## Bottom line

- **Slippage decomposition:** net slippage is **mean-zero noise** (mean −0.0008, 49% exact, $ impact −1.8). The 0.80 entry is **intent, not slippage**. Execution-side, there is nothing to recover.
- **No-fill verdict:** the 7.4% FAK no-fills lean *slightly winner* (would-have-won 86% vs 80%), so they are a **tiny cost, not a feature** — but immaterial (+$13.7/96 trades, not significant). Leave the FAK behavior alone.
- **Recoverable vs fundamental split:** **execution-recoverable ≈ $0** (slippage noise; depth/spread/cap all fail placebo). The realized **−17.33 is ~entirely fundamental** — net per-share edge −0.6¢, a fair coin-flip whose realized P&L is coin variance (sol +, xrp −).
- **Surviving rule:** the **only** placebo-credible (p=0.003), OOS-stable rule is a **price FLOOR ≥ 0.78** (drop the overpriced cheap-favorite tail), worth ~+0.05/trade OOS. It is an **entry-price** lever (hand it to the entry-quality agent), **not** an execution fix. Note the high-price tail the brief flagged as "worse odds" is actually the *winning* tail — do **not** cap it.
