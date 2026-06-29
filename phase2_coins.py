#!/usr/bin/env python3
"""
phase2_coins.py -- Phase 2: COIN-ALLOCATION tests on the VALIDATED real-money harness
=====================================================================================

Imports backtest_real.py (the Phase-1 validated replay harness; does NOT modify it).
Every coin-allocation change is judged by THREE bars, in order:

  1. IN-SAMPLE edge      : pnl/trade(change) - pnl/trade(equal-weight baseline).
  2. PLACEBO  (p<0.05)   : is the edge in the tail of a 200-shuffle label-permutation
                           null?  (gate_delta_test). For coin-DROP this is a weak test
                           -- coin identity is a label, not a per-trade signal -- so we
                           ALSO bootstrap the per-coin margins (Test 2) and lean on OOS.
  3. OOS (chronological) : split fills by ts ~60/40 train/test. Fit/choose the change on
                           TRAIN only, then measure its delta on the held-out TEST. A real
                           edge must survive; an overfit one collapses or flips sign.

THE PRIOR (from the brief / Phase 1): in PAPER, drop-xrp looked +$416. On the REAL
harness, Phase 1's placebo gave drop-xrp p=0.19 (NOISE). Per-coin realized is
btc -11.5, eth +0.6, sol +13.3, xrp -19.7, ALL at ~80% win on ~280 fills each. The
working hypothesis we are trying to falsify: per-coin differences are loss-tail
sampling variance, not a real per-coin edge, and we should hold EQUAL WEIGHT.

TESTS
  1. DROP-XRP        : re-confirm p=0.19 with a clean chronological OOS.
  2. PER-COIN MARGIN : bootstrap CI on each coin's pnl/trade; are the CIs distinguishable
                       from each other / from zero on ~280 trades?
  3. SOL-TILT / EX-ANTE WEIGHTING : weight coins by a ROBUST ex-ante metric (early-round
                       book DEPTH, knowable BEFORE the trade), never by in-sample pnl.
                       Does any ex-ante weighting beat equal-weight OOS + placebo?
  4. DEPTH/SPREAD GATE: drop THIN-BOOK rounds across ALL coins (the principled, per-trade
                       version of "drop xrp"). Survive placebo + OOS?

Run:  python3 phase2_coins.py            # full report -> stdout (also see phase2_coins.md)
      python3 phase2_coins.py --seeds 500  # heavier placebo
"""
from __future__ import annotations

import argparse
import random
import statistics as st
from collections import defaultdict

import backtest_real as br
from backtest_real import Fill, replay, gate_delta_test, placebo_shuffle

COINS = ("btc", "eth", "sol", "xrp")


# --------------------------------------------------------------------------- #
# Ex-ante book metric (knowable BEFORE the trade)
# --------------------------------------------------------------------------- #
# The ONLY honest "tilt" metric is one computable at decision time. We use the
# EARLY-round order book (rem in [180,300]s = the first ~2 min of the 5-min round,
# before the favorite even firms up): median L1 depth = min(bid_s1, ask_s1), and
# median L1 spread = ask_p1 - bid_p1. This recovers the known structural fact
# (btc deep ~83 shares, xrp thin ~7) WITHOUT peeking at the outcome or the fill.
def build_exante(fills: list[Fill]) -> dict[str, dict]:
    """market_id -> {'depth':med L1 depth, 'spread':med L1 spread} from the EARLY book."""
    book = br.TickBook()
    out: dict[str, dict] = {}
    for f in fills:
        if f.market_id in out:
            continue
        rows = book.rows(f.market_id)
        early = [r for r in rows if r[0] is not None and 180.0 <= r[0] <= 300.0]
        depths = [min(r[3], r[4]) for r in early
                  if r[3] is not None and r[4] is not None]
        spreads = [r[2] - r[1] for r in early
                   if r[1] is not None and r[2] is not None]
        if depths and spreads:
            out[f.market_id] = {"depth": st.median(depths),
                                "spread": st.median(spreads)}
    return out


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def per_trade(fl: list[Fill], predicate=None) -> tuple[float, int, float]:
    """Return (pnl/trade, n, total_pnl) over settled fills, optionally gated."""
    r = replay(fl, predicate=predicate)
    return (r.pnl / r.n if r.n else 0.0), r.n, r.pnl


