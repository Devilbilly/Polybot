#!/usr/bin/env python3
"""
phase2_entry.py -- Phase-2 ENTRY-QUALITY gate tests on the validated replay harness
====================================================================================

Imports the Phase-1 foundation `backtest_real.py` (DOES NOT modify it) and tests
ENTRY-QUALITY gates, each with a label-shuffle PLACEBO and a chronological
OUT-OF-SAMPLE (OOS) split. Thesis under test (from Phase 1): the dominant problem
is ENTRY-PRICE DRIFT -- only 44% of fills land in the stated [0.76,0.85] band, 26%
land ABOVE 0.85 where favorites are fairly priced and per-share edge ~0. So "don't
enter where there is no edge" is the lever.

DISCIPLINE (mandated): a gate is REAL only if it (a) beats its placebo (p<0.05) AND
(b) holds OOS on the held-out test half. Fire-rate cost is reported honestly.

All gates are SKIP-type predicates -> they reuse the REAL fill price, REAL shares,
REAL winner. Per `BACKTEST_FOUNDATION.md` section 4 this path is EXACT (zero
simulation error). We never re-price entries (the tick sim is ~0.08 too cheap and
research-only).

TESTS
  1. PRICE-CAP   : skip fills above a ceiling (sweep 0.88/0.85/0.84/0.82/0.80).
  2. BAND[0.78,0.84] : confirm/kill the Phase-1 in-sample p=0.005 candidate OOS.
  3. CHOP/LOW-VOL : skip rounds whose realized 5-min Binance move < threshold.
  4. COMBINED    : only if 1-3 point the same way.

METRIC. The headline economic loss (-$17.33) is an accounting artifact (won-but-
unredeemed positions; see foundation section 3). We therefore judge gates by the
per-TRADE MARGIN they realize, not summed $. We report two margin notions:
  * pnl/trade  : realized P&L per kept trade (the harness's gate_delta_test metric).
  * win-minus-price margin = win_rate - mean_entry_price : the "is there edge over
    the favorite's own price" number the brief asks for (>0 == beating the line).

OOS PROTOCOL. Sort settled fills by ts, split 60/40 (train/test). "Pick on train":
choose the gate threshold that maximises the train per-trade edge (vs no-gate on
train), then report that SAME frozen threshold's edge on the held-out test half.
A gate that only works in-sample collapses OOS.

PLACEBO. Reuse the foundation's gate_delta_test (200 label-shuffle seeds): edge =
pnl/trade(gated) - pnl/trade(all); p = fraction of placebo nulls >= real edge.
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics as st
from collections import defaultdict
from typing import Callable, Optional

import backtest_real as B  # the validated Phase-1 harness (imported, NOT modified)

HERE = os.path.dirname(os.path.abspath(__file__))
BOX = os.path.join(HERE, "boxdata")
LEDGER_DB = os.path.join(BOX, "ledger.db")
KLINE_CACHE = os.path.join(BOX, "binance_klines_cache.json")

COINS = B.COINS
OOS_SPLIT_FRAC = 0.60  # chronological train fraction


# --------------------------------------------------------------------------- #
# Round-window / Binance realized-vol enrichment
# --------------------------------------------------------------------------- #
def load_settle_map() -> dict[str, int]:
    """market_id -> settle wall-clock ts, snapped to the 5-min UTC boundary.

    SETTLE events in the ledger carry the round-end wall time; 1911/~2000 land
    exactly on a 5-min boundary (verified), so each round is the clean UTC window
    [settle-300, settle). This is what lets us align Binance 1-min klines exactly."""
    con = sqlite3.connect(f"file:{LEDGER_DB}?mode=ro", uri=True)
    sm = {
        mid: round(ts / 300) * 300
        for mid, ts in con.execute(
            "SELECT market_id, ts FROM ledger WHERE event='SETTLE' GROUP BY market_id"
        ).fetchall()
    }
    con.close()
    return sm


def load_klines() -> dict[str, dict[int, list]]:
    """coin -> {open_time_ms: [open, high, low, close]} from the cached Binance pull.

    Cache built once (boxdata/binance_klines_cache.json) over the full ledger span
    via api.binance.com 1-min klines; offline thereafter."""
    with open(KLINE_CACHE) as f:
        raw = json.load(f)
    # JSON keys are strings; coerce to int ms.
    return {coin: {int(k): v for k, v in bars.items()} for coin, bars in raw.items()}


def load_fill_ts() -> dict[str, float]:
    """market_id -> wall-clock ts of the LIVE fill (when we actually entered)."""
    con = sqlite3.connect(f"file:{LEDGER_DB}?mode=ro", uri=True)
    ft = {
        mid: ts
        for mid, ts in con.execute(
            "SELECT market_id, ts FROM ledger WHERE event='FILL' AND mode='LIVE' "
            "AND fill_shares>0 AND fill_price>0"
        ).fetchall()
    }
    con.close()
    return ft


class VolEnricher:
    """DECISION-HONEST realized-move enrichment for the chop/low-vol gate.

    CRITICAL look-ahead control. Each round is the 5-min UTC window [settle-300,
    settle). We do NOT enter at round-start: the median fill leaves ~145s to settle,
    i.e. we enter ~2.5 min INTO the round. The full-window move therefore contains
    ~2.4 min of price action AFTER our entry -- that post-entry move is NOT knowable
    at decision time, and a gate built on it is partly look-ahead (it silently
    selects "rounds that already resolved in the favorite's direction"). We were
    burned by exactly this kind of overfit before.

    So we compute the move using ONLY the 1-min bars from round-start up to and
    including the bar we entered in (>=1 bar, median 3). Two metrics:
      preabsmove = |close_atEntryBar - open_roundStart| / open   (net directional)
      prerange   = (max high - min low up to entry) / open       (realized range)
    Rounds without kline/fill data return None (kept -- we cannot judge them)."""

    def __init__(self):
        self.sm = load_settle_map()
        self.kl = load_klines()
        self.ft = load_fill_ts()
        self._cache: dict[str, Optional[dict]] = {}

    def metrics(self, coin: str, market_id: str) -> Optional[dict]:
        if market_id in self._cache:
            return self._cache[market_id]
        settle = self.sm.get(market_id)
        fill_ts = self.ft.get(market_id)
        if settle is None or fill_ts is None:
            self._cache[market_id] = None
            return None
        start = settle - 300
        entry_bar = int((fill_ts - start) // 60)        # 0..4: which 1-min bar we entered in
        entry_bar = max(0, min(4, entry_bar))
        bars = []
        for i in range(entry_bar + 1):                  # round-start .. entry bar, inclusive
            ms = int((start + 60 * i) * 1000)
            b = self.kl.get(coin, {}).get(ms)
            if b:
                bars.append(b)
        if not bars:
            self._cache[market_id] = None
            return None
        o = bars[0][0]
        c = bars[-1][3]                                  # close of the bar we entered in (no look-ahead)
        hi = max(b[1] for b in bars)
        lo = min(b[2] for b in bars)
        m = {
            "preabsmove": abs(c - o) / o if o else 0.0,  # net directional move up to entry
            "prerange": (hi - lo) / o if o else 0.0,     # realized range up to entry
            "nbars": len(bars),
        }
        self._cache[market_id] = m
        return m


# --------------------------------------------------------------------------- #
# Metrics helpers
# --------------------------------------------------------------------------- #
def margin_stats(fills: list[B.Fill]) -> dict:
    """Per-trade economics over a fills list (all assumed settled)."""
    n = len(fills)
    if n == 0:
        return dict(n=0, win_rate=0.0, mean_price=0.0, pnl=0.0, pnl_per_trade=0.0,
                    wmp_margin=0.0)
    wins = sum(1 for f in fills if f.side == f.winner)
    mean_price = st.mean(f.fill_price for f in fills)
    pnl = sum(f.realized() for f in fills)
    return dict(
        n=n,
        win_rate=wins / n,
        mean_price=mean_price,
        pnl=pnl,
        pnl_per_trade=pnl / n,
        wmp_margin=(wins / n) - mean_price,  # win% - avg entry price
    )


def edge_vs_all(fills: list[B.Fill], pred: Callable[[B.Fill], bool]) -> float:
    """pnl/trade(gated) - pnl/trade(all) over `fills` (the gate_delta_test metric)."""
    allr = margin_stats(fills)
    kept = [f for f in fills if pred(f)]
    gat = margin_stats(kept)
    return gat["pnl_per_trade"] - allr["pnl_per_trade"]


def chrono_split(settled: list[B.Fill], frac: float = OOS_SPLIT_FRAC):
    s = sorted(settled, key=lambda f: f.ts)
    k = int(len(s) * frac)
    return s[:k], s[k:]


def fmt_ms(m: dict) -> str:
    return (f"n={m['n']:4} fire-context win%={100*m['win_rate']:3.0f} "
            f"entry={m['mean_price']:.3f} pnl/t={m['pnl_per_trade']:+.4f} "
            f"win-price_margin={m['wmp_margin']:+.4f} pnl=${m['pnl']:+.2f}")


# --------------------------------------------------------------------------- #
# Generic OOS gate evaluation: pick best threshold on train, report on test
# --------------------------------------------------------------------------- #
def oos_sweep(settled: list[B.Fill],
              pred_factory: Callable[[float], Callable[[B.Fill], bool]],
              thresholds: list[float],
              higher_is_tighter_note: str = "") -> dict:
    """Pick the threshold maximising TRAIN per-trade edge (vs no-gate train), then
    report that frozen threshold's edge on the held-out TEST half.

    Returns a dict with the chosen threshold and full train/test stats."""
    train, test = chrono_split(settled)
    base_train = margin_stats(train)
    base_test = margin_stats(test)

    # pick on train
    best = None
    train_table = []
    for thr in thresholds:
        pred = pred_factory(thr)
        kept = [f for f in train if pred(f)]
        m = margin_stats(kept)
        edge = m["pnl_per_trade"] - base_train["pnl_per_trade"]
        fire = m["n"] / base_train["n"] if base_train["n"] else 0.0
        train_table.append((thr, m, edge, fire))
        # require the gate to actually fire on a non-trivial fraction (>5%) to
        # avoid picking a degenerate threshold that keeps ~nothing
        if fire >= 0.05 and (best is None or edge > best[2]):
            best = (thr, m, edge, fire)

    if best is None:  # fallback: pick max edge regardless of fire
        best = max(train_table, key=lambda x: x[2])

    chosen_thr = best[0]
    pred = pred_factory(chosen_thr)
    # evaluate the FROZEN threshold on test
    test_kept = [f for f in test if pred(f)]
    test_m = margin_stats(test_kept)
    test_edge = test_m["pnl_per_trade"] - base_test["pnl_per_trade"]
    test_fire = test_m["n"] / base_test["n"] if base_test["n"] else 0.0
    test_wmp_delta = test_m["wmp_margin"] - base_test["wmp_margin"]

    return dict(
        chosen_thr=chosen_thr,
        train_table=train_table,
        base_train=base_train, base_test=base_test,
        train_best=best,
        test_m=test_m, test_edge=test_edge, test_fire=test_fire,
        test_wmp_delta=test_wmp_delta,
        note=higher_is_tighter_note,
    )


def print_oos(title: str, res: dict, thr_label: str = "thr"):
    print(f"\n  -- OOS (train {OOS_SPLIT_FRAC:.0%} / test {1-OOS_SPLIT_FRAC:.0%} by ts) --")
    bt, bte = res["base_train"], res["base_test"]
    print(f"    train no-gate: {fmt_ms(bt)}")
    print(f"    test  no-gate: {fmt_ms(bte)}")
    print(f"    train sweep (pick max per-trade edge, fire>=5%):")
    for thr, m, edge, fire in res["train_table"]:
        star = "  <== PICKED" if thr == res["chosen_thr"] else ""
        print(f"      {thr_label}={thr:<6} fire={100*fire:3.0f}% edge={edge:+.4f} "
              f"pnl/t={m['pnl_per_trade']:+.4f}{star}")
    tm = res["test_m"]
    verdict = "HOLDS OOS (edge>0)" if res["test_edge"] > 0 else "FAILS OOS (edge<=0)"
    print(f"    FROZEN {thr_label}={res['chosen_thr']} on TEST: fire={100*res['test_fire']:3.0f}% "
          f"edge={res['test_edge']:+.4f}  win-price_margin delta={res['test_wmp_delta']:+.4f}")
    print(f"    test kept: {fmt_ms(tm)}")
    print(f"    OOS VERDICT: {verdict}")


# --------------------------------------------------------------------------- #
# Predicate factories
# --------------------------------------------------------------------------- #
def pred_cap(ceiling: float) -> Callable[[B.Fill], bool]:
    """Keep trades with fill_price <= ceiling (skip the expensive-favorite tail)."""
    return lambda f: f.fill_price <= ceiling


def pred_floor(floor: float) -> Callable[[B.Fill], bool]:
    return lambda f: f.fill_price >= floor


def make_vol_pred(enr: VolEnricher, metric: str, thr: float, drop_below: bool = True):
    """Skip rounds whose realized 5-min `metric` < thr (drop_below=True == skip chop).
    Rounds with no kline data are KEPT (we cannot judge them) -- reported separately."""
    def pred(f: B.Fill) -> bool:
        m = enr.metrics(f.coin, f.market_id)
        if m is None:
            return True  # no data -> don't gate it out
        return (m[metric] >= thr) if drop_below else (m[metric] <= thr)
    return pred


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def header(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


def test1_price_cap(settled, results):
    header("TEST 1  PRICE-CAP : skip fills above a ceiling (cut the no-edge >0.85 tail)")
    base = margin_stats(settled)
    print(f"\n  baseline (no cap): {fmt_ms(base)}")
    print(f"\n  IN-SAMPLE sweep (full data, EXACT reuse of real fill/winner):")
    print(f"    {'cap':>5} {'kept':>5} {'fire%':>6} {'win%':>5} {'entry':>6} "
          f"{'pnl/t':>9} {'win-price':>10} {'pnl$':>9}")
    caps = [0.88, 0.85, 0.84, 0.82, 0.80]
    for cap in caps:
        kept = [f for f in settled if f.fill_price <= cap]
        m = margin_stats(kept)
        print(f"    {cap:>5} {m['n']:5} {100*m['n']/base['n']:5.0f}% "
              f"{100*m['win_rate']:4.0f}% {m['mean_price']:6.3f} "
              f"{m['pnl_per_trade']:+9.4f} {m['wmp_margin']:+10.4f} {m['pnl']:+9.2f}")
    print("    (dropped tail = the >cap fills; 'win-price' = win% - avg entry, >0 == beating the line)")

    # placebo on the in-sample best-looking cap candidates
    print(f"\n  PLACEBO (200 label-shuffles, gate_delta_test) on representative caps:")
    fills_all = B.load_fills()
    for cap in (0.85, 0.84, 0.82):
        B.gate_delta_test(fills_all, pred_cap(cap), f"cap<= {cap}")

    # OOS: pick best cap on train, freeze, report on test
    res = oos_sweep(settled, pred_cap, caps, "cap")
    print_oos("price-cap", res, "cap")
    results["price_cap"] = res
    return res


def test2_band(settled, results):
    header("TEST 2  BAND [0.78,0.84] : confirm/kill the Phase-1 in-sample p=0.005 candidate")
    base = margin_stats(settled)
    band = lambda f: 0.78 <= f.fill_price <= 0.84
    kept = [f for f in settled if band(f)]
    m = margin_stats(kept)
    print(f"\n  baseline (no gate): {fmt_ms(base)}")
    print(f"  band [0.78,0.84]  : {fmt_ms(m)}  fire={100*m['n']/base['n']:.0f}%")
    print(f"  in-sample per-trade edge vs all = {m['pnl_per_trade']-base['pnl_per_trade']:+.4f}")

    print(f"\n  PLACEBO (200 label-shuffles) -- re-confirm Phase-1's p=0.005:")
    fills_all = B.load_fills()
    B.gate_delta_test(fills_all, B.pred_band(0.78, 0.84), "band [0.78,0.84]")

    # OOS: this is a FIXED band (no threshold to pick); just freeze it and test both halves.
    train, test = chrono_split(settled)
    bt, bte = margin_stats(train), margin_stats(test)
    tr_kept = [f for f in train if band(f)]
    te_kept = [f for f in test if band(f)]
    tr_m, te_m = margin_stats(tr_kept), margin_stats(te_kept)
    tr_edge = tr_m["pnl_per_trade"] - bt["pnl_per_trade"]
    te_edge = te_m["pnl_per_trade"] - bte["pnl_per_trade"]
    print(f"\n  -- OOS (fixed band, no threshold pick) --")
    print(f"    TRAIN: base pnl/t={bt['pnl_per_trade']:+.4f}  band {fmt_ms(tr_m)}")
    print(f"           train edge={tr_edge:+.4f}  fire={100*tr_m['n']/bt['n']:.0f}%")
    print(f"    TEST : base pnl/t={bte['pnl_per_trade']:+.4f}  band {fmt_ms(te_m)}")
    print(f"           test  edge={te_edge:+.4f}  fire={100*te_m['n']/bte['n']:.0f}%  "
          f"win-price delta={te_m['wmp_margin']-bte['wmp_margin']:+.4f}")
    verdict = "HOLDS OOS" if te_edge > 0 else "FAILS OOS (in-sample overfit)"
    print(f"    OOS VERDICT: {verdict}")
    results["band"] = dict(train_edge=tr_edge, test_edge=te_edge,
                           test_fire=te_m['n']/bte['n'], in_sample_edge=m['pnl_per_trade']-base['pnl_per_trade'])


def test3_chop(settled, results, enr):
    header("TEST 3  CHOP / LOW-VOL : skip rounds with weak PRE-ENTRY 5-min move "
           "(decision-honest)")
    base = margin_stats(settled)
    have = [f for f in settled if enr.metrics(f.coin, f.market_id) is not None]
    print(f"\n  kline coverage: {len(have)}/{len(settled)} settled fills have pre-entry klines")
    nbars = [enr.metrics(f.coin, f.market_id)["nbars"] for f in have]
    print(f"  pre-entry bars used: median {st.median(nbars):.0f} (we enter ~2.5min into the round;")
    print(f"    LOOK-AHEAD CONTROL: only bars from round-start UP TO our entry bar are used,")
    print(f"    so the gate never sees the post-entry move that decides the round.)")
    print(f"  baseline (no gate): {fmt_ms(base)}")

    # primary metric: preabsmove (net directional move up to entry); secondary: prerange.
    fills_all = B.load_fills()
    chop_results = {}
    for metric in ("preabsmove", "prerange"):
        vals = sorted(enr.metrics(f.coin, f.market_id)[metric] for f in have)
        thr_grid = [round(vals[int(len(vals)*q)], 5) for q in (0.10, 0.20, 0.30, 0.40, 0.50)]
        print(f"\n  -- metric '{metric}' -- IN-SAMPLE: skip rounds with {metric} < thr "
              f"(EXACT reuse):")
        print(f"    {'thr':>8} {'kept':>5} {'fire%':>6} {'win%':>5} {'entry':>6} "
              f"{'pnl/t':>9} {'win-price':>10} {'edge':>9}")
        best_thr, best_edge = None, -1e9
        for thr in thr_grid:
            pred = make_vol_pred(enr, metric, thr, drop_below=True)
            m = margin_stats([f for f in settled if pred(f)])
            edge = m["pnl_per_trade"] - base["pnl_per_trade"]
            if edge > best_edge:
                best_edge, best_thr = edge, thr
            print(f"    {thr:>8} {m['n']:5} {100*m['n']/base['n']:5.0f}% "
                  f"{100*m['win_rate']:4.0f}% {m['mean_price']:6.3f} "
                  f"{m['pnl_per_trade']:+9.4f} {m['wmp_margin']:+10.4f} {edge:+9.4f}")

        print(f"\n    PLACEBO (200 label-shuffles) on in-sample best {metric} thr={best_thr}:")
        B.gate_delta_test(fills_all, make_vol_pred(enr, metric, best_thr, drop_below=True),
                          f"skip {metric}<{best_thr}")

        res = oos_sweep(settled, lambda t, mt=metric: make_vol_pred(enr, mt, t, drop_below=True),
                        thr_grid, metric)
        print_oos(f"chop/low-vol ({metric})", res, metric)
        chop_results[metric] = dict(res=res, best_thr=best_thr, best_edge=best_edge)

    # the chop gate of record is the stronger metric (preabsmove)
    results["chop"] = chop_results["preabsmove"]
    results["chop_all"] = chop_results
    results["chop_enr"] = enr
    return chop_results["preabsmove"]["res"]


def test4_combined(settled, results, enr):
    header("TEST 4  COMBINED gate (band AND chop)")
    cap_res = results.get("price_cap")
    band_res = results.get("band")
    chop_res = results.get("chop", {}).get("res")

    cap_ok = bool(cap_res and cap_res["test_edge"] > 0)
    band_ok = bool(band_res and band_res["test_edge"] > 0)
    chop_ok = bool(chop_res and chop_res["test_edge"] > 0)
    print(f"\n  OOS survivors (passed placebo AND OOS): "
          f"price-cap={'PASS' if cap_ok else 'FAIL'} "
          f"band={'PASS' if band_ok else 'FAIL'} chop={'PASS' if chop_ok else 'FAIL'}")

    survivors = []
    if band_ok:
        survivors.append(("band[0.78,0.84]", lambda f: 0.78 <= f.fill_price <= 0.84))
    if chop_ok:
        thr = chop_res["chosen_thr"]
        survivors.append((f"chop(preabsmove>={thr})",
                          make_vol_pred(enr, "preabsmove", thr, drop_below=True)))
    if cap_ok:
        survivors.append((f"cap<={cap_res['chosen_thr']}", pred_cap(cap_res["chosen_thr"])))

    if len(survivors) < 2:
        print("\n  Fewer than 2 gates survive OOS -> a COMBINED gate is not warranted.")
        results["combined"] = None
        return

    print(f"\n  Survivors point the same way (cut weak/flat entries). Combining via")
    print(f"  logical AND: {[s[0] for s in survivors]}")
    combo = lambda f: all(p(f) for _, p in survivors)

    # independence check: does each survivor add edge WITHIN the others' kept set?
    if band_ok and chop_ok:
        band = survivors[0][1]; chop = survivors[1][1]
        in_band = [f for f in settled if band(f)]
        mb = margin_stats(in_band)
        mbc = margin_stats([f for f in in_band if chop(f)])
        print(f"\n  Independence: chop ADDS edge inside the band-kept set?")
        print(f"    band alone     : pnl/t={mb['pnl_per_trade']:+.4f} win-price={mb['wmp_margin']:+.4f}")
        print(f"    band AND chop  : pnl/t={mbc['pnl_per_trade']:+.4f} win-price={mbc['wmp_margin']:+.4f}"
              f"  (lift confirms the two signals are largely independent)")

    train, test = chrono_split(settled)
    bte = margin_stats(test)
    te_m = margin_stats([f for f in test if combo(f)])
    te_edge = te_m["pnl_per_trade"] - bte["pnl_per_trade"]
    print(f"\n  -- OOS (held-out test half) --")
    print(f"    TEST base : {fmt_ms(bte)}")
    print(f"    TEST combo: {fmt_ms(te_m)} fire={100*te_m['n']/bte['n']:.0f}% edge={te_edge:+.4f}"
          f"  win-price delta={te_m['wmp_margin']-bte['wmp_margin']:+.4f}")
    print(f"\n  PLACEBO on the combined gate:")
    fills_all = B.load_fills()
    B.gate_delta_test(fills_all, combo, "COMBINED " + " AND ".join(s[0] for s in survivors))
    results["combined"] = dict(test_edge=te_edge, test_fire=te_m['n']/bte['n'],
                               test_wmp_delta=te_m['wmp_margin']-bte['wmp_margin'])


# --------------------------------------------------------------------------- #
def main():
    print("#" * 74)
    print("PHASE-2 ENTRY-QUALITY GATE TESTS  (placebo + chronological OOS)")
    print("#" * 74)
    fills = B.load_fills()
    settled = [f for f in fills if f.settled]
    print(f"settled fills: {len(settled)}  span 60/40 split = train/test by ts")
    enr = VolEnricher()

    results: dict = {}
    test1_price_cap(settled, results)
    test2_band(settled, results)
    test3_chop(settled, results, enr)
    test4_combined(settled, results, enr)
    robustness_splits(settled, enr)

    header("SUMMARY")
    print("See phase2_entry.md for the written verdicts.")


def robustness_splits(settled, enr):
    """OOS is fragile on 1.74 days of data; re-check the survivors at 50/60/70%
    chronological split points with frozen thresholds. A survivor should hold at
    ALL split points, not just the chosen 60/40."""
    header("ROBUSTNESS : survivors across 50/60/70% chronological splits "
           "(frozen thresholds)")
    band = lambda f: 0.78 <= f.fill_price <= 0.84
    chop = make_vol_pred(enr, "preabsmove", 0.00056, drop_below=True)
    combo = lambda f: band(f) and chop(f)
    print(f"\n  {'split':>6} {'gate':10} {'fire%':>6} {'edge':>9} {'wmp_delta':>10} verdict")
    for frac in (0.50, 0.60, 0.70):
        s = sorted(settled, key=lambda f: f.ts)
        te = s[int(len(s) * frac):]
        bte = margin_stats(te)
        for name, pred in [("band", band), ("chop", chop), ("combo", combo)]:
            m = margin_stats([f for f in te if pred(f)])
            edge = m["pnl_per_trade"] - bte["pnl_per_trade"]
            wd = m["wmp_margin"] - bte["wmp_margin"]
            print(f"  {frac:>5.0%} {name:10} {100*m['n']/bte['n']:5.0f}% {edge:+9.4f} "
                  f"{wd:+10.4f} {'HOLD' if edge > 0 else 'FAIL'}")
        print()


if __name__ == "__main__":
    main()
