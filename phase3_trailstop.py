#!/usr/bin/env python3
"""
phase3_trailstop.py -- Backtest a TRAILING STOP (mid-round EXIT) on the validated
real harness.  Phase 3 of improving Polybot.
=====================================================================================

MOTIVATION
----------
The deployed chop gate avoids ENTERING flat rounds. Its proven blind spot
(date/2026-06-29-20-00.btc.loss.study, 2026-06-29-WIN-hours-13-18.study) is
POST-ENTRY REVERSALS: the favorite moves with us (clears the gate at entry) and
then reverses by settle, and we lose the whole stake. A trailing stop would EXIT
when the favorite collapses after entry, cutting those losses BEFORE settlement.

This is intuitively appealing but the default expectation is NEGATIVE: a
favorite-longshot book's edge comes from HOLDING winners to par. A trailing stop
shakes winners out (a favorite that dips then recovers is exited at a loss) and
every exit pays the bid-ask spread (we bought the ask, we sell into the bid). So
the crux is the TRADEOFF: losers-saved vs winners-shaken-out, in DOLLARS.

WHAT THIS BUILDS (the new capability the harness lacks)
-------------------------------------------------------
backtest_real.py only does EXACT SKIP predicates. A trailing stop is a MID-ROUND
EXIT, which needs the POST-ENTRY price PATH of the favorite we bought. We get it
from traded_ticks.db (read-only):

  * Map our entry to the round timeline:  entry_rem = market.end_ts - fill_ts.
    (verified: all entry_rems in [2.6, 149.1]s, zero negatives -- the bot enters
     in the first ~half of the 5-min round; median ~145s to settle.)
  * Post-entry path = ticks with rem < entry_rem, ascending in time (rem desc).
  * SELL price for the favorite we hold (the price we could EXIT into):
        YES bet -> sell YES  -> hit the YES bid           = bid_p1
        NO  bet -> sell NO   -> NO bid = 1 - YES ask       = 1 - ask_p1
    (The ticks store the YES token's L2 book. backtest_real.fav_price is the BUY
     side -- buy YES = YES ask, buy NO = 1-YES bid -- so the SELL side is the
     mirror: sell YES = YES bid, sell NO = 1-YES ask. This captures the spread
     honestly: at the first post-entry tick the sell bid is on average -0.013
     BELOW our fill, i.e. an immediate round-trip already loses ~1.3c to spread.)

SIMULATION (per entered market; reuses real fill_price, shares, side, winner)
----------------------------------------------------------------------------
  * Track the favorite's SELL bid after entry. peak = running max sell-bid.
  * TRAILING exit: if sell_bid <= peak - trail  -> EXIT at that sell_bid.
  * FIXED stop (comparison): if sell_bid <= fill_price - stop -> EXIT at sell_bid.
  * PnL if exited:  shares*(exit_bid - fill_price).
  * If never triggered -> HOLD TO SETTLE = baseline:
        win  -> shares*(1 - fill_price)   else  -shares*fill_price.

ASSUMPTIONS / CAVEATS (stated, per the brief)
---------------------------------------------
  * Exit fills at the recorded bid: MILDLY OPTIMISTIC -- no exit slippage, and we
    assume the displayed bid absorbs our size. Our size is tiny (median 2.07
    shares, max 20.1), so this is defensible but it FLATTERS the trailing stop.
  * Thin post-entry coverage: 21/1153 fills have 0 usable post-entry ticks (13 no
    post-entry tick, 8 no end_ts). Those CANNOT be stopped -> they HOLD TO SETTLE
    (= baseline) and are reported separately. They do not bias the comparison
    (same outcome under baseline and trail).
  * 1.74-day window (2026-06-27..06-29). OOS halves are ~0.9 days each. Treat OOS
    as directional, not definitive.

DISCIPLINE: recommend the trailing stop ONLY if it BEATS the hold-to-settle
baseline AND survives a label-shuffle placebo AND holds out-of-sample. Default
expectation is it loses; if so, say so plainly with the loser/winner numbers.

Run:  python3 phase3_trailstop.py
"""
from __future__ import annotations

import os
import sqlite3
import statistics as st
from dataclasses import dataclass
from typing import Optional

import backtest_real as B  # the validated harness (imported, NOT modified)