def chrono_split(fills: list[Fill], frac: float = 0.60) -> tuple[list, list]:
    """Chronological train/test split by ts (no shuffling -- this is the whole point
    of an OOS test: the future must be held out)."""
    s = sorted([f for f in fills if f.settled], key=lambda f: f.ts)
    k = int(len(s) * frac)
    return s[:k], s[k:]


def bootstrap_ci(xs: list[float], stat=st.mean, n_boot: int = 5000,
                 seed: int = 0, lo: float = 2.5, hi: float = 97.5):
    rng = random.Random(seed)
    m = len(xs)
    if m == 0:
        return (0.0, 0.0, 0.0)
    point = stat(xs)
    boots = []
    for _ in range(n_boot):
        sample = [xs[rng.randrange(m)] for _ in range(m)]
        boots.append(stat(sample))
    boots.sort()
    return point, boots[int(lo / 100 * n_boot)], boots[int(hi / 100 * n_boot)]


def trade_pnls(fl: list[Fill]) -> dict[str, list[float]]:
    """Per-coin list of per-trade realized P&L (for bootstrap)."""
    per = defaultdict(list)
    for f in fl:
        if f.settled:
            per[f.coin].append(f.realized())
    return per


# --------------------------------------------------------------------------- #
# TEST 1 -- DROP XRP : placebo (re-confirm) + clean chronological OOS
# --------------------------------------------------------------------------- #
def test1_drop_xrp(fills, seeds):
    print("\n" + "=" * 74)
    print("TEST 1 -- DROP XRP  (placebo re-confirm + chronological OOS)")
    print("=" * 74)
    settled = [f for f in fills if f.settled]
    pred = lambda f: f.coin != "xrp"

    base_pt, base_n, base_pnl = per_trade(settled)
    g_pt, g_n, g_pnl = per_trade(settled, pred)
    print(f"in-sample: keep-all  pnl/trade={base_pt:+.4f} (n={base_n}, ${base_pnl:+.2f})")
    print(f"           drop-xrp  pnl/trade={g_pt:+.4f} (n={g_n}, ${g_pnl:+.2f})")
    print(f"           in-sample edge/trade = {g_pt-base_pt:+.4f}")

    print("\nPLACEBO (label-shuffle, the Phase-1 test, re-run):")
    real_edge, null, pval = gate_delta_test(fills, pred, "drop xrp", seeds=seeds)

    print("\nCHRONOLOGICAL OOS (train 60% earliest, test 40% latest by ts):")
    train, test = chrono_split(fills, 0.60)
    tr_b, *_ = per_trade(train); tr_g, *_ = per_trade(train, pred)
    te_b, te_n, te_pnl = per_trade(test); te_g, teg_n, teg_pnl = per_trade(test, pred)
    print(f"  TRAIN edge/trade (drop-xrp - keep-all) = {tr_g-tr_b:+.4f}")
    print(f"  TEST  edge/trade                        = {te_g-te_b:+.4f}  "
          f"(keep-all ${te_pnl:+.2f} -> drop-xrp ${teg_pnl:+.2f})")
    # per-coin pnl in the TEST window to see if xrp is still the worst out-of-sample
    test_per = trade_pnls(test)
    print("  TEST-window per-coin total pnl: " +
          "  ".join(f"{c}={sum(test_per[c]):+.2f}(n{len(test_per[c])})" for c in COINS))

    surv = (pval < 0.05) and (tr_g - tr_b > 0) and (te_g - te_b > 0)
    print(f"\n  VERDICT: placebo p={pval:.3f}  train_edge={tr_g-tr_b:+.4f}  "
          f"test_edge={te_g-te_b:+.4f}")
    print(f"  drop-xrp {'SURVIVES' if surv else 'FAILS'} placebo+OOS "
          f"({'credible' if surv else 'NOISE -- stopping xrp was conservative, not edge-justified'})")
    return dict(pval=pval, train_edge=tr_g - tr_b, test_edge=te_g - te_b, survives=surv)


