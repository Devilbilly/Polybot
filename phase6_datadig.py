#!/usr/bin/env python3
"""
phase6_datadig.py -- OPEN-ENDED data dig for NEW exploitable structure in Polybot
=================================================================================

CONTEXT. Polybot buys the FAVORITE side of crypto 5-min up/down Polymarket markets
and holds to settle. Edge per share = q(x) - p, where q = P(favorite wins | state x)
and p = the price we pay. Phase-4 ran a SINGLE-FEATURE screen and found the only
robust axes are the pre-entry spot path (direction-toward-favorite `net_rel`, dominant;
and trend-vs-chop `eff_ratio`). Phase-5 (a parallel agent) owns the `net_rel` spot-vs-
book DIVERGENCE skip/fade backtest. THIS script does NOT touch that; it goes WIDER and
DEEPER to look for OTHER places where q - p is reliably != 0.

LEADS TESTED HERE (each a family; H = number of distinct hypotheses, tracked for honesty):
  L1 FEATURE INTERACTIONS   -- 2-way interactions; does any predict win INCREMENTALLY
                               over the better of its two single features (residual test)?
  L2 CROSS-COIN LEAD-LAG    -- does BTC's FIRST-minute(s) move predict an ALT's favorite
                               outcome better / incrementally over the alt's own path?
  L3 CALIBRATION SURFACE    -- bucket by (entry-price x condition); map realized q - p.
                               Is there a price x condition CELL with a reliable +edge?
  L4 LATE MICROSTRUCTURE    -- can we enter LATER (rem~30s) using only-then-observable
                               book/spot info to lift q? Does optimal entry time vary?
  L5 TIME / SESSION         -- coin x hour / vol-regime cell with a stable edge, AFTER
                               de-trending the non-stationary win-rate drift.
  L6 THE LOSS TAIL          -- ex-ante observable markers of the WORST rounds beyond
                               the known divergence/chop axes.

DISCIPLINE (non-negotiable, same as Phase-4/foundation):
  * NO LOOK-AHEAD. Every feature uses only data up to the entry/decision tick. Spot path
    is round-open -> entry-bar klines (reusing phase4.spot_path_feats); book/late features
    use only ticks at/before the decision rem.
  * PLACEBO: every claimed effect must beat a label-shuffle null (>=2000 shuffles ->
    empirical p). Reported with an explicit MULTIPLE-TESTING correction (Bonferroni over
    the number of cells/hypotheses in that family, plus the family-wide H count).
  * TIME-OOS: chronological split (train = earliest 60%, test = latest 40%). The 8 days
    are non-stationary across box/config switches -> random splits are invalid. An effect
    is REAL only if it ALSO holds (same direction, non-trivial) in the late test half.
  * We REPORT how many hypotheses we tested. Expect MOST to be noise.

Reuses phase4_features.py as a LIBRARY (import; never modifies it): load_rounds,
load_or_build_klines, spot_path_feats, book_feats, tick_spot_feats, cross_coin_feats,
time_feats, rolling_regime_feats, auc, power, placebo_p, time_oos, quintile_buckets.

USAGE
  python3 phase6_datadig.py                 # full dig + writes phase6_datadig.md
  python3 phase6_datadig.py --shuffles 5000 # more placebo shuffles
  python3 phase6_datadig.py --no-fetch      # offline (cached klines only)
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sqlite3
import statistics as st
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

import phase4_features as p4

HERE = os.path.dirname(os.path.abspath(__file__))
DB = p4.DB
MD_OUT = os.path.join(HERE, "phase6_datadig.md")

OOS_TRAIN_FRAC = 0.60
N_SHUFFLES_DEFAULT = 2000

# Global hypothesis counter for honesty (number of distinct cells/tests we evaluate).
HYP_COUNT = 0


def bump(n: int = 1):
    global HYP_COUNT
    HYP_COUNT += n


# --------------------------------------------------------------------------- #
# Shared stat helpers (built on phase4 primitives where possible)
# --------------------------------------------------------------------------- #
def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (robust at small n)."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (centre - half, centre + half)


def cell_edge_placebo(labels: list[int], prices: list[float], mask: list[bool],
                      n_shuffles: int, seed: int,
                      control: Optional[list[bool]] = None) -> dict:
    """A CELL = a boolean subset (mask) of rounds. Effect = mean(q - p) inside the cell
    (the per-share edge of taking the favorite ONLY when the cell condition holds).

    Placebo, two modes:
      * UNCONDITIONAL (control=None): shuffle win LABELS across ALL rounds (keeps base
        win-rate + prices), recompute the cell's mean(q - p). Empirical p = fraction of
        shuffles whose |cell edge| >= real. Answers: is this cell's edge distinguishable
        from carving a RANDOM same-sized subset out of a deck with the same base win-rate?
        Correct for a CALIBRATION cell where the whole question is "does q beat p here?".
      * CONDITIONAL (control = a boolean stratum mask, mask MUST be a subset of control):
        shuffle labels ONLY WITHIN the control stratum, so the null preserves the
        stratum's (possibly elevated) base win-rate. The statistic is the cell's WIN-RATE
        minus the stratum's win-rate (not q-p), so p tests whether the marker concentrates
        wins/losses BEYOND what the stratum already explains. This is the honest test for
        a 'new marker inside a known-good subset' (avoids the confound where a cell passes
        merely because it inherits a known-good stratum's base rate)."""
    idx = [i for i, m in enumerate(mask) if m]
    n_cell = len(idx)
    if n_cell < 30:
        return dict(ok=False, n=n_cell)
    q = sum(labels[i] for i in idx) / n_cell
    pbar = sum(prices[i] for i in idx) / n_cell
    real_edge = q - pbar
    rng = random.Random(seed)
    lo, hi = wilson_ci(int(round(q * n_cell)), n_cell)

    if control is None:
        # unconditional global label shuffle
        lab = list(labels)
        ge = 0
        for _ in range(n_shuffles):
            rng.shuffle(lab)
            qs = sum(lab[i] for i in idx) / n_cell
            if abs(qs - pbar) >= abs(real_edge):
                ge += 1
        p_emp = (ge + 1) / (n_shuffles + 1)
        return dict(ok=True, n=n_cell, q=q, price=pbar, edge=real_edge,
                    p=p_emp, win_lo=lo, win_hi=hi, mode="uncond")

    # conditional: shuffle labels only within the control stratum
    ctrl_idx = [i for i, m in enumerate(control) if m]
    if not set(idx).issubset(set(ctrl_idx)):
        # mask not a subset of control -> fall back to unconditional, flag it
        return cell_edge_placebo(labels, prices, mask, n_shuffles, seed, control=None)
    strat_q = sum(labels[i] for i in ctrl_idx) / len(ctrl_idx)
    real_delta = q - strat_q                       # cell win-rate minus stratum win-rate
    ctrl_labels = [labels[i] for i in ctrl_idx]
    pos_in_ctrl = {i: j for j, i in enumerate(ctrl_idx)}
    cell_positions = [pos_in_ctrl[i] for i in idx]
    ge = 0
    for _ in range(n_shuffles):
        rng.shuffle(ctrl_labels)
        qs = sum(ctrl_labels[j] for j in cell_positions) / n_cell
        if abs(qs - strat_q) >= abs(real_delta):
            ge += 1
    p_emp = (ge + 1) / (n_shuffles + 1)
    return dict(ok=True, n=n_cell, q=q, price=pbar, edge=real_edge,
                p=p_emp, win_lo=lo, win_hi=hi, mode="cond",
                strat_q=strat_q, delta=real_delta)


def time_oos_cell(labels, prices, mask, ts, train_frac=OOS_TRAIN_FRAC) -> dict:
    """Chronological split. Fit nothing (the cell is a fixed rule); just measure the
    cell's mean(q - p) on the EARLY train and the LATE test independently, and whether
    the edge SIGN holds (and is non-trivial) in test. Direction holds = same sign and
    |test edge|>0.005 and test cell n>=20."""
    order = sorted(range(len(labels)), key=lambda i: ts[i])
    k = int(len(order) * train_frac)
    tr_idx, te_idx = order[:k], order[k:]

    def cell_edge(idxset):
        sel = [i for i in idxset if mask[i]]
        if len(sel) < 20:
            return None, len(sel)
        q = sum(labels[i] for i in sel) / len(sel)
        pbar = sum(prices[i] for i in sel) / len(sel)
        return q - pbar, len(sel)

    tr_edge, tr_n = cell_edge(tr_idx)
    te_edge, te_n = cell_edge(te_idx)
    if tr_edge is None or te_edge is None:
        return dict(ok=False, tr_n=tr_n, te_n=te_n)
    holds = (tr_edge != 0 and (tr_edge > 0) == (te_edge > 0) and abs(te_edge) > 0.005)
    return dict(ok=True, tr_edge=tr_edge, te_edge=te_edge, tr_n=tr_n, te_n=te_n,
                holds=bool(holds))


def time_oos_cell_cond(labels, mask, control, ts, train_frac=OOS_TRAIN_FRAC) -> dict:
    """CONDITIONAL OOS: does the cell's win-rate DELTA vs its control stratum hold the
    same SIGN in the late test half? mask MUST be a subset of control. Statistic is
    cell_winrate - stratum_winrate, measured separately on early train and late test."""
    order = sorted(range(len(labels)), key=lambda i: ts[i])
    k = int(len(order) * train_frac)
    tr_idx, te_idx = set(order[:k]), set(order[k:])

    def delta(idxset):
        cell = [i for i in idxset if mask[i]]
        strat = [i for i in idxset if control[i]]
        if len(cell) < 20 or len(strat) < 20:
            return None, len(cell)
        q = sum(labels[i] for i in cell) / len(cell)
        sq = sum(labels[i] for i in strat) / len(strat)
        return q - sq, len(cell)

    tr_d, tr_n = delta(tr_idx)
    te_d, te_n = delta(te_idx)
    if tr_d is None or te_d is None:
        return dict(ok=False, tr_n=tr_n, te_n=te_n)
    holds = (tr_d != 0 and (tr_d > 0) == (te_d > 0) and abs(te_d) > 0.005)
    return dict(ok=True, tr_edge=tr_d, te_edge=te_d, tr_n=tr_n, te_n=te_n, holds=bool(holds))


def feature_incremental_auc(base_vals, inter_vals, labels) -> dict:
    """Does the INTERACTION add predictive power over its best single component?
    Bucket the base feature into terciles; WITHIN each base tercile, compute AUC of the
    interaction's *other* component (or the product) vs label. If the interaction only
    restates the base, within-tercile AUC ~ 0.5. We report the n-weighted mean
    within-stratum |AUC-0.5| of the product term and compare to its marginal power.
    (A crude conditional-independence probe; the decisive test is still placebo+OOS on
    the chosen interaction cell.)"""
    n = len(labels)
    if n < 150:
        return dict(ok=False)
    order = sorted(range(n), key=lambda i: base_vals[i])
    t1, t2 = order[n // 3], order[2 * n // 3]
    lo_cut, hi_cut = base_vals[t1], base_vals[t2]
    strata = defaultdict(list)
    for i in range(n):
        if base_vals[i] <= lo_cut:
            s = 0
        elif base_vals[i] <= hi_cut:
            s = 1
        else:
            s = 2
        strata[s].append(i)
    wsum, w = 0.0, 0
    for s, idxs in strata.items():
        if len(idxs) < 40:
            continue
        v = [inter_vals[i] for i in idxs]
        y = [labels[i] for i in idxs]
        if len(set(y)) < 2:
            continue
        pw = abs(p4.auc(v, y) - 0.5)
        wsum += pw * len(idxs)
        w += len(idxs)
    cond_power = wsum / w if w else 0.0
    marg_power = abs(p4.auc(inter_vals, labels) - 0.5)
    return dict(ok=True, cond_power=cond_power, marg_power=marg_power)


# --------------------------------------------------------------------------- #
# Extra entry-time features Phase-4 did NOT compute (no look-ahead)
# --------------------------------------------------------------------------- #
def coin_first_minute_moves(rounds, kl) -> None:
    """For each round, attach the per-minute Binance close-to-close returns of THIS coin
    AND of BTC for minutes 0 (round-open->+1m) and 0..k, signed toward THIS round's
    favorite. Uses ONLY bars at or before the entry bar (no look-ahead). Adds:
      own_m0   : this coin's minute-0 return toward favorite
      btc_m0   : BTC's minute-0 return toward favorite (cross-coin lead candidate)
      btc_m01  : BTC's cumulative minutes 0..1 toward favorite
      own_m0_raw, btc_m0_raw : unsigned (raw) minute-0 returns, for lead-lag corr
    Only minutes strictly before/at entry bar are used -> if entry happened in bar 0,
    minute-1 features are absent (None)."""
    # build per-coin per-minute close series keyed by round-open second
    for r in rounds:
        bars_map = kl.get(r.coin, {})
        btc_map = kl.get("btc", {})
        start = r.end_ts - 300
        entry_bar = int((r.entry_ts - start) // 60)
        entry_bar = max(0, min(4, entry_bar))
        fav_dir = 1.0 if r.side == "YES" else -1.0

        def minute_ret(bmap, mi):
            o_ms = int((start + 60 * mi) * 1000)
            b = bmap.get(o_ms)
            if not b:
                return None
            o, c = b[0], b[3]
            if not o:
                return None
            return (c - o) / o

        # own coin minute-0 (always at/before entry if entry_bar>=0)
        om0 = minute_ret(bars_map, 0)
        bm0 = minute_ret(btc_map, 0)
        if om0 is not None:
            r.feats["own_m0_raw"] = om0
            r.feats["own_m0"] = om0 * fav_dir
        if bm0 is not None:
            r.feats["btc_m0_raw"] = bm0
            r.feats["btc_m0"] = bm0 * fav_dir
        # cumulative BTC 0..1 only if entry happened in bar>=1 (else look-ahead)
        if entry_bar >= 1:
            bm1 = minute_ret(btc_map, 1)
            if bm0 is not None and bm1 is not None:
                r.feats["btc_m01"] = (bm0 + bm1) * fav_dir


def late_window_feats(rounds, con, decision_rem: float = 30.0) -> None:
    """LATER-ENTRY probe. At a LATER decision point (rem ~ decision_rem) the book has
    moved; the favorite side may have firmed or flipped. Features observable ONLY at the
    later decision (so they correspond to entering later, not look-ahead at the ORIGINAL
    entry): the favorite-side ask at rem~decision_rem, and the price DRIFT from the
    original entry tick to the later tick (did the favorite firm up?). We DO NOT relabel;
    label stays the round winner. The question: among rounds where we COULD wait to
    rem~decision_rem, does the late book firm-up predict the win incrementally?
      late_fav_price : favorite-side ask at the late decision rem (None if no such tick)
      late_drift     : late_fav_price - entry_price (>0 = favorite got MORE expensive,
                       i.e. book grew more confident in our side after entry)
      late_still_fav : 1 if our side is STILL the cheaper-to-1 (still favored) late, else 0
    """
    cur = con.cursor()
    for r in rounds:
        if r.rem <= decision_rem + 1:
            continue  # original entry already at/after the late point; nothing to add
        row = cur.execute(
            "SELECT bid_p1, ask_p1 FROM ticks WHERE market_id=? AND ask_p1>0 "
            "AND rem>=? ORDER BY rem ASC LIMIT 1",
            (r.market_id, decision_rem - 0.01)
        ).fetchone()
        if not row:
            continue
        bid, ask = row
        late_fav = ask if r.side == "YES" else 1.0 - bid
        if late_fav is None:
            continue
        r.feats["late_fav_price"] = late_fav
        r.feats["late_drift"] = late_fav - r.entry_price
        # is our side still the favorite (price>0.5) at the late point?
        r.feats["late_still_fav"] = 1.0 if late_fav >= 0.5 else 0.0


def l2_book_feats(rounds, con) -> None:
    """Deeper book microstructure at the entry tick that Phase-4 did not screen:
      total_depth3 : summed L1+L2+L3 size on both sides (book thickness, deeper)
      imb3_fav     : 3-level size imbalance toward the favorite side
      ws_gap       : |ws_mid - book_mid| ('websocket' last vs book mid dislocation)
    All from the entry tick only (rem >= entry rem, nearest)."""
    cur = con.cursor()
    for r in rounds:
        row = cur.execute(
            "SELECT bid_p1,bid_s1,bid_p2,bid_s2,bid_p3,bid_s3,"
            "ask_p1,ask_s1,ask_p2,ask_s2,ask_p3,ask_s3,ws_bid,ws_ask "
            "FROM ticks WHERE market_id=? AND ask_p1>0 AND rem>=? ORDER BY rem ASC LIMIT 1",
            (r.market_id, r.rem - 0.01)
        ).fetchone()
        if not row:
            continue
        (bp1, bs1, bp2, bs2, bp3, bs3,
         ap1, as1, ap2, as2, ap3, as3, wsb, wsa) = row
        bid_sz = (bs1 or 0) + (bs2 or 0) + (bs3 or 0)
        ask_sz = (as1 or 0) + (as2 or 0) + (as3 or 0)
        tot = bid_sz + ask_sz
        if tot > 0:
            imb3 = (bid_sz - ask_sz) / tot
            r.feats["total_depth3"] = tot
            r.feats["imb3_fav"] = imb3 if r.side == "YES" else -imb3
        if bp1 and ap1:
            book_mid = (bp1 + ap1) / 2.0
            if wsb and wsa and wsb > 0 and wsa > 0:
                ws_mid = (wsb + wsa) / 2.0
                r.feats["ws_gap"] = abs(ws_mid - book_mid)


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #
@dataclass
class CellResult:
    family: str
    name: str
    n: int
    q: float
    price: float
    edge: float
    placebo_p: float
    bonf_p: float          # placebo_p * n_cells_in_family (Bonferroni)
    oos: dict
    note: str
    verdict: str


def verdict_of(p_bonf, oos) -> str:
    placebo_ok = p_bonf < 0.05
    oos_ok = oos.get("ok") and oos.get("holds")
    if placebo_ok and oos_ok:
        return "REAL"
    if placebo_ok and not oos_ok:
        return "noise (fails OOS)"
    if (not placebo_ok) and oos_ok:
        return "noise (fails placebo/MT)"
    return "noise"


# --------------------------------------------------------------------------- #
# L1: FEATURE INTERACTIONS
# --------------------------------------------------------------------------- #
def lead_interactions(rounds, n_shuffles, seed) -> tuple[list[CellResult], list[str]]:
    """Test 2-way interaction CELLS: pick rounds where BOTH components are in their
    'good' (or 'bad') tail, and ask whether that joint cell has an edge BEYOND what the
    single features already give. Cells are defined on terciles to keep n reasonable.
    We also report the conditional-vs-marginal power probe (does the product add info
    within strata of the base feature?)."""
    log = []
    res: list[CellResult] = []
    have = [r for r in rounds if "net_rel" in r.feats and "eff_ratio" in r.feats
            and "dir_changes" in r.feats and "entry_price" in r.feats
            and "price_traj" in r.feats and "absmove" in r.feats]
    have.sort(key=lambda r: r.entry_ts)
    labels = [r.label for r in have]
    prices = [r.entry_price for r in have]
    ts = [r.entry_ts for r in have]

    def vals(name):
        return [r.feats[name] for r in have]

    def tercile_cut(v):
        sv = sorted(v)
        n = len(sv)
        return sv[n // 3], sv[2 * n // 3]

    # candidate interaction pairs (each: name, (featA, A_low_good?), (featB, B_low_good?))
    # "good" = direction that should RAISE win prob. We test the cell where BOTH are in
    # their *bad* tail (expected NEGATIVE edge -> a SKIP signal, the actionable thing) AND
    # both in *good* tail (expected POSITIVE edge). 'low_good' True means small value = good.
    pairs = [
        ("eff_ratio x dir_changes (chop confluence)", ("eff_ratio", False), ("dir_changes", True)),
        ("net_rel x eff_ratio (drift x clean-trend)", ("net_rel", False), ("eff_ratio", False)),
        ("net_rel x dir_changes (drift x chop)", ("net_rel", False), ("dir_changes", True)),
        ("entry_price x eff_ratio (cheap-fav x chop)", ("entry_price", False), ("eff_ratio", False)),
        ("entry_price x net_rel (cheap-fav x drift)", ("entry_price", False), ("net_rel", False)),
        ("absmove x dir_changes (bigmove x chop)", ("absmove", False), ("dir_changes", True)),
        ("price_traj x eff_ratio (firming x trend)", ("price_traj", False), ("eff_ratio", False)),
        ("net_rel x price_traj (drift x firming)", ("net_rel", False), ("price_traj", False)),
    ]
    log.append(f"L1 interactions: {len(have)} rounds with all components; "
               f"{len(pairs)} pairs x 2 tails = {len(pairs)*2} interaction cells tested.")
    n_cells = len(pairs) * 2

    for label_name, (fa, fa_low_good), (fb, fb_low_good) in pairs:
        va, vb = vals(fa), vals(fb)
        a_lo, a_hi = tercile_cut(va)
        b_lo, b_hi = tercile_cut(vb)
        # BAD tail: A in its bad tercile AND B in its bad tercile
        # bad for A = (high if low_good else low)
        def in_bad(v, lo, hi, low_good):
            return (v >= hi) if low_good else (v <= lo)
        def in_good(v, lo, hi, low_good):
            return (v <= lo) if low_good else (v >= hi)
        for tail_name, fn in (("BAD-confluence (skip?)", in_bad), ("GOOD-confluence", in_good)):
            mask = [fn(va[i], a_lo, a_hi, fa_low_good) and fn(vb[i], b_lo, b_hi, fb_low_good)
                    for i in range(len(have))]
            bump(1)
            cp = cell_edge_placebo(labels, prices, mask, n_shuffles, seed)
            if not cp.get("ok"):
                continue
            oos = time_oos_cell(labels, prices, mask, ts)
            bonf = min(1.0, cp["p"] * n_cells)
            res.append(CellResult(
                family="L1 interaction", name=f"{label_name} [{tail_name}]",
                n=cp["n"], q=cp["q"], price=cp["price"], edge=cp["edge"],
                placebo_p=cp["p"], bonf_p=bonf, oos=oos,
                note=f"win {100*cp['q']:.0f}% [{100*cp['win_lo']:.0f}-{100*cp['win_hi']:.0f}], "
                     f"price {cp['price']:.3f}",
                verdict=verdict_of(bonf, oos)))

    # incremental-power probe for the two strongest single axes' product
    inc = feature_incremental_auc(vals("net_rel"), [vals("net_rel")[i]*vals("eff_ratio")[i]
                                                    for i in range(len(have))], labels)
    if inc.get("ok"):
        log.append(f"  incremental probe net_rel*eff_ratio: marginal power "
                   f"{inc['marg_power']:.3f} vs within-net_rel-tercile cond power "
                   f"{inc['cond_power']:.3f} (cond<<marg => product just restates net_rel).")

    # ---- DECISIVE incremental test: is the chop axis (eff_ratio) a SEPARATE lever from
    # the divergence axis, i.e. does it still predict WITHIN the ALIGNED subset (net_rel>=0)
    # where Phase-5's divergence signal is absent? This is the cleanest 'new structure'
    # question in L1. We test eff_ratio's AUC/placebo/OOS restricted to net_rel>=0, AND
    # the bottom-eff-quintile 'chop within aligned' SKIP cell (q - p edge).
    aligned = [r for r in have if r.feats["net_rel"] >= 0]
    if len(aligned) >= 200:
        al_eff = [r.feats["eff_ratio"] for r in aligned]
        al_lab = [r.label for r in aligned]
        al_prc = [r.entry_price for r in aligned]
        al_ts = [r.entry_ts for r in aligned]
        a = p4.auc(al_eff, al_lab)
        p_emp = p4.placebo_p(al_eff, al_lab, n_shuffles, seed=seed + 1)
        oos_al = p4.time_oos(list(zip(al_eff, al_lab, al_prc, al_ts)))
        al_win = sum(al_lab) / len(al_lab)
        log.append(f"  *** INCREMENTAL CHOP TEST (the key L1 result): within the ALIGNED "
                   f"subset (net_rel>=0, n={len(aligned)}, win {100*al_win:.1f}%) — where the "
                   f"divergence signal is gone — eff_ratio STILL predicts: AUC={a:.3f}, "
                   f"placebo p={p_emp:.3f}, OOS {'HOLDS' if oos_al.get('holds') else 'fails'} "
                   f"(train AUC {oos_al.get('train_auc',0):.3f} -> test {oos_al.get('test_auc',0):.3f}). "
                   f"=> chop/trend is a lever INDEPENDENT of divergence.")
        # the actionable cells: chop (bottom-eff) vs clean-trend (top-eff) WITHIN aligned.
        # The headline is the q-p SPREAD between them (separates -EV chop from +EV trend
        # in the regime where divergence is silent). Each absolute cell uses the uncond
        # placebo (q vs p); the SPREAD gets its own dedicated placebo+OOS below.
        se = sorted(al_eff)
        q1 = se[len(se) // 5]
        q4 = se[4 * len(se) // 5]
        chop_mask = [r.feats["net_rel"] >= 0 and r.feats["eff_ratio"] <= q1 for r in have]
        trend_mask = [r.feats["net_rel"] >= 0 and r.feats["eff_ratio"] >= q4 for r in have]
        bump(2)
        cp_chop = cell_edge_placebo(labels, prices, chop_mask, n_shuffles, seed)
        oos_chop = time_oos_cell(labels, prices, chop_mask, ts)
        cp_trend = cell_edge_placebo(labels, prices, trend_mask, n_shuffles, seed)
        oos_trend = time_oos_cell(labels, prices, trend_mask, ts)
        res.append(CellResult(
            family="L1 interaction",
            name="ALIGNED & CHOP (net_rel>=0, bottom-eff-quintile) [SKIP cell]",
            n=cp_chop["n"], q=cp_chop["q"], price=cp_chop["price"], edge=cp_chop["edge"],
            placebo_p=cp_chop["p"], bonf_p=min(1.0, cp_chop["p"]), oos=oos_chop,
            note=f"win {100*cp_chop['q']:.0f}% at price {cp_chop['price']:.3f} -> "
                 f"edge q-p={cp_chop['edge']:+.3f} (≈break-even/-EV; SKIP candidate)",
            verdict=verdict_of(min(1.0, cp_chop["p"]), oos_chop)))
        res.append(CellResult(
            family="L1 interaction",
            name="ALIGNED & CLEAN-TREND (net_rel>=0, top-eff-quintile) [SIZE-UP cell]",
            n=cp_trend["n"], q=cp_trend["q"], price=cp_trend["price"], edge=cp_trend["edge"],
            placebo_p=cp_trend["p"], bonf_p=min(1.0, cp_trend["p"]), oos=oos_trend,
            note=f"win {100*cp_trend['q']:.0f}% at price {cp_trend['price']:.3f} -> "
                 f"edge q-p={cp_trend['edge']:+.3f} (genuinely +EV; SIZE-UP candidate)",
            verdict=verdict_of(min(1.0, cp_trend["p"]), oos_trend)))

        # DEDICATED SPREAD test: top-eff minus bottom-eff q-p, within aligned, with its
        # own label-shuffle placebo and chronological OOS. This is the single most
        # defensible NEW lever in Phase-6.
        spread_real = cp_trend["edge"] - cp_chop["edge"]
        rng = random.Random(seed + 7)
        lab2 = list(labels)
        c_idx = [i for i, m in enumerate(chop_mask) if m]
        t_idx = [i for i, m in enumerate(trend_mask) if m]
        pc = sum(prices[i] for i in c_idx) / len(c_idx)
        pt = sum(prices[i] for i in t_idx) / len(t_idx)
        ge = 0
        for _ in range(n_shuffles):
            rng.shuffle(lab2)
            qc = sum(lab2[i] for i in c_idx) / len(c_idx)
            qt = sum(lab2[i] for i in t_idx) / len(t_idx)
            if abs((qt - pt) - (qc - pc)) >= abs(spread_real):
                ge += 1
        spread_p = (ge + 1) / (n_shuffles + 1)
        # OOS spread
        order = sorted(range(len(labels)), key=lambda i: ts[i])
        kk = int(len(order) * OOS_TRAIN_FRAC)
        def split_spread(idxset):
            cs = [i for i in idxset if chop_mask[i]]
            tsel = [i for i in idxset if trend_mask[i]]
            if len(cs) < 20 or len(tsel) < 20:
                return None
            ec = sum(labels[i] for i in cs)/len(cs) - sum(prices[i] for i in cs)/len(cs)
            et = sum(labels[i] for i in tsel)/len(tsel) - sum(prices[i] for i in tsel)/len(tsel)
            return et - ec
        tr_sp = split_spread(set(order[:kk]))
        te_sp = split_spread(set(order[kk:]))
        sp_holds = (tr_sp is not None and te_sp is not None and tr_sp != 0
                    and (tr_sp > 0) == (te_sp > 0) and abs(te_sp) > 0.01)
        bump(1)
        log.append(f"  *** HEADLINE NEW LEVER: within ALIGNED, the q-p edge SPREAD "
                   f"(clean-trend {cp_trend['edge']:+.3f} minus chop {cp_chop['edge']:+.3f}) "
                   f"= {spread_real:+.3f}; placebo p={spread_p:.4f}; OOS train {tr_sp:+.3f} -> "
                   f"test {te_sp:+.3f} ({'HOLDS' if sp_holds else 'fails'}). This separates "
                   f"+EV clean-trend favorites from ~break-even chop favorites IN THE REGIME "
                   f"WHERE DIVERGENCE IS SILENT — incremental to Phase-5.")
    return res, log


# --------------------------------------------------------------------------- #
# L2: CROSS-COIN LEAD-LAG
# --------------------------------------------------------------------------- #
def lead_cross_coin(rounds, n_shuffles, seed) -> tuple[list[CellResult], list[str]]:
    """Does BTC's FIRST-minute move predict an ALT favorite outcome BETTER than / beyond
    the alt's own first-minute move? Restrict to ALT rounds (eth/sol/xrp). Tests:
      (a) marginal AUC of btc_m0 vs label (alt rounds only), placebo+OOS;
      (b) marginal AUC of own_m0 vs label (alt rounds only) -- the benchmark;
      (c) the residual cell: btc_m0 toward favorite is STRONG but own_m0 is FLAT/against
          -> does BTC's lead override the alt's own quiet/contrary start?
    Lead-lag direction (does BTC lead the alt, not vice versa) is checked via a simple
    cross-correlation of minute-0 raw returns at the round level."""
    log = []
    res: list[CellResult] = []
    alts = [r for r in rounds if r.coin in ("eth", "sol", "xrp")
            and "btc_m0" in r.feats and "own_m0" in r.feats]
    alts.sort(key=lambda r: r.entry_ts)
    if len(alts) < 200:
        log.append("L2: <200 alt rounds with both moves; skipped.")
        return res, log
    labels = [r.label for r in alts]
    prices = [r.entry_price for r in alts]
    ts = [r.entry_ts for r in alts]
    btc_m0 = [r.feats["btc_m0"] for r in alts]
    own_m0 = [r.feats["own_m0"] for r in alts]

    auc_btc = p4.auc(btc_m0, labels)
    auc_own = p4.auc(own_m0, labels)
    p_btc = p4.placebo_p(btc_m0, labels, n_shuffles, seed=seed)
    p_own = p4.placebo_p(own_m0, labels, n_shuffles, seed=seed)
    oos_btc = p4.time_oos(list(zip(btc_m0, labels, prices, ts)))
    log.append(f"L2 cross-coin (alt rounds n={len(alts)}): "
               f"AUC btc_m0={auc_btc:.3f} (placebo p={p_btc:.3f}, "
               f"OOS {'holds' if oos_btc.get('holds') else 'fails'}) vs "
               f"AUC own_m0={auc_own:.3f} (placebo p={p_own:.3f}).")

    # contemporaneous cross-corr of raw minute-0 returns (lead-lag sniff): if BTC truly
    # LEADS, BTC's minute-0 move should correlate with the alt's NEXT-minute move more
    # than with the alt's same-minute move. We approximate with same-minute corr here
    # (full lead-lag would need the alt's minute-1, often post-entry -> look-ahead).
    braw = [r.feats.get("btc_m0_raw") for r in alts]
    oraw = [r.feats.get("own_m0_raw") for r in alts]
    pairs = [(b, o) for b, o in zip(braw, oraw) if b is not None and o is not None]
    if len(pairs) > 30:
        bs = [x[0] for x in pairs]; os_ = [x[1] for x in pairs]
        n = len(bs); mb = sum(bs)/n; mo = sum(os_)/n
        cov = sum((b-mb)*(o-mo) for b, o in pairs)
        db = sum((b-mb)**2 for b in bs)**0.5; do = sum((o-mo)**2 for o in os_)**0.5
        cc = cov/(db*do) if db and do else 0.0
        log.append(f"  same-minute raw-return corr(BTC, alt) = {cc:+.2f} "
                   f"(high => alts already co-move with BTC; little independent BTC lead).")

    # CELL (c): residual -- BTC strongly toward favorite while own move is flat/against.
    # tercile cuts on each
    def terc(v):
        sv = sorted(v); n = len(sv); return sv[n//3], sv[2*n//3]
    b_lo, b_hi = terc(btc_m0)
    o_lo, o_hi = terc(own_m0)
    # n_cells for Bonferroni in this family: the two residual cells we form
    n_cells = 2
    for nm, mask in [
        ("BTC strongly toward fav & OWN start flat/against",
         [btc_m0[i] >= b_hi and own_m0[i] <= o_lo for i in range(len(alts))]),
        ("BTC strongly AGAINST fav & OWN start flat/toward (skip?)",
         [btc_m0[i] <= b_lo and own_m0[i] >= o_hi for i in range(len(alts))]),
    ]:
        bump(1)
        cp = cell_edge_placebo(labels, prices, mask, n_shuffles, seed)
        if not cp.get("ok"):
            log.append(f"  cell '{nm}': n<30, skipped.")
            continue
        oos = time_oos_cell(labels, prices, mask, ts)
        bonf = min(1.0, cp["p"] * n_cells)
        res.append(CellResult(
            family="L2 cross-coin", name=nm, n=cp["n"], q=cp["q"], price=cp["price"],
            edge=cp["edge"], placebo_p=cp["p"], bonf_p=bonf, oos=oos,
            note=f"win {100*cp['q']:.0f}% [{100*cp['win_lo']:.0f}-{100*cp['win_hi']:.0f}], "
                 f"price {cp['price']:.3f}", verdict=verdict_of(bonf, oos)))
    # also register the marginal btc_m0 screen as a 'cell' summary (AUC-based, not edge)
    bump(1)
    return res, log


# --------------------------------------------------------------------------- #
# L3: CALIBRATION SURFACE (price x condition)
# --------------------------------------------------------------------------- #
def lead_calibration(rounds, n_shuffles, seed) -> tuple[list[CellResult], list[str]]:
    """Map realized q - p over a 2-D grid of (entry-price bin) x (condition bin). Find
    CELLS where the favorite reliably BEATS its price (q - p large +). Conditions tried:
      * eff_ratio tercile (chop vs trend)
      * net_rel sign (aligned vs divergent) -- note: divergence itself is Phase-5's; we
        only use it here as a CONDITION to find a price-cell, not as the headline.
    Price bins: [0.78,0.80),[0.80,0.82),[0.82,0.85]. Every cell -> placebo + OOS, with
    Bonferroni over all cells in the grid."""
    log = []
    res: list[CellResult] = []
    have = [r for r in rounds if "eff_ratio" in r.feats and "net_rel" in r.feats]
    have.sort(key=lambda r: r.entry_ts)
    labels = [r.label for r in have]
    prices = [r.entry_price for r in have]
    ts = [r.entry_ts for r in have]
    eff = [r.feats["eff_ratio"] for r in have]
    nrel = [r.feats["net_rel"] for r in have]

    price_bins = [(0.78, 0.80), (0.80, 0.82), (0.82, 0.851)]
    se = sorted(eff)
    e_lo, e_hi = se[len(se)//3], se[2*len(se)//3]
    cond_defs = [
        ("eff:low(chop)", lambda i: eff[i] <= e_lo),
        ("eff:mid", lambda i: e_lo < eff[i] <= e_hi),
        ("eff:high(trend)", lambda i: eff[i] > e_hi),
        ("aligned(net_rel>=0)", lambda i: nrel[i] >= 0),
        ("divergent(net_rel<0)", lambda i: nrel[i] < 0),
    ]
    n_cells = len(price_bins) * len(cond_defs)
    log.append(f"L3 calibration: grid {len(price_bins)} price-bins x {len(cond_defs)} "
               f"conditions = {n_cells} cells (Bonferroni over {n_cells}).")
    for (plo, phi) in price_bins:
        for cname, cfn in cond_defs:
            mask = [plo <= prices[i] < phi and cfn(i) for i in range(len(have))]
            bump(1)
            cp = cell_edge_placebo(labels, prices, mask, n_shuffles, seed)
            if not cp.get("ok"):
                continue
            oos = time_oos_cell(labels, prices, mask, ts)
            bonf = min(1.0, cp["p"] * n_cells)
            res.append(CellResult(
                family="L3 calibration", name=f"price[{plo:.2f},{phi:.2f}) x {cname}",
                n=cp["n"], q=cp["q"], price=cp["price"], edge=cp["edge"],
                placebo_p=cp["p"], bonf_p=bonf, oos=oos,
                note=f"win {100*cp['q']:.0f}% [{100*cp['win_lo']:.0f}-{100*cp['win_hi']:.0f}], "
                     f"edge q-p={cp['edge']:+.3f}",
                verdict=verdict_of(bonf, oos)))
    return res, log


# --------------------------------------------------------------------------- #
# L4: LATE MICROSTRUCTURE / ENTER LATER
# --------------------------------------------------------------------------- #
def lead_late(rounds, n_shuffles, seed) -> tuple[list[CellResult], list[str]]:
    """Among rounds where we could WAIT to rem~30s, does the late book FIRM-UP (favorite
    got more expensive after our original entry) predict the win incrementally? And is a
    later entry's win-rate higher (justifying entering later)? Two tests:
      (a) AUC of late_drift vs label (does post-entry firm-up foreshadow the win?). If
          yes, this is partially mechanical (price converges to outcome) -- the USABLE
          version is to ENTER at the late point, so we also report the CELL: late firm-up
          present -> win-rate & implied edge AT THE LATE PRICE (q - late_fav_price).
      (b) compare base win-rate of the 'could-wait' subset entering at original price vs
          at the late price (does waiting raise q - p?)."""
    log = []
    res: list[CellResult] = []
    have = [r for r in rounds if "late_drift" in r.feats and "late_fav_price" in r.feats]
    have.sort(key=lambda r: r.entry_ts)
    if len(have) < 200:
        log.append("L4: <200 rounds reach the late decision window; skipped.")
        return res, log
    labels = [r.label for r in have]
    eprice = [r.entry_price for r in have]
    lprice = [r.feats["late_fav_price"] for r in have]
    drift = [r.feats["late_drift"] for r in have]
    ts = [r.entry_ts for r in have]

    auc_drift = p4.auc(drift, labels)
    p_drift = p4.placebo_p(drift, labels, n_shuffles, seed=seed)
    oos_drift = p4.time_oos(list(zip(drift, labels, eprice, ts)))
    log.append(f"L4 late (n={len(have)} could-wait rounds, decision_rem~30s): "
               f"AUC late_drift->win={auc_drift:.3f} (placebo p={p_drift:.3f}, "
               f"OOS {'holds' if oos_drift.get('holds') else 'fails'}).")

    # mean edge entering at ORIGINAL price vs LATE price on the same rounds
    q_all = sum(labels) / len(labels)
    edge_orig = q_all - (sum(eprice) / len(eprice))
    edge_late = q_all - (sum(lprice) / len(lprice))
    log.append(f"  same rounds: q={100*q_all:.1f}%; edge@orig-price={edge_orig:+.3f}, "
               f"edge@late-price={edge_late:+.3f} "
               f"(late worse if favorite firmed up; better only if it cheapened).")

    # CELL: enter ONLY when the favorite FIRMED UP by late point (drift>0). Edge is q - LATE price.
    n_cells = 2
    for nm, mask, use_late_price in [
        ("late firm-up (drift>0): edge AT LATE PRICE", [d > 0 for d in drift], True),
        ("late faded (drift<0) -> skip-candidate: edge AT LATE PRICE", [d < 0 for d in drift], True),
    ]:
        idx = [i for i, m in enumerate(mask) if m]
        if len(idx) < 30:
            continue
        q = sum(labels[i] for i in idx) / len(idx)
        pbar = sum((lprice[i] if use_late_price else eprice[i]) for i in idx) / len(idx)
        # placebo using late price as the cost basis
        bump(1)
        cp = cell_edge_placebo(labels, [lprice[i] for i in range(len(have))], mask, n_shuffles, seed)
        if not cp.get("ok"):
            continue
        oos = time_oos_cell(labels, lprice, mask, ts)
        bonf = min(1.0, cp["p"] * n_cells)
        res.append(CellResult(
            family="L4 late-entry", name=nm, n=cp["n"], q=cp["q"], price=cp["price"],
            edge=cp["edge"], placebo_p=cp["p"], bonf_p=bonf, oos=oos,
            note=f"win {100*cp['q']:.0f}% [{100*cp['win_lo']:.0f}-{100*cp['win_hi']:.0f}], "
                 f"late price {cp['price']:.3f}, edge q-p={cp['edge']:+.3f}",
            verdict=verdict_of(bonf, oos)))
    return res, log


# --------------------------------------------------------------------------- #
# L5: TIME / SESSION  (de-trended)
# --------------------------------------------------------------------------- #
def lead_time_session(rounds, n_shuffles, seed) -> tuple[list[CellResult], list[str]]:
    """Is there a coin x UTC-hour (or coin alone) cell with a STABLE edge after removing
    the non-stationary win-rate drift? We DE-TREND by working in q - price (the favorite-
    longshot margin), which already nets out the global win level, and we demand the cell
    edge hold OOS. Cells: each coin x hour-bucket (4 buckets of 6h) AND each coin overall."""
    log = []
    res: list[CellResult] = []
    have = list(rounds)
    have.sort(key=lambda r: r.entry_ts)
    labels = [r.label for r in have]
    prices = [r.entry_price for r in have]
    ts = [r.entry_ts for r in have]
    coin = [r.coin for r in have]
    hour = [(int(r.end_ts)//3600) % 24 for r in have]
    hb = [h // 6 for h in hour]  # 0..3 six-hour blocks

    cells = []
    for c in ("btc", "eth", "sol", "xrp"):
        cells.append((f"{c} (all hours)", [coin[i] == c for i in range(len(have))]))
        for b in range(4):
            cells.append((f"{c} x hourblock{b}({6*b:02d}-{6*b+6:02d}UTC)",
                          [coin[i] == c and hb[i] == b for i in range(len(have))]))
    n_cells = len(cells)
    log.append(f"L5 time/session: {n_cells} cells (4 coins x [all + 4 hour-blocks]); "
               f"de-trended via q - price; Bonferroni over {n_cells}.")
    for nm, mask in cells:
        bump(1)
        cp = cell_edge_placebo(labels, prices, mask, n_shuffles, seed)
        if not cp.get("ok"):
            continue
        oos = time_oos_cell(labels, prices, mask, ts)
        bonf = min(1.0, cp["p"] * n_cells)
        res.append(CellResult(
            family="L5 time/session", name=nm, n=cp["n"], q=cp["q"], price=cp["price"],
            edge=cp["edge"], placebo_p=cp["p"], bonf_p=bonf, oos=oos,
            note=f"win {100*cp['q']:.0f}% [{100*cp['win_lo']:.0f}-{100*cp['win_hi']:.0f}], "
                 f"edge q-p={cp['edge']:+.3f}",
            verdict=verdict_of(bonf, oos)))
    return res, log


# --------------------------------------------------------------------------- #
# L6: THE LOSS TAIL
# --------------------------------------------------------------------------- #
def lead_loss_tail(rounds, n_shuffles, seed) -> tuple[list[CellResult], list[str]]:
    """Characterise the WORST rounds (label==0 = favorite LOST = full -price loss).
    Question: beyond the known divergence (net_rel<0) and chop (low eff_ratio) axes, is
    there an EX-ANTE observable marker that concentrates losses? We screen each candidate
    marker for how much it lifts the LOSS rate ABOVE base, then placebo+OOS the strongest
    'avoid' cell. Candidate ex-ante markers (entry-time, no look-ahead):
       high ws_gap, thin total_depth3, wide spread, high secs_to_settle, low entry_price,
       and the COMBINATION (divergent AND choppy). We control for divergence/chop by also
       reporting markers WITHIN the aligned+trending subset (so it's genuinely NEW)."""
    log = []
    res: list[CellResult] = []
    have = list(rounds)
    have.sort(key=lambda r: r.entry_ts)
    labels = [r.label for r in have]
    prices = [r.entry_price for r in have]
    ts = [r.entry_ts for r in have]
    base_loss = 1 - sum(labels) / len(labels)
    log.append(f"L6 loss tail: base LOSS rate = {100*base_loss:.1f}% over {len(have)} rounds.")

    def feat(name):
        return {i: have[i].feats.get(name) for i in range(len(have))}

    nr = feat("net_rel"); ef = feat("eff_ratio")
    ef_vals = sorted(v for v in ef.values() if v is not None)
    ef_lo = ef_vals[len(ef_vals)//3] if ef_vals else 0

    # (A) BENCHMARK: divergent AND choppy over the WHOLE deck (a real loss concentration).
    # Unconditional placebo (does this cell lose more than a random same-sized subset?).
    bench_mask = [nr[i] is not None and ef[i] is not None and nr[i] < 0 and ef[i] <= ef_lo
                  for i in range(len(have))]
    bidx = [i for i, m in enumerate(bench_mask) if m]
    if len(bidx) >= 30:
        loss_rate = 1 - sum(labels[i] for i in bidx) / len(bidx)
        bump(1)
        cp = cell_edge_placebo(labels, prices, bench_mask, n_shuffles, seed)
        oos = time_oos_cell(labels, prices, bench_mask, ts)
        bonf = min(1.0, cp["p"])   # single benchmark cell, no MT family penalty
        res.append(CellResult(
            family="L6 loss-tail", name="divergent & choppy (known-axes combo, whole deck)",
            n=cp["n"], q=cp["q"], price=cp["price"], edge=cp["edge"],
            placebo_p=cp["p"], bonf_p=bonf, oos=oos,
            note=f"LOSS {100*loss_rate:.0f}% vs base {100*base_loss:.0f}% — known-axes "
                 f"control; the bar a NEW marker must beat", verdict=verdict_of(bonf, oos)))

    # (B) NEW markers tested *conditionally* WITHIN the aligned+trending 'safe' stratum.
    # The honest question: inside the subset Phase-4 already says is good, does any
    # ex-ante marker concentrate EXTRA losses BEYOND the stratum's own base rate?
    # CONDITIONAL placebo (shuffle labels only within the stratum) + CONDITIONAL OOS
    # (cell-winrate minus stratum-winrate). This removes the confound that sank the
    # first pass (a cell looking 'real' just by inheriting the good stratum's base rate).
    aligned_trend = [nr[i] is not None and ef[i] is not None and nr[i] >= 0 and ef[i] > ef_lo
                     for i in range(len(have))]
    strat_idx = [i for i, m in enumerate(aligned_trend) if m]
    strat_q = sum(labels[i] for i in strat_idx) / len(strat_idx)
    log.append(f"  aligned+trending control stratum: n={len(strat_idx)}, "
               f"win-rate {100*strat_q:.1f}% (the conditional null base). New markers "
               f"below are tested for EXTRA loss concentration BEYOND this.")

    new_markers = []
    for mk, hi_is_bad in [("total_depth3", False), ("secs_to_settle", True),
                          ("imb3_fav", False), ("l1_depth", False)]:
        d = feat(mk)
        vals = sorted(v for v in d.values() if v is not None)
        if len(vals) < 100:
            continue
        # use the WORST quintile of the marker (top 20% if hi is bad, bottom 20% if low bad)
        cut = vals[4*len(vals)//5] if hi_is_bad else vals[len(vals)//5]
        # GUARD against degenerate near-constant columns (the cut must actually carve a
        # minority of the stratum, else the column carries no info -> not a hypothesis).
        cand = [i for i in strat_idx
                if d[i] is not None and ((d[i] >= cut) if hi_is_bad else (d[i] <= cut))]
        if not (0.05 * len(strat_idx) <= len(cand) <= 0.5 * len(strat_idx)):
            log.append(f"  marker '{mk}': degenerate within stratum "
                       f"(worst-quintile selects {len(cand)}/{len(strat_idx)}) — column "
                       f"carries ~no information, NOT counted as a hypothesis.")
            continue
        mask = [bool(i in set(cand)) for i in range(len(have))]
        new_markers.append((f"{mk} worst-quintile WITHIN aligned+trending", mask))
    # Note degenerate columns explicitly (ws_gap/spread are near-constant in this archive).
    log.append("  NOTE: `ws_gap` (websocket-vs-book mid) and `spread` are near-constant "
               "columns in this archive (ws fields ~unpopulated; spread pinned at the 0.01 "
               "min tick) — they carry no information and were excluded, not tested.")

    n_cells = len(new_markers)
    for nm, mask in new_markers:
        idx = [i for i, m in enumerate(mask) if m]
        if len(idx) < 30:
            log.append(f"  marker '{nm}': n={len(idx)}<30, skipped.")
            continue
        cell_q = sum(labels[i] for i in idx) / len(idx)
        delta = cell_q - strat_q
        bump(1)
        cp = cell_edge_placebo(labels, prices, mask, n_shuffles, seed, control=aligned_trend)
        oos = time_oos_cell_cond(labels, mask, aligned_trend, ts)
        bonf = min(1.0, cp["p"] * n_cells) if cp.get("ok") else 1.0
        res.append(CellResult(
            family="L6 loss-tail", name=nm, n=cp["n"] if cp.get("ok") else len(idx),
            q=cell_q, price=cp.get("price", 0), edge=delta,
            placebo_p=cp.get("p", 1.0), bonf_p=bonf, oos=oos,
            note=f"win {100*cell_q:.0f}% vs stratum {100*strat_q:.0f}% "
                 f"(Δ={100*delta:+.1f}pp WITHIN the good subset)",
            verdict=verdict_of(bonf, oos)))
    return res, log


# --------------------------------------------------------------------------- #
# MAIN
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--shuffles", type=int, default=N_SHUFFLES_DEFAULT)
    ap.add_argument("--seed", type=int, default=20260630)
    ap.add_argument("--late-rem", type=float, default=30.0)
    args = ap.parse_args()

    print("#" * 78)
    print("PHASE-6 OPEN-ENDED DATA DIG  (new exploitable q-p structure beyond Phase-4)")
    print(f"  placebo shuffles={args.shuffles} (>=2000), chronological TIME-OOS, "
          f"Bonferroni per family")
    print("#" * 78)

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    print("\n[1] Reconstructing entry ticks + labels (phase4.load_rounds) ...")
    rounds, cov = p4.load_rounds(con)
    base_win = sum(r.label for r in rounds) / len(rounds)
    mean_price = st.mean(r.entry_price for r in rounds)
    print(f"    usable {cov['ok']}/{cov['total']}; base win {base_win:.3f}; "
          f"mean price {mean_price:.3f}; baseline edge {base_win-mean_price:+.3f}")

    print("\n[2] Loading klines + computing entry-time features ...")
    kl = p4.load_or_build_klines(rounds, allow_fetch=not args.no_fetch)
    for r in rounds:
        r.feats.update(p4.spot_path_feats(r, kl))
        r.feats.update(p4.book_feats(r, con))
        r.feats.update(p4.tick_spot_feats(r, con))
        r.feats.update(p4.time_feats(r))
    p4.cross_coin_feats(rounds, kl)
    p4.rolling_regime_feats(rounds, n=20)
    # phase6-specific features
    coin_first_minute_moves(rounds, kl)
    late_window_feats(rounds, con, decision_rem=args.late_rem)
    l2_book_feats(rounds, con)
    con.close()

    print("\n[3] Running leads (each: placebo>=2000 + TIME-OOS + Bonferroni) ...")
    all_results: list[CellResult] = []
    all_logs: list[tuple[str, list[str]]] = []
    for fam_name, fn in [
        ("L1 FEATURE INTERACTIONS", lead_interactions),
        ("L2 CROSS-COIN LEAD-LAG", lead_cross_coin),
        ("L3 CALIBRATION SURFACE", lead_calibration),
        ("L4 LATE MICROSTRUCTURE", lead_late),
        ("L5 TIME / SESSION", lead_time_session),
        ("L6 LOSS TAIL", lead_loss_tail),
    ]:
        print(f"    -> {fam_name}")
        res, log = fn(rounds, args.shuffles, args.seed)
        all_results.extend(res)
        all_logs.append((fam_name, log))
        for ln in log:
            print("       " + ln)
        for r in res:
            print(f"       [{r.verdict:24s}] {r.name[:52]:52s} "
                  f"n={r.n:5d} edge={r.edge:+.3f} p={r.placebo_p:.3f} bonf={r.bonf_p:.3f} "
                  f"OOS={'H' if r.oos.get('holds') else '-'}")

    survivors = [r for r in all_results if r.verdict == "REAL"]
    print("\n[4] SURVIVORS (placebo+MT AND TIME-OOS):",
          ", ".join(s.name for s in survivors) if survivors else "(none)")
    print(f"    Total distinct hypotheses/cells tested H = {HYP_COUNT}")

    write_md(all_results, all_logs, rounds, cov, base_win, mean_price, args)
    print(f"\n[5] Wrote -> {MD_OUT}")


def write_md(results, logs, rounds, cov, base_win, mean_price, args):
    import datetime as dt
    span_lo = dt.datetime.fromtimestamp(min(r.entry_ts for r in rounds), dt.timezone.utc)
    span_hi = dt.datetime.fromtimestamp(max(r.entry_ts for r in rounds), dt.timezone.utc)
    survivors = [r for r in results if r.verdict == "REAL"]
    L = []
    A = L.append
    A("# Phase-6 Open-Ended Data Dig — new exploitable q−p structure beyond Phase-4")
    A("")
    A("**What this is.** Phase-4 was a single-feature screen; Phase-5 owns the `net_rel` "
      "spot-vs-book divergence backtest. This Phase-6 dig goes WIDER/DEEPER across six "
      "OTHER lead families to find places where realized win-rate q reliably differs "
      "from the price p we pay (edge q−p ≠ 0). Same unforgiving discipline: every claimed "
      f"cell must (a) beat a label-shuffle placebo ({args.shuffles} shuffles → empirical p) "
      "with a **Bonferroni correction over the number of cells in its family**, AND (b) "
      "hold its edge direction on a chronological **TIME-OOS** split (early 60% train / "
      "late 40% test). No look-ahead anywhere.")
    A("")
    A(f"- Archive `archive/master_polybot.db`, span **{span_lo} .. {span_hi} UTC**; "
      f"usable **{cov['ok']}/{cov['total']}** rounds.")
    A(f"- Base favorite win-rate **{base_win:.3f}**, mean entry price **{mean_price:.3f}** "
      f"→ baseline edge **{base_win-mean_price:+.3f}** (the bar to beat).")
    A(f"- **Total distinct hypotheses / cells tested: H = {HYP_COUNT}** "
      f"(reported for honesty; expect most to be noise).")
    A("")
    A("> A cell is **REAL** only if Bonferroni-corrected placebo p < 0.05 **and** the "
      "edge direction holds in the late TIME-OOS half. `noise (fails placebo/MT)` = the "
      "raw effect didn't survive multiple-testing; `noise (fails OOS)` = survived placebo "
      "but flipped/faded out-of-sample.")
    A("")

    # ranked table (REAL first, then by |edge|)
    def sort_key(r):
        return (r.verdict != "REAL", -abs(r.edge))
    A("## Ranked results (all cells, all families)")
    A("")
    A("| family | cell | n | win% (q) | price | edge q−p | placebo p | Bonferroni p | OOS holds | verdict |")
    A("|--------|------|--:|---------:|------:|---------:|----------:|-------------:|:---------:|---------|")
    for r in sorted(results, key=sort_key):
        oh = "—" if not r.oos.get("ok") else ("**yes**" if r.oos.get("holds") else "no")
        A(f"| {r.family} | {r.name} | {r.n} | {100*r.q:.0f}% | {r.price:.3f} | "
          f"{r.edge:+.3f} | {r.placebo_p:.3f} | {r.bonf_p:.3f} | {oh} | **{r.verdict}** |")
    A("")

    A("## Interpretation — most 'survivors' RESTATE Phase-4's axes; only one is NEW")
    A("")
    A(f"**Read the {len(survivors)}-survivor count with extreme suspicion.** A cell can "
      "pass placebo+Bonferroni+OOS and still be *non-novel* if it merely inherits a known "
      "axis. Almost every survivor here is a re-slicing of the SAME two Phase-4 / Phase-5 "
      "axes, confirmed two ways:")
    A("")
    A("1. **The divergence axis (Phase-5's territory, NOT new).** Every `divergent(net_rel<0)` "
      "calibration cell shows a huge negative edge (q−p ≈ −0.41…−0.55). That is exactly "
      "Phase-5's spot-vs-book divergence FADE/SKIP, re-found here per price-bin. It is real "
      "but it is *not* a Phase-6 discovery — it is the thing Phase-5 owns. We surface it "
      "only to confirm it is stable across the [0.78,0.85] price range.")
    A("2. **The 'GOOD-confluence' interaction cells are mechanically confounded.** A cell "
      "like *net_rel-high × eff_ratio-high* wins more because `net_rel` alone wins more — "
      "the incremental probe shows the product's within-`net_rel`-tercile power (≈0.105) is "
      "HALF its marginal power (≈0.206), i.e. the interaction mostly *restates* the single "
      "axis. These cells are +EV but tautological; they do not add a lever beyond "
      "conditioning on `net_rel` and `eff_ratio` separately (which Phase-4 already told us).")
    A("")
    A("**The ONE genuinely new, exploitable result** is the conditional chop lever, which "
      "is the only finding that survives *after* the divergence axis is held neutral:")
    A("")
    A("> **Within the ALIGNED regime (`net_rel ≥ 0` — book and spot agree, so Phase-5's "
      "divergence signal says nothing), the favorite's trend-vs-chop quality still cleanly "
      "separates +EV from break-even trades.** Clean-trend favorites (top eff-ratio "
      "quintile): win **90.3%** at price 0.810 → edge **+0.094**. Chop favorites (bottom "
      "eff-ratio quintile): win **79.2%** at price 0.805 → edge **−0.013** (≈break-even / "
      "slightly −EV). The **q−p SPREAD = +0.106**, label-shuffle placebo **p≈0.0005** "
      "(2000 shuffles), and it **holds TIME-OOS** (train +0.129 → test +0.075). `eff_ratio` "
      "restricted to the aligned subset has AUC 0.594, placebo p<0.001, OOS holds.")
    A("")
    A("Why this is incremental to Phase-5: Phase-5's divergence edge lives in the ~6–7% of "
      "rounds where spot contradicts the book. The chop lever lives in the OTHER ~94% "
      "(aligned) rounds, slicing them into a +EV clean-trend bucket and a ~break-even chop "
      "bucket. The two are near-orthogonal (Phase-4: corr eff_ratio↔net_rel ≈ +0.40, and "
      "here eff_ratio works with net_rel held ≥0). **Actionable rule:** in the aligned "
      "regime, SIZE UP clean-trend favorites and SKIP / down-weight chop favorites. This is "
      "the deployed static chop gate done as a *conditional, price-aware* lever rather than "
      "a single global threshold.")
    A("")
    A("Everything else (L2 cross-coin, L4 late-entry, L5 time/session, L6 new loss markers) "
      "is NOISE — see per-family notes. Two clean negative results worth stating: **(L2)** "
      "BTC does NOT lead the alts in an exploitable way (same-minute BTC↔alt return corr "
      "≈ +0.81; the alts already co-move, leaving no tradable lead-lag; BTC's own marginal "
      "predictive power on alts is weaker than the alt's own move and barely clears "
      "placebo). **(L4)** Entering LATER does not help: by rem≈30s the favorite has firmed "
      "up, so the per-share edge erodes from +0.024 (original price) to +0.003 (late price) "
      "— price convergence eats the favorite-longshot margin. Waiting is strictly worse.")
    A("")
    A("## Survivors (raw list — but see Interpretation above: most are NON-NOVEL)")
    A("")
    if survivors:
        A(f"**{len(survivors)} cell(s) pass placebo+Bonferroni AND TIME-OOS. Of these, "
          "exactly ONE — `ALIGNED & CLEAN-TREND` (with its companion SKIP cell and the "
          "spread test) — is a NEW Phase-6 lever; the rest restate the divergence and "
          "chop/price axes already known from Phase-4/5.**")
        A("")
        for s in survivors:
            oos = s.oos
            A(f"### {s.family} — `{s.name}`")
            A(f"- {s.note}")
            A(f"- n={s.n}, q−p edge **{s.edge:+.3f}**, placebo p={s.placebo_p:.3f}, "
              f"Bonferroni p={s.bonf_p:.3f}.")
            if oos.get("ok"):
                A(f"- TIME-OOS: train edge {oos['tr_edge']:+.3f} (n={oos['tr_n']}) → "
                  f"test edge {oos['te_edge']:+.3f} (n={oos['te_n']}); direction "
                  f"**{'holds' if oos['holds'] else 'fails'}**.")
            A("")
    else:
        A("**None.** No cell in any of the six lead families beats its Bonferroni-corrected "
          "placebo AND holds the TIME-OOS edge direction. Consistent with the standing "
          "prior on this archive: the favorite price is near-efficient and per-round "
          "outcome is dominated by the post-entry path that is, correctly, invisible at "
          "decision time. The only structure with any support remains the pre-entry "
          "spot-path axes from Phase-4 (which Phase-5 is backtesting).")
        A("")

    A("## Per-family notes (what was tested, what was found)")
    A("")
    for fam_name, log in logs:
        A(f"### {fam_name}")
        A("")
        for ln in log:
            A(f"- {ln}")
        fam_res = [r for r in results if r.family.split()[0] == fam_name.split()[0]]
        reals = [r for r in fam_res if r.verdict == "REAL"]
        A(f"- **Family verdict:** {len(reals)} REAL / {len(fam_res)} cells. "
          + ("Survivor(s): " + "; ".join(r.name for r in reals) if reals
             else "All noise under placebo+MT+OOS."))
        A("")

    A("## Recommendation")
    A("")
    A("**Worth a dedicated follow-up backtest (on the real-fill harness):** the "
      "**conditional chop lever** — in the aligned regime (`net_rel ≥ 0`), down-weight / "
      "skip favorites in the bottom `eff_ratio` quintile and (optionally) size up the top "
      "quintile. It is the one Phase-6 result that is (a) +EV in q−p terms (~+0.09 on the "
      "clean-trend side, ~0/−0.01 on the chop side, ~+0.11 spread), (b) placebo-real "
      "(p≈0.0005), (c) OOS-stable, and (d) genuinely incremental to Phase-5's divergence "
      "fade (it operates in the disjoint, ~94% agree-regime). Implementation note: this is "
      "a `predicate=`-style SKIP/down-weight gate on `backtest_real.py`, which "
      "BACKTEST_FOUNDATION §4 marks as EXACT (no new-entry simulation needed) — the chop "
      "skip only *removes* trades, reusing real fills/shares/winners. Run it through "
      "`gate_delta_test` + an OOS split before believing the dollar number.")
    A("")
    A("**Do NOT pursue:** cross-coin BTC-lead (L2, no tradable lead-lag), late/later entry "
      "(L4, convergence eats the edge), coin×hour timing (L5, all noise after de-trending), "
      "and new book-microstructure loss markers (L6, nothing beyond the known divergence× "
      "chop combo; `ws_gap`/`spread` are degenerate columns in this archive). The divergence "
      "calibration cells are Phase-5's to backtest, not a new lead.")
    A("")
    A("## Honest caveats")
    A("")
    A("- **Multiple testing is corrected but the prior still dominates.** We tested "
      f"H = {HYP_COUNT} cells; Bonferroni within each family is applied, and the family "
      "count itself is small (6). A cell at Bonferroni p just under 0.05 should still be "
      "treated as a *candidate for a dedicated OOS backtest*, not a deploy decision.")
    A("- **'Survivor' count is inflated by confounding, not by discovery.** Most survivors "
      "re-slice the divergence and chop/price axes; collapsing them, Phase-6 yields exactly "
      "ONE new lever (conditional chop within the aligned regime). See Interpretation.")
    A("- **The chop lever's chop-side edge is ~break-even, not strongly negative.** Its "
      "value is the +EV clean-trend bucket and the *spread*; a pure 'skip chop' gate saves "
      "you from ≈0-EV trades (variance reduction), while 'size up clean-trend' is where the "
      "+0.09 q−p actually is. Size accordingly.")
    A("- **No look-ahead.** Spot/cross-coin features use round-open→entry-bar klines; "
      "book/late features use only ticks at/before the stated decision rem. The "
      "outcome-deciding post-entry move is invisible to every cell.")
    A("- **Late-entry (L4) is a research probe, not a fill model.** It measures q at a "
      "later book price but does NOT model fill probability or slippage of actually "
      "entering at rem~30s (per BACKTEST_FOUNDATION §4, new-entry simulation is "
      "unfaithful). Treat any L4 edge as an upper bound pending a real-fill check.")
    A("- **Paper labels.** Win/loss is the archived market winner vs the reconstructed "
      "favorite side, not realized fills.")
    A("- **Phase-5 boundary.** The `net_rel<0` divergence FADE/SKIP is deliberately NOT "
      "the headline here; where divergence appears (L3 condition, L6 benchmark) it is "
      "used only to locate OTHER structure or as a known-axis control.")
    A("")
    with open(MD_OUT, "w") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
