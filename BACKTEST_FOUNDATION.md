# Polybot Backtest Foundation (Phase 1)

**Goal.** Build a replay harness that reproduces our **real** trading P&L from the
raw ledger + settlement data, so that Phase-2 strategy changes can be tested
apples-to-apples against a baseline that is *actually true*. The motivating lesson:
an idealized paper backtest claimed **+EV** while real money was **~breakeven**. A
backtest is only trustworthy once it reproduces reality.

Deliverable: [`backtest_real.py`](backtest_real.py). Read-only data is copied to
`./boxdata/` (ledger.db, traded_ticks.db, sessions.csv, session_strategy.csv);
nothing on the box `34.92.235.71` was modified.

---

## 1. Reconciliation table (paper vs real-reconstruction vs harness)

All numbers are **realized** P&L: `shares*(1-fill_price)` if our side won, else
`-shares*fill_price`. Settlement winner comes from `polymarket.db sessions`
(market_id -> winner); LIVE fill rows carry no winner of their own.

| Coin | Paper signal book | **Harness @ brief snapshot (n=1111)** | Brief ground truth | **Harness current (n=1150, clean)** |
|------|------------------:|--------------------------------:|-------------------:|--------------------------:|
| btc  | +204.89 | **-6.77** | -6.8  | -11.54 |
| eth  |  +24.53 | **+0.56** | +0.6  |  +0.59 |
| sol  | +344.24 | **+14.17** | +14.2 | +13.32 |
| xrp  | -415.86 | **-19.70** | -19.7 | -19.70 |
| **TOT** | **+157.80** | **-11.73** | **~-12** | **-17.33** |
| win% | — | 80% | ~80% | 80% |
| n fills | 2297 sessions | 1111 | ~1115 | 1150 |

**The harness reproduces the brief's ground truth to $0.09 L1 across all four coins**
(`python3 backtest_real.py --snapshot 1111`). eth and xrp reproduce to the cent at
*every* time cutoff, which proves the methodology is exact; btc/sol track the brief
once you align the snapshot.

Two reconciliation levers were identified and are made explicit in the harness:

1. **Time snapshot.** The bot is *still trading*. The brief's numbers were a
   point-in-time snapshot at ~1111 settled fills; the current ledger has 1150
   clean settled fills. The harness reproduces the snapshot (`--snapshot 1111`)
   and the current state (default run). The "drift" btc -6.8 -> -11.5 etc. is
   purely additional trades after the snapshot, not a methodology gap.

2. **fill_price=0 artifacts.** 16 ledger rows have `fill_shares>0` but
   `fill_price=0`, `intended_price=NULL`, and no `order_id` -> placeholder rows,
   not genuine fills. Counted naively they fabricate **+$11** of "$0-cost free
   wins." The brief's snapshot *included* them (that's the basis that matches
   $0.09); the harness **excludes** them by default as the principled choice
   (`--include-artifacts` to keep). Lever: `-17.33` clean vs `-6.33` naive at the
   current ledger.

**Verdict: the harness is validated.** It reproduces real money to the cent given
the same time-snapshot and artifact convention.

---

## 2. Paper -> real friction decomposition

The paper "fav_hold" book is **session-level** (one row per multi-round session,
~587 sessions/coin) and **not per-market joinable** to live fills, so paper-vs-real
is honest only per-coin/total (the harness enforces this; see `load_paper_pnl`).
With that caveat, the paper(+157.8) -> real(~-12..-17) gap decomposes as:

| Driver | Magnitude | Effect |
|--------|-----------|--------|
| **(a) Stake size** | paper ~$25 vs real ~$1.66 notional (~15x) | Scales **magnitude, not sign**. Paper +157.8 / 15 ~= +$10, the same order as the real spread. Explains the headline-dollar gap, **not** the EV. |
| **(b) Selection + entry drift** | paper trades every session (~587/coin); live entered only ~290/coin, at mean price **0.80** with a tail to **0.97** | **Dominant driver.** Only **44%** of fills land in the stated [0.76,0.85] band; **26% above 0.85**. At ~0.80 a favorite is ~fairly priced, so per-share edge ~= 0. |
| **(c) No-fills (FAK)** | **94/1263 = 7.4%** of orders | Modeled as **trades that never happened** ($0, no position). Paper assumes a fill every time, over-counting opportunities. |
| **(d) Win-rate** | **80%** real wins | Equals the ~0.80 entry price -> **break-even by construction**; no skill premium over the favorite's price. |

**Net interpretation.** The paper book's positive total is almost entirely
**leverage (a)** applied to a **near-zero per-share edge (b/d)**. Strip the leverage
and the real per-share edge is ~0; trading is ~breakeven and dominated by
coin-level variance (sol +13, xrp -20). There is no robust edge in the picks
themselves at the prices we actually pay.

**No-fill handling (justified).** A FAK no-fill is recorded as a real order that
returned 0 shares. We treat it as a non-event: no entry, no P&L. The alternative
(impute a fill at the book ask) would invent positions the strategy never held and
re-introduce the paper-book optimism we are trying to eliminate. The 7.4% no-fill
rate is itself a friction Phase-2 execution changes can target, and the harness
already exposes the count.

---

## 3. The -$56 / -$67.87 residual

**Finding: the residual is an accounting artifact, not lost money.**