# --------------------------------------------------------------------------- #
# TEST 2 -- PER-COIN MARGIN  (bootstrap CI on pnl/trade)
# --------------------------------------------------------------------------- #
def test2_margins(fills):
    print("\n" + "=" * 74)
    print("TEST 2 -- PER-COIN MARGIN  (bootstrap 95% CI on pnl/trade)")
    print("=" * 74)
    settled = [f for f in fills if f.settled]
    per = trade_pnls(settled)

    print(f"  {'coin':4} {'n':>4} {'win%':>5} {'avg_entry':>9} "
          f"{'pnl/trade':>10} {'95% CI':>22}  zero?")
    cis = {}
    for c in COINS:
        xs = per[c]
        n = len(xs)
        win = sum(1 for f in settled if f.coin == c and f.side == f.winner)
        ent = st.mean([f.fill_price for f in settled if f.coin == c])
        pt, clo, chi = bootstrap_ci(xs)
        cis[c] = (pt, clo, chi)
        crosses0 = clo <= 0 <= chi
        print(f"  {c:4} {n:4} {100*win/n:4.1f}% {ent:9.3f} {pt:+10.4f} "
              f"[{clo:+.4f}, {chi:+.4f}]  {'yes' if crosses0 else 'NO'}")

    # pairwise: does any coin's pnl/trade differ from the pooled-others?
    print("\n  Pairwise: coin vs all-other-coins pooled (bootstrap CI on the DIFFERENCE):")
    print(f"  {'coin':4} {'diff pnl/trade':>14} {'95% CI of diff':>26}  distinguishable?")
    distinct = {}
    for c in COINS:
        xs = per[c]
        others = [v for cc in COINS if cc != c for v in per[cc]]
        rng = random.Random(7)
        boots = []
        for _ in range(5000):
            a = [xs[rng.randrange(len(xs))] for _ in range(len(xs))]
            b = [others[rng.randrange(len(others))] for _ in range(len(others))]
            boots.append(st.mean(a) - st.mean(b))
        boots.sort()
        diff = st.mean(xs) - st.mean(others)
        dlo, dhi = boots[125], boots[4874]
        sig = not (dlo <= 0 <= dhi)
        distinct[c] = sig
        print(f"  {c:4} {diff:+14.4f} [{dlo:+.4f}, {dhi:+.4f}]  {'YES' if sig else 'no'}")

    n_distinct = sum(distinct.values())
    print(f"\n  VERDICT: {n_distinct}/4 coins have a pnl/trade distinguishable from the")
    print("  pooled rest at 95%. With ~280 trades/coin and ~80% win at ~0.80 entry,")
    print("  per-trade P&L is a high-variance lottery (win +0.2, lose -0.8); CIs are wide.")
    return dict(cis=cis, distinct=distinct, n_distinct=n_distinct)