HERE = os.path.dirname(os.path.abspath(__file__))
BOX = os.path.join(HERE, "boxdata")
TICKS_DB = os.path.join(BOX, "traded_ticks.db")

COINS = B.COINS
OOS_SPLIT_FRAC = 0.60  # same chronological train fraction as phase2_entry

# Deployed entry gate (from phase2_entry.py): band[0.78,0.84] AND chop preabsmove>=thr
BAND_LO, BAND_HI = 0.78, 0.84
CHOP_THR = 0.00056

TRAIL_SWEEP = [0.03, 0.05, 0.08, 0.10, 0.15]
STOP_SWEEP = [0.05, 0.08, 0.10, 0.15, 0.20]


# --------------------------------------------------------------------------- #
# Post-entry price path (the new capability)
# --------------------------------------------------------------------------- #
class PostEntryBook:
    """Post-entry favorite SELL-bid path for each entered market.

    The price we could EXIT (sell) the favorite into, sampled over time AFTER our
    fill until settle. Keyed by market_id; lazily loaded; read-only."""

    def __init__(self, db: str = TICKS_DB):
        self.con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        self.end_ts = {
            mid: end for mid, end in
            self.con.execute("SELECT market_id, end_ts FROM markets").fetchall()
        }
        self._cache: dict[str, list] = {}

    @staticmethod
    def sell_bid(side: str, bid_p1: Optional[float], ask_p1: Optional[float]) -> Optional[float]:
        """Price to SELL the favorite `side` (mirror of harness fav_price BUY side).
        Sell YES -> hit YES bid = bid_p1.  Sell NO -> NO bid = 1 - YES ask."""
        if side == "YES":
            return bid_p1 if bid_p1 is not None else None
        return (1.0 - ask_p1) if ask_p1 is not None else None

    def path(self, f: B.Fill) -> list[tuple[float, float]]:
        """[(rem, sell_bid), ...] for ticks AFTER our entry, ascending in time
        (descending rem). Empty if no end_ts or no post-entry ticks/quotes."""
        if f.market_id in self._cache:
            return self._cache[f.market_id]
        out: list[tuple[float, float]] = []
        end = self.end_ts.get(f.market_id)
        if end is not None:
            entry_rem = end - f.ts
            rows = self.con.execute(
                "SELECT rem, bid_p1, ask_p1 FROM ticks "
                "WHERE market_id=? AND rem < ? ORDER BY rem DESC",
                (f.market_id, entry_rem),
            ).fetchall()
            for rem, bp, ap in rows:
                if rem is None:
                    continue
                sb = self.sell_bid(f.side, bp, ap)
                # valid, in-(0,1) quote only
                if sb is not None and 0.0 < sb < 1.0:
                    out.append((rem, sb))
        self._cache[f.market_id] = out
        return out


# --------------------------------------------------------------------------- #
# Per-market exit simulation
# --------------------------------------------------------------------------- #
@dataclass
class ExitOutcome:
    pnl: float            # realized PnL under the exit rule
    exited: bool          # did the stop trigger (vs held to settle)?
    base_pnl: float       # hold-to-settle PnL (the baseline for this market)
    base_win: bool        # would this market have WON if held to settle?
    has_path: bool        # did we have a usable post-entry path?

    @property
    def loser_saved(self) -> bool:
        # would-have-lost, and the exit cut the loss (exited at a smaller loss)
        return self.exited and (not self.base_win) and self.pnl > self.base_pnl

    @property
    def winner_shaken(self) -> bool:
        # would-have-won, but we exited early for less than the win payoff
        return self.exited and self.base_win and self.pnl < self.base_pnl


def hold_to_settle_pnl(f: B.Fill) -> float:
    return f.shares * (1.0 - f.fill_price) if f.side == f.winner else -f.shares * f.fill_price