Read-only `data-api.polymarket.com` queries for the funder `0x7D31..5472`
(2026-06-29):

- `value?user=...` -> **$1.35** (cash collateral only)
- `positions?user=...` -> **240 REDEEMABLE positions**, `currentValue=0`,
  **size (= $ at redemption) sum $522.05**, cost basis $390.10; only 1 non-redeemable.

The data-api values **won-but-unredeemed** winning shares at **$0** until they are
claimed on-chain. So "account value" (cash) drastically understates economic value:
~$522 of winning tickets are sitting unredeemed. True economic value ~=
`$1.35 + $522.05 = ~$523`. The apparent account drawdown disappears once unredeemed
winnings are valued at par. The realized-P&L reconstruction (~-12) is the correct
*trading-performance* number.

Secondary point: the **$118.57 deposit baseline is itself shaky** —
`balance_history.tsv`'s first reading is **$113.79** and it bounces $103-$123, so
the baseline carries ~+/-$10 of its own error. Only 4 LIVE fills are genuinely
unsettled (cost basis **$5.00**), far too small to matter.

**To fully close** (the one thing not in local data): the Polymarket
**deposit/withdrawal history** for `0x7D31..5472` (exact USDC in/out). Everything we
*can* see is consistent with **no real outflow** — the money is unredeemed
winnings, not losses.

---

## 4. What the harness CAN and CANNOT evaluate trustworthily

**CAN (exact, zero simulation error) — use `replay(predicate=...)`:**
- **Skip/gate changes** that only *remove* trades we really made: tighter entry
  band, coin drop/re-weight, hour/spread/vol gates, kill-switches. These reuse the
  **real fill price, real shares, real winner** -> exact counterfactual P&L.
- **Stake re-sizing** on the same trades (`fixed_shares=`), since payoff is linear
  in shares.

**CANNOT (or only with a reported error) — `replay(entry_fn=...)`:**
- **Different-entry** changes (enter earlier/later/at a different price). The
  tick-based entry simulator was **validated and found unfaithful**: "first tick in
  band" enters ~0.08 too cheap on average (it doesn't reproduce the bot's real
  timing/momentum/round logic), giving a large aggregate error
  (`--validate-sim`). **Do not** use raw tick-sim entries as a baseline. It is a
  research probe only, and any result from it must be reported *with* its
  calibration error. The exact-reuse path is error-0 by construction.
- **Anything requiring spot/vol** (the Phase-2 chop/low-vol gate) is *gateable*
  exactly **iff** the gate variable is computable from data we have at decision
  time (ticks carry `spot`/`strike`; Binance klines are available). The *decision*
  to skip is exact; just don't simulate a *new entry price*.

**Cross-cutting caveat:** paper and real are session-vs-market level — compare
per-coin/total only, never per-market.

---

## 5. Placebo wiring for Phase 2 (mandatory discipline)

We have been repeatedly burned by gates (floor/ceiling/hour/spread) that looked
+EV but whose "edge" a label-shuffle placebo reproduced. The harness wires this in:

- `placebo_shuffle(fills, seed)` permutes the **win/loss outcome** across markets
  while keeping prices, shares, and base win-rate intact, destroying any real
  selection-variable -> outcome correlation.
- `gate_delta_test(fills, predicate, name, seeds=200)` measures a gate's edge as
  `pnl/trade(gated) - pnl/trade(all)`, computes it on real labels, then builds a
  null from N placebos, and reports an empirical **p-value**. A gate is credible
  only if its real edge sits in the tail of the placebo null **and** holds
  out-of-sample.

Demonstration (`python3 backtest_real.py --placebo`):

| Gate | real edge/trade | placebo null (mean ± sd) | p-value | verdict |
|------|----------------:|-------------------------:|--------:|---------|
| tighter band [0.78,0.84] | +0.102 | -0.034 ± 0.041 | 0.005 | CREDIBLE (in-sample; needs OOS) |
| drop xrp | +0.018 | +0.006 ± 0.012 | 0.189 | NOISE |
| tighter band [0.76,0.80] | -0.016 | +0.031 ± 0.045 | 0.856 | NOISE |

The placebo correctly flags two of three example gates as indistinguishable from a
coin-flip. The one survivor ([0.78,0.84], p=0.005) is exactly the kind of candidate
Phase 2 must then re-test **out-of-sample** before believing — band gates are the
prime suspects for in-sample overfit. Coin-weight changes (drop xrp) are a
selection on identity, not a per-trade signal, so they must be judged OOS
(next-period), not by placebo.

---

## How to run

```bash
python3 backtest_real.py                       # current baseline + friction + residual
python3 backtest_real.py --snapshot 1111       # reproduce the brief's ground truth ($0.09 L1)
python3 backtest_real.py --include-artifacts    # keep fill_price=0 rows (the naive -6.33 basis)
python3 backtest_real.py --validate-sim         # tick entry-simulator calibration error
python3 backtest_real.py --placebo              # label-shuffle gate acceptance test
python3 backtest_real.py --examples             # example Phase-2 gate re-evaluations (in-sample)
```

Phase-2 changes plug in as `predicate=` (skip/gate, exact) or `entry_fn=`
(re-price, with reported error), and **every** proposed gate goes through
`gate_delta_test` + an out-of-sample split before it is believed.