# --------------------------------------------------------------------------- #
# TEST 3 -- EX-ANTE WEIGHTING (the honest "sol-tilt") : OOS + placebo
# --------------------------------------------------------------------------- #
def test3_exante_weight(fills, exante, seeds):
    print("\n" + "=" * 74)
    print("TEST 3 -- EX-ANTE WEIGHTING  (tilt by early-book DEPTH, knowable pre-trade)")
    print("=" * 74)
    settled = [f for f in fills if f.settled and f.market_id in exante]

    # per-coin ex-ante depth (the metric we tilt by) -- computed WITHOUT outcomes
    coin_depth = {}
    for c in COINS:
        ds = [exante[f.market_id]["depth"] for f in settled if f.coin == c]
        coin_depth[c] = st.median(ds) if ds else 0.0
    print("  ex-ante median early-book DEPTH per coin (the tilt metric, no peeking):")
    print("   " + "  ".join(f"{c}={coin_depth[c]:.1f}" for c in COINS))
    print(f"   (recovers structural fact: xrp thinnest, btc deepest -- matches brief)")

    # Two ex-ante tilts, both fixed-rule (no in-sample pnl):
    #   TILT-DEEP : overweight deep-book coins (hypothesis: thin book = worse fills/adverse).
    #   TILT-THIN : the opposite, as a sanity/placebo on the direction.
    # 'Weight' on this harness = subsetting (we can only DROP/KEEP real trades exactly,
    # not lever them up faithfully). So we express a tilt as: DROP the bottom-depth
    # quantile of ROUNDS (across all coins) -> this is depth-weighting via selection,
    # and it is per-trade (uses each round's own depth), not coin-identity.
    depths_all = sorted(exante[f.market_id]["depth"] for f in settled)

    def edge_for_threshold(thr, fl):
        keep = lambda f: f.market_id in exante and exante[f.market_id]["depth"] >= thr
        b, *_ = per_trade(fl)
        g, gn, gp = per_trade(fl, keep)
        return g - b, gn, gp

    print("\n  Depth-threshold tilt (keep rounds with early depth >= q-th pct), IN-SAMPLE:")
    print(f"  {'pct':>4} {'thr':>6} {'n_kept':>7} {'edge/trade':>11} {'kept_pnl':>9}")
    for q in (0, 10, 25, 40, 50):
        thr = depths_all[int(q / 100 * (len(depths_all) - 1))]
        e, gn, gp = edge_for_threshold(thr, settled)
        print(f"  {q:4d} {thr:6.1f} {gn:7d} {e:+11.4f} {gp:+9.2f}")

    # OOS: choose the threshold on TRAIN, apply to TEST.
    print("\n  OOS: pick best depth pct on TRAIN, measure that SAME rule on TEST:")
    train, test = chrono_split(fills, 0.60)
    train = [f for f in train if f.market_id in exante]
    test = [f for f in test if f.market_id in exante]
    tr_depths = sorted(exante[f.market_id]["depth"] for f in train)
    best_q, best_e, best_thr = None, -1e9, None
    for q in (10, 25, 40, 50):
        thr = tr_depths[int(q / 100 * (len(tr_depths) - 1))]
        e, *_ = edge_for_threshold(thr, train)
        if e > best_e:
            best_q, best_e, best_thr = q, e, thr
    te_edge, te_n, te_pnl = edge_for_threshold(best_thr, test)
    print(f"  TRAIN best: keep depth>=p{best_q} (thr={best_thr:.1f}) "
          f"train_edge={best_e:+.4f}")
    print(f"  TEST  same rule: edge/trade={te_edge:+.4f} (n_kept={te_n}, pnl={te_pnl:+.2f})")

    # placebo on the chosen depth gate
    print("\n  PLACEBO on the TRAIN-chosen depth gate (full sample):")
    keep = lambda f: f.market_id in exante and exante[f.market_id]["depth"] >= best_thr
    _, _, pval = gate_delta_test([f for f in fills if f.market_id in exante],
                                 keep, f"keep depth>=p{best_q}", seeds=seeds)

    surv = (pval < 0.05) and (best_e > 0) and (te_edge > 0)
    print(f"\n  VERDICT: ex-ante depth tilt -- train_edge={best_e:+.4f} "
          f"test_edge={te_edge:+.4f} placebo_p={pval:.3f}")
    print(f"  {'SURVIVES' if surv else 'FAILS'} placebo+OOS")
    return dict(best_q=best_q, train_edge=best_e, test_edge=te_edge,
                pval=pval, survives=surv, coin_depth=coin_depth)