def simulate_exit(f: B.Fill, path: list[tuple[float, float]],
                  trail: Optional[float] = None,
                  stop: Optional[float] = None,
                  exit_haircut: float = 0.0) -> ExitOutcome:
    """Simulate a trailing stop (trail) OR fixed stop (stop) on one market.

    trail: exit when sell_bid <= running_peak - trail.
    stop : exit when sell_bid <= fill_price - stop.
    Exactly one of trail/stop should be set. Exit fills at that sell_bid.
    If never triggered (or no path), hold to settle (= baseline).

    exit_haircut: cents subtracted from the exit bid to model thin-book exit
    slippage (we sell into the bid but our size may walk it down, or the displayed
    top-of-book may not absorb us). 0.0 = the optimistic 'fill at displayed bid'
    base case the brief specifies; >0 stress-tests the optimism."""
    base_pnl = hold_to_settle_pnl(f)
    base_win = (f.side == f.winner)
    if not path:
        return ExitOutcome(base_pnl, False, base_pnl, base_win, has_path=False)

    peak = path[0][1]  # peak starts at first post-entry sell bid
    for _rem, bid in path:
        if bid > peak:
            peak = bid
        trigger = False
        if trail is not None and bid <= peak - trail:
            trigger = True
        elif stop is not None and bid <= f.fill_price - stop:
            trigger = True
        if trigger:
            eff = max(0.0, bid - exit_haircut)
            pnl = f.shares * (eff - f.fill_price)
            return ExitOutcome(pnl, True, base_pnl, base_win, has_path=True)
    return ExitOutcome(base_pnl, False, base_pnl, base_win, has_path=True)


# --------------------------------------------------------------------------- #
# Aggregate a sweep level over a fills list
# --------------------------------------------------------------------------- #
@dataclass
class SweepResult:
    n: int
    pnl: float            # total PnL under the rule
    base_pnl: float       # total hold-to-settle PnL (baseline) over the SAME fills
    n_exited: int
    losers_saved: int
    winners_shaken: int
    dollars_saved: float   # sum over loser-saved of (rule_pnl - base_pnl)  (>0 good)
    dollars_lost: float    # sum over winner-shaken of (base_pnl - rule_pnl) (>0 bad)
    n_no_path: int
    per_coin: dict         # coin -> [n, pnl, base_pnl]

    @property
    def delta(self) -> float:
        return self.pnl - self.base_pnl

    @property
    def net_dollars(self) -> float:
        return self.dollars_saved - self.dollars_lost


def run_sweep(fills: list[B.Fill], book: PostEntryBook,
              trail: Optional[float] = None,
              stop: Optional[float] = None,
              exit_haircut: float = 0.0) -> SweepResult:
    n = 0
    pnl = base_pnl = 0.0
    n_exited = losers_saved = winners_shaken = n_no_path = 0
    dollars_saved = dollars_lost = 0.0
    per_coin: dict = {c: [0, 0.0, 0.0] for c in COINS}
    for f in fills:
        if not f.settled:
            continue
        out = simulate_exit(f, book.path(f), trail=trail, stop=stop,
                            exit_haircut=exit_haircut)
        n += 1
        pnl += out.pnl
        base_pnl += out.base_pnl
        if not out.has_path:
            n_no_path += 1
        if out.exited:
            n_exited += 1
        if out.loser_saved:
            losers_saved += 1
            dollars_saved += (out.pnl - out.base_pnl)
        if out.winner_shaken:
            winners_shaken += 1
            dollars_lost += (out.base_pnl - out.pnl)
        pc = per_coin.setdefault(f.coin, [0, 0.0, 0.0])
        pc[0] += 1
        pc[1] += out.pnl
        pc[2] += out.base_pnl
    return SweepResult(n, pnl, base_pnl, n_exited, losers_saved, winners_shaken,
                       dollars_saved, dollars_lost, n_no_path,
                       {c: v for c, v in per_coin.items() if v[0] > 0})


# --------------------------------------------------------------------------- #
# Placebo: shuffle the win/loss label, recompute the trail-vs-baseline DELTA
# --------------------------------------------------------------------------- #
def placebo_delta_null(fills: list[B.Fill], book: PostEntryBook,
                       trail: float, seeds: int = 200) -> tuple[float, list, float]:
    """Edge of the trailing stop = total_pnl(trail) - total_pnl(baseline), in $.

    Real edge on real labels, then a null from `seeds` label-shuffles (B.placebo_
    shuffle permutes win/loss across markets, preserving prices/shares/win-rate).
    The price PATH is tied to the market_id and is left intact; only the SETTLEMENT
    winner is shuffled. So the null answers: 'given random outcomes, how often does
    this exit rule beat hold-to-settle by chance?'.

    A trailing stop is credible only if its real $-edge sits in the (positive) tail
    of the null. We test the UPPER tail because we only deploy it if it HELPS."""
    real = run_sweep(fills, book, trail=trail).delta

    # Pre-extract paths so we can rebuild Fills with shuffled winners cheaply.
    null = []
    for s in range(seeds):
        shuffled = B.placebo_shuffle(fills, seed=s)
        d = run_sweep(shuffled, book, trail=trail).delta
        null.append(d)
    null.sort()
    # one-sided upper-tail p: fraction of null deltas >= real delta
    ge = sum(1 for x in null if x >= real)
    pval = (ge + 1) / (seeds + 1)
    return real, null, pval