# --------------------------------------------------------------------------- #
# TEST 4 -- DEPTH / SPREAD GATE (principled "drop thin-book", all coins) : placebo+OOS
# --------------------------------------------------------------------------- #
def test4_depth_gate(fills, exante, seeds):
    print("\n" + "=" * 74)
    print("TEST 4 -- THIN-BOOK GATE  (drop thin-depth rounds across ALL coins)")
    print("=" * 74)
    print("  The principled, per-trade version of 'drop xrp': xrp is just the coin")
    print("  whose rounds are usually thin. Gate on the ROUND's own ex-ante depth, so")
    print("  a deep xrp round is kept and a thin btc round is dropped.")
    elig = [f for f in fills if f.market_id in exante]
    settled = [f for f in elig if f.settled]
    depths_all = sorted(exante[f.market_id]["depth"] for f in settled)

    print(f"\n  in-sample depth-gate (drop rounds below the q-th pct depth):")
    print(f"  {'drop<pct':>8} {'thr':>6} {'n_kept':>7} {'edge/trade':>11} {'kept_pnl':>9} {'placebo_p':>10}")
    results = {}
    for q in (10, 25, 33, 50):
        thr = depths_all[int(q / 100 * (len(depths_all) - 1))]
        keep = lambda f, t=thr: f.market_id in exante and exante[f.market_id]["depth"] >= t
        b, *_ = per_trade(settled)
        g, gn, gp = per_trade(settled, keep)
        # quiet placebo (suppress gate_delta_test prints by computing inline)
        edge = g - b
        null = []
        for s in range(seeds):
            pl = placebo_shuffle(elig, seed=s)
            bb, *_ = per_trade(pl)
            gg, *_ = per_trade(pl, keep)
            null.append(gg - bb)
        ge = sum(1 for x in null if x >= edge)
        pv = (ge + 1) / (seeds + 1)
        results[q] = (thr, gn, edge, gp, pv)
        print(f"  {q:8d} {thr:6.1f} {gn:7d} {edge:+11.4f} {gp:+9.2f} {pv:10.3f}")

    # OOS for the best in-sample depth gate
    best_q = max(results, key=lambda q: results[q][2])
    thr_full = results[best_q][0]
    print(f"\n  OOS for the best in-sample gate (drop<p{best_q}):")
    train, test = chrono_split(fills, 0.60)
    train = [f for f in train if f.market_id in exante]
    test = [f for f in test if f.market_id in exante]
    tr_depths = sorted(exante[f.market_id]["depth"] for f in train)
    thr_tr = tr_depths[int(best_q / 100 * (len(tr_depths) - 1))]
    keep = lambda f: f.market_id in exante and exante[f.market_id]["depth"] >= thr_tr
    trb, *_ = per_trade(train); trg, *_ = per_trade(train, keep)
    teb, *_ = per_trade(test); teg, ten, tep = per_trade(test, keep)
    print(f"  TRAIN edge/trade={trg-trb:+.4f} (thr p{best_q}={thr_tr:.1f})")
    print(f"  TEST  edge/trade={teg-teb:+.4f} (n_kept={ten}, kept_pnl={tep:+.2f})")

    pv = results[best_q][4]
    surv = (pv < 0.05) and (results[best_q][2] > 0) and (trg - trb > 0) and (teg - teb > 0)
    print(f"\n  VERDICT: thin-book gate (drop<p{best_q}) -- in_sample_edge={results[best_q][2]:+.4f}"
          f" placebo_p={pv:.3f} train={trg-trb:+.4f} test={teg-teb:+.4f}")
    print(f"  {'SURVIVES' if surv else 'FAILS'} placebo+OOS")
    return dict(best_q=best_q, in_edge=results[best_q][2], pval=pv,
                train_edge=trg - trb, test_edge=teg - teb, survives=surv)


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", type=int, default=200, help="placebo shuffles")
    args = ap.parse_args()

    fills = br.load_fills()  # principled basis (artifacts excluded), same as Phase 1
    settled = [f for f in fills if f.settled]
    print("#" * 74)
    print("PHASE 2 -- COIN ALLOCATION TESTS  (on the validated real-money harness)")
    print("#" * 74)
    print(f"settled LIVE fills: {len(settled)}  (artifacts excluded; matches Phase-1 baseline)")
    base = replay(settled)
    print(f"baseline real P&L : ${base.pnl:+.2f}  win%={100*base.win_rate:.0f}  "
          f"({'  '.join(f'{c} {base.per_coin[c][2]:+.1f}' for c in COINS)})")
    print(f"placebo seeds     : {args.seeds}")

    print("\n  building ex-ante book metrics (early-round depth/spread)...")
    exante = build_exante(fills)
    print(f"  ex-ante metric available for {len(exante)}/{len(set(f.market_id for f in settled))} markets")

    r1 = test1_drop_xrp(fills, args.seeds)
    r2 = test2_margins(fills)
    r3 = test3_exante_weight(fills, exante, args.seeds)
    r4 = test4_depth_gate(fills, exante, args.seeds)

    # ---- final verdict ----
    print("\n" + "#" * 74)
    print("PHASE 2 COIN-ALLOCATION VERDICT")
    print("#" * 74)
    print(f"  1 drop-xrp        : placebo p={r1['pval']:.3f}  OOS test_edge={r1['test_edge']:+.4f}"
          f"  -> {'SURVIVE' if r1['survives'] else 'NOISE'}")
    print(f"  2 per-coin margin : {r2['n_distinct']}/4 coins distinguishable from pooled rest at 95%")
    print(f"  3 ex-ante tilt    : placebo p={r3['pval']:.3f}  OOS test_edge={r3['test_edge']:+.4f}"
          f"  -> {'SURVIVE' if r3['survives'] else 'NOISE'}")
    print(f"  4 thin-book gate  : placebo p={r4['pval']:.3f}  OOS test_edge={r4['test_edge']:+.4f}"
          f"  -> {'SURVIVE' if r4['survives'] else 'NOISE'}")
    any_surv = r1['survives'] or r3['survives'] or r4['survives']
    if any_surv:
        print("\n  >>> At least one coin-allocation change beat placebo AND held OOS. See above.")
    else:
        print("\n  >>> NO coin-allocation change beat placebo AND held OOS.")
        print("  >>> RECOMMENDATION: HOLD EQUAL WEIGHT. Per-coin selection is overfit to the")
        print("      loss-tail variance of ~280 trades/coin. Stopping xrp live was a")
        print("      conservative choice, not an edge-justified one.")


if __name__ == "__main__":
    main()