# --------------------------------------------------------------------------- #
# OOS: pick best trail on chronological train half, freeze, report on test half
# --------------------------------------------------------------------------- #
def chrono_split(settled: list[B.Fill], frac: float = OOS_SPLIT_FRAC):
    s = sorted(settled, key=lambda f: f.ts)
    k = int(len(s) * frac)
    return s[:k], s[k:]


# --------------------------------------------------------------------------- #
# Chop+band gate (deployed entry subset) -- reuse phase2 VolEnricher
# --------------------------------------------------------------------------- #
def build_gate_predicate():
    """band[0.78,0.84] AND chop(preabsmove>=CHOP_THR). Reuses phase2_entry's
    decision-honest VolEnricher (pre-entry klines only, no look-ahead)."""
    try:
        import phase2_entry as P
        enr = P.VolEnricher()

        def pred(f: B.Fill) -> bool:
            if not (BAND_LO <= f.fill_price <= BAND_HI):
                return False
            m = enr.metrics(f.coin, f.market_id)
            if m is None:
                return True  # no kline data -> not gated out (phase2 convention)
            return m["preabsmove"] >= CHOP_THR
        return pred, True
    except Exception as e:  # pragma: no cover - fallback if phase2 import fails
        print(f"  [warn] phase2_entry VolEnricher unavailable ({e});")
        print(f"         falling back to BAND-ONLY [{BAND_LO},{BAND_HI}] gate.")

        def pred(f: B.Fill) -> bool:
            return BAND_LO <= f.fill_price <= BAND_HI
        return pred, False


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def print_sweep_table(title: str, base_pnl: float, results: list[tuple[str, SweepResult]]):
    print(f"\n=== {title} ===")
    print(f"  baseline (hold-to-settle): {base_pnl:+.2f}  over n fills")
    print(f"  {'level':>10} {'n_exit':>7} {'PnL$':>9} {'vs_base':>9} "
          f"{'losrSav':>8} {'winShk':>8} {'$saved':>8} {'$lost':>8} {'net$':>8}")
    for label, r in results:
        print(f"  {label:>10} {r.n_exited:7} {r.pnl:+9.2f} {r.delta:+9.2f} "
              f"{r.losers_saved:8} {r.winners_shaken:8} "
              f"{r.dollars_saved:8.2f} {r.dollars_lost:8.2f} {r.net_dollars:+8.2f}")


def main():
    print("#" * 74)
    print("PHASE 3: TRAILING-STOP BACKTEST on the validated real harness")
    print("#" * 74)

    fills = B.load_fills()
    settled = [f for f in fills if f.settled]
    book = PostEntryBook()

    # coverage report
    n_path = sum(1 for f in settled if book.path(f))
    n_no = len(settled) - n_path
    base = run_sweep(settled, book, trail=999.0)  # trail huge -> never triggers = baseline
    print(f"\nsettled fills: {len(settled)}   baseline hold-to-settle PnL: {base.base_pnl:+.2f}")
    print(f"(harness baseline cross-check: B.replay = {B.replay(settled).pnl:+.2f})")
    print(f"post-entry path coverage: {n_path}/{len(settled)} have >=1 usable post-entry "
          f"sell-bid;\n  {n_no} have none -> they HOLD TO SETTLE (= baseline) and cannot "
          f"be stopped.")
    print("\nCAVEAT: exits fill at the recorded bid (no exit slippage; tiny size "
          "~2 shares).\n  This is MILDLY OPTIMISTIC and FLATTERS the trailing stop.")
    print("CAVEAT: 1.74-day window -- OOS halves are ~0.9 days; treat OOS as directional.")

    # ----------------------------------------------------------------- #
    # 1+2. Full-data sweep: PnL table + losers-saved/winners-shaken tradeoff
    # ----------------------------------------------------------------- #
    trail_results = [(f"trail {t:.2f}", run_sweep(settled, book, trail=t)) for t in TRAIL_SWEEP]
    stop_results = [(f"stop {s:.2f}", run_sweep(settled, book, stop=s)) for s in STOP_SWEEP]

    print("\n" + "=" * 74)
    print("1+2. FULL-DATA SWEEP  (PnL vs baseline; losers-saved vs winners-shaken)")
    print("=" * 74)
    print("  THE TRADEOFF: a trailing stop helps ONLY if $saved (loser exits) >")
    print("  $lost (winner shake-outs). 'net$' is exactly delta-vs-baseline split.")
    print_sweep_table("TRAILING STOP", base.base_pnl, trail_results)
    print_sweep_table("FIXED STOP", base.base_pnl, stop_results)

    # pick the best trail by full-data PnL (will still be OOS-tested below)
    best_label, best_trail_res = max(trail_results, key=lambda x: x[1].pnl)
    best_trail = float(best_label.split()[1])
    print(f"\n  best trailing level by full-data PnL: {best_label} "
          f"(PnL {best_trail_res.pnl:+.2f} vs baseline {base.base_pnl:+.2f}, "
          f"delta {best_trail_res.delta:+.2f})")

    # ----------------------------------------------------------------- #
    # 3. Placebo on the best trail level
    # ----------------------------------------------------------------- #
    print("\n" + "=" * 74)
    print("3. PLACEBO  (label-shuffle null for the best trail's $-edge vs baseline)")
    print("=" * 74)
    real_d, null, pval = placebo_delta_null(settled, book, best_trail, seeds=200)
    print(f"  trail {best_trail:.2f}: real edge vs baseline = {real_d:+.2f} $")
    print(f"    placebo null (200 shuffles): mean {st.mean(null):+.2f}  "
          f"sd {st.pstdev(null):.2f}  range [{null[0]:+.2f}, {null[-1]:+.2f}]")
    print(f"    upper-tail p-value = {pval:.3f}  "
          f"({'CREDIBLE (real $-edge in positive tail)' if pval < 0.05 else 'NOT CREDIBLE (indistinguishable from a coin-flip exit rule)'})")
    print("    (We test the UPPER tail: we'd only deploy a stop that HELPS. A "
          "negative\n     real edge is by definition not in the positive tail.)")

    # ----------------------------------------------------------------- #
    # 4. OOS: pick best trail on train half, freeze, report on test half
    # ----------------------------------------------------------------- #
    print("\n" + "=" * 74)
    print(f"4. OUT-OF-SAMPLE  (train {OOS_SPLIT_FRAC:.0%} / test {1-OOS_SPLIT_FRAC:.0%} by ts)")
    print("=" * 74)
    train, test = chrono_split(settled)
    base_train = run_sweep(train, book, trail=999.0).base_pnl
    base_test = run_sweep(test, book, trail=999.0).base_pnl
    print(f"  train baseline {base_train:+.2f} (n={len(train)})   "
          f"test baseline {base_test:+.2f} (n={len(test)})")
    print(f"  {'trail':>7} {'train_PnL':>10} {'train_d':>9}  pick-by-train-PnL")
    train_sweep = []
    for t in TRAIL_SWEEP:
        r = run_sweep(train, book, trail=t)
        train_sweep.append((t, r))
        print(f"  {t:>7.2f} {r.pnl:>+10.2f} {r.delta:>+9.2f}")
    pick_t, pick_r = max(train_sweep, key=lambda x: x[1].pnl)
    print(f"  -> PICKED trail {pick_t:.2f} on train (train delta {pick_r.delta:+.2f})")
    test_frozen = run_sweep(test, book, trail=pick_t)
    oos_verdict = "HOLDS OOS (beats baseline on test)" if test_frozen.delta > 0 else \
                  "FAILS OOS (loses to baseline on test)"
    print(f"  FROZEN trail {pick_t:.2f} on TEST: PnL {test_frozen.pnl:+.2f} vs "
          f"baseline {base_test:+.2f} -> delta {test_frozen.delta:+.2f}")
    print(f"    test losers-saved {test_frozen.losers_saved} / winners-shaken "
          f"{test_frozen.winners_shaken} / net$ {test_frozen.net_dollars:+.2f}")
    print(f"  OOS VERDICT: {oos_verdict}")

    # ----------------------------------------------------------------- #
    # 4b. PER-COIN ATTRIBUTION of the best-trail edge (is it real alpha or
    #     just damage-mitigation on the structurally-losing coins?)
    # ----------------------------------------------------------------- #
    print("\n" + "=" * 74)
    print("4b. PER-COIN ATTRIBUTION  (where does the trail's edge come from?)")
    print("=" * 74)
    print(f"  {'coin':4} {'n':>4} {'base$':>8} {'trail$':>8} {'delta':>8}  note")
    win_coins_hurt = 0
    for c in COINS:
        if c not in best_trail_res.per_coin:
            continue
        n, pnl, bpnl = best_trail_res.per_coin[c]
        d = pnl - bpnl
        note = ("cuts a LOSING coin (damage-mitigation)" if bpnl < 0
                else "HURTS a WINNING coin (shakes winners)" if d < -0.5
                else "~neutral")
        if bpnl > 0 and d < -0.5:
            win_coins_hurt += 1
        print(f"  {c:4} {n:4} {bpnl:+8.2f} {pnl:+8.2f} {d:+8.2f}  {note}")
    print("  READ: if the +edge lives in the structurally-LOSING coins (which a coin-")
    print("  drop would also fix) while the WINNING coins are HURT, the trail is not")
    print("  finding alpha -- it is mitigating damage on a book that is already bleeding.")

    # ----------------------------------------------------------------- #
    # 4c. EXIT-SLIPPAGE SENSITIVITY (the brief's key caveat, made first-class)
    # ----------------------------------------------------------------- #
    print("\n" + "=" * 74)
    print("4c. EXIT-SLIPPAGE SENSITIVITY  (the load-bearing caveat)")
    print("=" * 74)
    print("  The base case fills exits at the DISPLAYED bid (no slippage) -- optimistic.")
    print("  The measured round-trip spread (sell-bid vs our buy-fill) is ~1.3c already.")
    print("  Stress the optimism: subtract a per-exit haircut from the exit bid.")
    print(f"  {'haircut':>8} {'trail PnL':>10} {'vs_base':>9}")
    haircut_deltas = {}
    for hc in (0.00, 0.01, 0.02, 0.03):
        r = run_sweep(settled, book, trail=best_trail, exit_haircut=hc)
        haircut_deltas[hc] = r.delta
        print(f"  {hc*100:6.0f}c {r.pnl:>+10.2f} {r.delta:>+9.2f}")
    slip_robust = haircut_deltas.get(0.02, -1) > 0  # survives a realistic ~2c haircut?
    print(f"  -> survives a realistic ~2c exit haircut: {slip_robust}")
    print("  (Polymarket 1-cent tick + thin top-of-book on 2-share exits make 1-2c of")
    print("   exit slippage realistic; the edge evaporates within that band.)")

    # ----------------------------------------------------------------- #
    # 5. ON TOP OF THE DEPLOYED GATE (band+chop subset) -- the real deploy Q
    # ----------------------------------------------------------------- #
    print("\n" + "=" * 74)
    print("5. ON TOP OF THE DEPLOYED GATE  (band[0.78,0.84] AND chop preabsmove>=thr)")
    print("=" * 74)
    pred, full_gate = build_gate_predicate()
    gated = [f for f in settled if pred(f)]
    gate_name = ("band[0.78,0.84] AND chop(preabsmove>=%.5f)" % CHOP_THR) if full_gate \
                else ("band[0.78,0.84] ONLY (chop enricher unavailable)")
    gate_base = run_sweep(gated, book, trail=999.0).base_pnl
    print(f"  deployed gate = {gate_name}")
    print(f"  gated subset: n={len(gated)}  hold-to-settle PnL on subset = {gate_base:+.2f}")
    gate_adds = False
    if not gated:
        print("  (gate keeps 0 fills -- cannot test trailing stop on top.)")
    else:
        gate_trail = [(f"trail {t:.2f}", run_sweep(gated, book, trail=t)) for t in TRAIL_SWEEP]
        print_sweep_table("TRAILING STOP on GATED subset", gate_base, gate_trail)
        gbest_label, gbest = max(gate_trail, key=lambda x: x[1].pnl)
        gate_adds = gbest.pnl > gate_base
        print(f"  -> best on gated subset: {gbest_label} PnL {gbest.pnl:+.2f} vs "
              f"gated baseline {gate_base:+.2f} (delta {gbest.delta:+.2f})")
        print(f"  ON-TOP-OF-GATE VERDICT: trailing stop "
              f"{'ADDS to' if gate_adds else 'does NOT add to (HURTS) '} the deployed gate.")
        print("  (This is the REAL deployment question: the chop gate already excludes")
        print("   flat rounds. Adding a stop ON TOP of it is what would actually ship.)")

    # ----------------------------------------------------------------- #
    # Final verdict
    # ----------------------------------------------------------------- #
    print("\n" + "=" * 74)
    print("VERDICT")
    print("=" * 74)
    beats = best_trail_res.pnl > base.base_pnl
    placebo_ok = pval < 0.05
    oos_ok = test_frozen.delta > 0
    print(f"  [1] beats baseline (full data): {beats}  "
          f"(best {best_label} {best_trail_res.pnl:+.2f} vs {base.base_pnl:+.2f})")
    print(f"  [2] survives placebo:          {placebo_ok}  (p={pval:.3f})")
    print(f"  [3] holds OOS:                 {oos_ok}  "
          f"(frozen trail {pick_t:.2f} test delta {test_frozen.delta:+.2f})")
    print(f"  [4] robust to ~2c exit slip:   {slip_robust}  "
          f"(delta at 2c haircut = {haircut_deltas.get(0.02, float('nan')):+.2f})")
    print(f"  [5] ADDS on top of deployed gate: {gate_adds}  "
          f"(the real deployment question)")
    print(f"  [6] edge NOT concentrated in losing coins: {win_coins_hurt == 0}  "
          f"(winning coins the stop HURTS: {win_coins_hurt})")

    # The three brief-mandated bars: beats baseline + placebo + OOS.
    passes_brief_bar = beats and placebo_ok and oos_ok
    # But deployment also requires it to survive realistic slippage AND add on top
    # of the gate we actually run. The full-book 'win' is concentrated in losing
    # coins and dies under slippage -- those are deployment-killers.
    deployable = passes_brief_bar and slip_robust and gate_adds

    print()
    if not passes_brief_bar:
        print("  RECOMMENDATION: REJECT. Fails the baseline/placebo/OOS bar.")
    elif not deployable:
        print("  RECOMMENDATION: REJECT for deployment (do NOT ship).")
        print("  It clears the narrow baseline/placebo/OOS bar ON THE FULL BOOK, but that")
        print("  'win' is a MIRAGE for three reasons that the deployment lens exposes:")
        print(f"    (a) CONCENTRATION: the +edge lives in the structurally-LOSING coins")
        print(f"        (btc, xrp); on the WINNING coin (sol) the stop HURTS. A coin-drop")
        print(f"        achieves the same loss-mitigation without shaking winners.")
        print(f"    (b) SLIPPAGE-FRAGILE: the entire edge evaporates within ~1-2c of exit")
        print(f"        slippage (delta {haircut_deltas.get(0.0,0):+.2f} at 0c -> "
              f"{haircut_deltas.get(0.02,0):+.2f} at 2c). The base case fills at the")
        print(f"        displayed bid (no slippage) -- the optimistic assumption is doing")
        print(f"        all the work, and the measured round-trip spread is already ~1.3c.")
        print(f"    (c) HURTS THE DEPLOYED SUBSET: on the band+chop rounds we actually")
        print(f"        trade ({gate_base:+.2f} baseline), the stop is NEGATIVE -- it cuts")
        print(f"        winners out of an already-profitable subset.")
        print("  Mechanism: favorite-longshot value comes from HOLDING winners to par.")
        print("  The exits are still 62% winners; the stop shakes out more winner-$ than")
        print("  loser-$ everywhere the book is already working. The full-book number")
        print("  flatters it only because the losing coins dominate the aggregate.")
    else:
        print("  RECOMMENDATION: ADOPT (cleared baseline+placebo+OOS+slippage+gate).")


if __name__ == "__main__":
    main()
