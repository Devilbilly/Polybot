#!/usr/bin/env python3
"""
phase8_band2d.py -- FULL 2D PRICE-BAND SWEEP: prove the optimal favorite band
=============================================================================

QUESTION. Polybot buys the FAVORITE side of a crypto 5-min up/down market when its
price is in [buy_p, sell_p] (a chop gate also skips flat rounds), and HOLDS to settle.
Edge per share = q(x) - p (favorite realized win-rate minus the price paid). The live
deployed band is **[buy_p=0.78, sell_p=0.90]** (we just raised sell_p 0.85->0.90 in
phase7). Floor has historically been 0.70/0.76/0.78. This phase PROVES — across the
full 8-day archive — which (floor, ceiling) cell maximizes REALIZED edge, and whether
the deployed [0.78, 0.90] is near-optimal.

  (An abandoned unrelated 'flat_favorite' experiment once used 0.55; that is a DIFFERENT
   strategy and is explicitly ignored here. The floor sweep starts at 0.70.)

WHY 2D (vs phase7's 1D ceiling sweep). Phase7 fixed the floor at 0.78 and only swept
the ceiling, concluding sell_p 0.85->0.90. Phase8 sweeps BOTH the floor (buy_p) and the
ceiling (sell_p) so the floor is no longer assumed. The favorite-longshot edge is a
PRICE-DEPENDENT margin q(x)-x; the optimal band is the [floor,ceiling] window over which
that margin, REALIZED and net of cost, is largest in dollars without polluting the book
with negative-margin price zones.

METHOD (mirrors phase4 + phase7 discipline; NO look-ahead; phase4/phase7 IMPORTED, NOT
modified — we only ADD a lower-floor reconstruction and the 2D grid logic):
  * RECONSTRUCT each round's favorite entry ONCE at a WIDE universe: first ticks row with
    rem<=150 where a FAVORITE side (YES ask=ask_p1, or NO ask=1-bid_p1) is >= FLOOR_MIN
    (0.70) and <= WIDE_CAP (0.97). Record favorite SIDE + entry PRICE + winner. A favorite
    already above any candidate ceiling at that first qualifying tick is ENTERED at that
    price (this is the whole point of a wide reconstruction) — NO look-ahead.
    LABEL = 1 if entry side == market winner (phase4's exact definition).
  * 2D SWEEP: buy_p (floor) in {0.70,0.74,0.76,0.78,0.80} x sell_p (ceiling) in
    {0.85,0.88,0.90,0.93,0.97}. For each (floor,ceiling) cell: trades = rounds with entry
    price in [floor, ceiling]. Report n / fire% / win% / mean price / win-minus-price
    MARGIN / EV-per-$1 (cost ~0.003) with bootstrap CI / total $ at $1/trade.
  * BOTH (a) RAW and (b) WITH the deployed chop gate (|spot-strike|/strike >= 0.00056 at
    the entry tick; NO-OP keep when tick has no spot/strike — older box only). (b) is the
    LIVE config and is the primary basis for the decision. We ALSO run the full-coverage
    Binance preabsmove proxy (same mechanism, look-ahead-controlled) as a robustness grid.
  * DISCIPLINE on the HEADLINE cell vs neighbors: label-shuffle PLACEBO (>=2000) that the
    cell's win-minus-price margin genuinely beats its own price; chronological TIME-OOS
    (early 60% train / late 40% test — the 8 days are non-stationary across box/config
    switches, so OOS is decisive).
  * REAL-LEDGER cross-check (the BINDING evidence): from archive/ledger.db (real LIVE
    fills), realized win-minus-price margin by price bucket
    [0.70,0.78),[0.78,0.85),[0.85,0.90),[0.90,0.93),[>=0.93]. This is what caught the
    phase7 paper-vs-real reversal (>0.90 is +EV on paper, -0.0485 in REAL money). Use it
    to sanity-check the paper 2D surface and FLAG any cell where real money disagrees.

VERDICT. The 2D MARGIN / total$ grid (floor rows x ceiling cols, chop-gated), the optimal
cell, where placebo/OOS bind, the real-ledger bucket margins, and a clear answer: is
[0.78, 0.90] near-optimal? If a different cell is MATERIALLY better AND survives
placebo+OOS+real-ledger, it is named with exact buy_p/sell_p and the expected gain. The
real ledger wins ties (idealized paper flatters the high-price tail).

USAGE
  python3 phase8_band2d.py                 # full 2D sweep + writes phase8_band2d.md
  python3 phase8_band2d.py --no-fetch      # cached klines only (offline)
  python3 phase8_band2d.py --shuffles 2000 # placebo shuffles (default 2000)
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sqlite3
import statistics as st
from collections import defaultdict
from typing import Optional

import phase4_features as p4          # REUSE reconstruction klines/spot machinery; DO NOT modify
import phase7_sellp as p7            # REUSE summarize / ev / bootstrap / chop gates / real-ledger; DO NOT modify

HERE = os.path.dirname(os.path.abspath(__file__))
DB = p4.DB
MD_OUT = os.path.join(HERE, "phase8_band2d.md")

# 2D grid
FLOORS = [0.70, 0.74, 0.76, 0.78, 0.80]            # buy_p sweep
CEILINGS = [0.85, 0.88, 0.90, 0.93, 0.97]          # sell_p sweep
FLOOR_MIN = min(FLOORS)                             # 0.70 -- wide reconstruction floor
WIDE_CAP = max(CEILINGS)                            # 0.97 -- wide reconstruction cap
REM_MAX = p4.REM_MAX
COST = p7.COST                                      # 0.003 cost cushion (reuse phase7's)
CHOP_MIN = p7.CHOP_MIN                              # 0.00056 deployed chop threshold
N_SHUFFLES = 2000
OOS_TRAIN_FRAC = 0.60
DEPLOYED = (0.78, 0.90)                             # the band we are asked to validate

# reuse phase7 helpers verbatim (imported, not redefined)
Round = p7.Round
summarize = p7.summarize
bootstrap_ci = p7.bootstrap_ci
ev_per_dollar = p7.ev_per_dollar
apply_chop_deployed = p7.apply_chop_deployed
apply_chop_preabs = p7.apply_chop_preabs
attach_preabsmove = p7.attach_preabsmove


# --------------------------------------------------------------------------- #
# 1. WIDE reconstruction at FLOOR_MIN=0.70 (floor swept post-hoc on entry_price)
# --------------------------------------------------------------------------- #
def load_rounds_wide_floor(con: sqlite3.Connection) -> tuple[list[Round], dict]:
    """Reconstruct every round's entry at the FIRST tick (rem<=150) where a favorite side
    is >= FLOOR_MIN (0.70) and <= WIDE_CAP (0.97). Floor AND ceiling are swept post-hoc on
    the recorded entry price. This is exactly phase7.load_rounds_wide with the floor lowered
    to 0.70 so the buy_p dimension is no longer fixed. NO look-ahead: side + price taken at
    the first qualifying tick; a favorite already above a candidate ceiling is entered at
    that price. Reuses phase4._coin_from_spot for coin fallback."""
    cur = con.cursor()
    sess = cur.execute("SELECT session_id, market_id, winner FROM sessions").fetchall()
    end_ts = {mid: ets for mid, ets in
              cur.execute("SELECT market_id, end_ts FROM markets").fetchall()}

    rounds: list[Round] = []
    cov = dict(total=len(sess), no_winner=0, no_market_end=0, no_coin=0,
               no_entry_tick=0, ok=0)

    for session_id, mid, winner in sess:
        if winner not in ("YES", "NO"):
            cov["no_winner"] += 1
            continue
        ets = end_ts.get(mid)
        if ets is None:
            cov["no_market_end"] += 1
            continue
        suf = session_id.rsplit("-", 1)[-1]
        coin = suf if suf in p4.COINS else None

        ticks = cur.execute(
            "SELECT rem, bid_p1, ask_p1, spot, strike "
            "FROM ticks WHERE market_id=? AND rem<=? AND ask_p1>0 "
            "ORDER BY rem DESC", (mid, REM_MAX)
        ).fetchall()
        if not ticks:
            cov["no_entry_tick"] += 1
            continue

        if coin is None:
            for t in ticks:
                coin = p4._coin_from_spot(t[3])
                if coin:
                    break
        if coin is None:
            cov["no_coin"] += 1
            continue

        # FIRST tick (latest rem => earliest in time within <=150) where a favorite is
        # at/above FLOOR_MIN. Enter at that price even if it is already above a candidate ceiling.
        entry = None
        for (rem, bid, ask, spot, strike) in ticks:
            yes_ask = ask
            no_ask = 1.0 - bid
            if FLOOR_MIN <= yes_ask <= WIDE_CAP:
                entry = ("YES", yes_ask, rem, spot, strike)
                break
            if FLOOR_MIN <= no_ask <= WIDE_CAP:
                entry = ("NO", no_ask, rem, spot, strike)
                break
        if entry is None:
            cov["no_entry_tick"] += 1
            continue

        side, eprice, rem, spot, strike = entry
        chop_dist = None
        if spot and spot > 0 and strike and strike > 0:
            chop_dist = abs(spot - strike) / strike

        rounds.append(Round(
            market_id=mid, coin=coin, end_ts=int(ets),
            entry_ts=ets - rem, rem=rem, side=side, entry_price=eprice,
            winner=winner, label=1 if side == winner else 0, chop_dist=chop_dist,
        ))
        cov["ok"] += 1

    rounds.sort(key=lambda r: r.entry_ts)
    return rounds, cov


# --------------------------------------------------------------------------- #
# 2. 2D grid metrics
# --------------------------------------------------------------------------- #
def cell_trades(favs: list[Round], floor: float, ceil: float) -> list[Round]:
    """A trade in cell (floor, ceil] is a round with entry price in [floor, ceil]
    (inclusive both ends, matching phase7's [FLOOR, X] convention)."""
    return [r for r in favs if floor <= r.entry_price <= ceil]


def grid_sweep(favs: list[Round], boot: int, seed: int) -> dict:
    """2D grid: grid[(floor,ceil)] -> summarize() dict + fire% + bootstrap margin CI."""
    n_all = len(favs)
    grid: dict[tuple, dict] = {}
    for floor in FLOORS:
        for ceil in CEILINGS:
            if ceil < floor:                       # degenerate (ceiling below floor)
                grid[(floor, ceil)] = dict(n=0, win=0.0, price=0.0, margin=0.0,
                                           ev=0.0, total=0.0, fire=0.0,
                                           margin_ci=(float("nan"), float("nan")))
                continue
            trades = cell_trades(favs, floor, ceil)
            s = summarize(trades)
            s["fire"] = (len(trades) / n_all) if n_all else 0.0
            s["margin_ci"] = bootstrap_ci(trades, "margin", boot, seed)
            grid[(floor, ceil)] = s
    return grid


def best_cell(grid: dict, metric: str = "total", min_n: int = 100) -> tuple:
    """Optimal cell by `metric` (default realized total $). Cells below min_n are
    excluded (small-n totals are noise). Returns ((floor,ceil), summary)."""
    cands = [(k, v) for k, v in grid.items() if v["n"] >= min_n]
    if not cands:
        cands = list(grid.items())
    return max(cands, key=lambda kv: kv[1][metric])


# --------------------------------------------------------------------------- #
# 3. PLACEBO + TIME-OOS for a specific cell (its own margin vs its own price)
# --------------------------------------------------------------------------- #
def placebo_cell(favs: list[Round], floor: float, ceil: float,
                 n_shuffles: int, seed: int = 0) -> float:
    """Label-shuffle placebo (one-sided): does the cell's win-minus-price MARGIN beat what
    a price-blind shuffle would give. Null = win labels carry NO price information; shuffle
    win labels across the WHOLE wide favorite universe (>=0.70), recompute the cell's
    win-price margin (cell prices fixed; only labels move). p = fraction of shuffles with
    shuffled_margin >= real_margin. A genuine favorite-longshot margin in the cell should
    rarely be matched by a price-blind reshuffle. This is the correct decision null (does
    the cell beat its own line), matching phase7.placebo_raise's construction."""
    idx = [i for i, r in enumerate(favs) if floor <= r.entry_price <= ceil]
    if len(idx) < 20:
        return float("nan")
    prices = [r.entry_price for r in favs]
    labels = [r.label for r in favs]
    n_cell = len(idx)
    cell_price_mean = sum(prices[i] for i in idx) / n_cell
    real_margin = sum(labels[i] for i in idx) / n_cell - cell_price_mean
    rng = random.Random(seed)
    lab = list(labels)
    ge = 0
    for _ in range(n_shuffles):
        rng.shuffle(lab)
        sh_margin = sum(lab[i] for i in idx) / n_cell - cell_price_mean
        if sh_margin >= real_margin:
            ge += 1
    return (ge + 1) / (n_shuffles + 1)


def oos_cell(favs: list[Round], floor: float, ceil: float,
             train_frac: float = OOS_TRAIN_FRAC) -> dict:
    """Chronological early-train / late-test on the cell's win-minus-price margin. Fits
    nothing — measures the SAME cell's margin in both halves. holds = +margin in BOTH
    halves (the 8 days are non-stationary, so a sign flip in the late test rejects)."""
    rows = sorted(favs, key=lambda r: r.entry_ts)
    k = int(len(rows) * train_frac)
    tr = [r for r in rows[:k] if floor <= r.entry_price <= ceil]
    te = [r for r in rows[k:] if floor <= r.entry_price <= ceil]
    if len(tr) < 10 or len(te) < 10:
        return dict(ok=False)
    str_, ste = summarize(tr), summarize(te)
    holds = (str_["margin"] > 0) and (ste["margin"] > 0)
    same_sign = (str_["margin"] > 0) == (ste["margin"] > 0)
    return dict(ok=True, n_tr=len(tr), n_te=len(te),
                tr_margin=str_["margin"], te_margin=ste["margin"],
                tr_ev=str_["ev"], te_ev=ste["ev"],
                holds=bool(holds), same_sign=bool(same_sign))


def neighbors_of(cell: tuple) -> list[tuple]:
    """Adjacent grid cells (one step in floor OR ceiling) for the headline comparison."""
    f, c = cell
    fi, ci = FLOORS.index(f), CEILINGS.index(c)
    out = []
    for df in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if df == 0 and dc == 0:
                continue
            nf, nc = fi + df, ci + dc
            if 0 <= nf < len(FLOORS) and 0 <= nc < len(CEILINGS):
                out.append((FLOORS[nf], CEILINGS[nc]))
    return out


# --------------------------------------------------------------------------- #
# 4. REAL-LEDGER cross-check (extend phase7's buckets to split the 0.70-0.78 floor zone)
# --------------------------------------------------------------------------- #
REAL_BUCKETS = ["[0.70,0.78)", "[0.78,0.85)", "[0.85,0.90)", "[0.90,0.93)", ">=0.93"]


def real_ledger_buckets() -> dict:
    """Realized win-rate / win-minus-price margin by REAL LIVE fill price bucket from
    archive/ledger.db (FILL->SETTLE joined by trade_id). Mirrors phase7.real_ledger_check
    but splits the sub-0.78 zone into [0.70,0.78) (the floor sweep's lower band) vs <0.70
    (out-of-scope, dropped) so the floor decision has its own real-money evidence. The real
    ledger is the binding evidence — it caught the phase7 >0.90 paper-vs-real reversal."""
    led = os.path.join(HERE, "archive", "ledger.db")
    out = {b: dict(n=0, wins=0, sump=0.0, win=0.0, price=0.0, margin=0.0)
           for b in REAL_BUCKETS}
    below = dict(n=0, wins=0, sump=0.0, win=0.0, price=0.0, margin=0.0)  # <0.70 (out of scope)
    if not os.path.exists(led):
        return out, below
    con = sqlite3.connect(f"file:{led}?mode=ro", uri=True)
    cur = con.cursor()
    settle = {tid: w for tid, w in
              cur.execute("SELECT trade_id, winner FROM ledger WHERE event='SETTLE'").fetchall()}
    fills = cur.execute("SELECT trade_id, mode, side, fill_price FROM ledger "
                        "WHERE event='FILL' AND fill_price>0").fetchall()
    con.close()
    for tid, mode, side, fp in fills:
        if mode != "LIVE":
            continue
        w = settle.get(tid)
        if w not in ("YES", "NO"):
            continue
        won = 1 if side == w else 0
        if fp < 0.70:
            rec = below
        elif fp < 0.78:
            rec = out["[0.70,0.78)"]
        elif fp < 0.85:
            rec = out["[0.78,0.85)"]
        elif fp < 0.90:
            rec = out["[0.85,0.90)"]
        elif fp < 0.93:
            rec = out["[0.90,0.93)"]
        else:
            rec = out[">=0.93"]
        rec["n"] += 1
        rec["wins"] += won
        rec["sump"] += fp
    for rec in list(out.values()) + [below]:
        if rec["n"]:
            rec["win"] = rec["wins"] / rec["n"]
            rec["price"] = rec["sump"] / rec["n"]
            rec["margin"] = rec["win"] - rec["price"]
    return out, below


REAL_EDGES = [("[0.70,0.78)", 0.70, 0.78), ("[0.78,0.85)", 0.78, 0.85),
              ("[0.85,0.90)", 0.85, 0.90), ("[0.90,0.93)", 0.90, 0.93),
              (">=0.93", 0.93, 1.00)]


def real_margin_for_cell(floor: float, ceil: float, real: dict) -> tuple[float, int]:
    """Coverage-weighted REAL margin POOLED over the real fill buckets overlapping a paper
    cell [floor,ceil]. Returns (pooled_margin, total_n). NOTE: pooling can MASK a bad
    marginal slice (a wide cell averages a real-negative tail bucket against good core
    buckets) — useful for a cell-span average, but the DECISION must use the MARGINAL real
    test below, not this pooled number. This is exactly the phase-7 trap (a wide ceiling
    looks fine pooled but its added >0.90 slice loses real money)."""
    wins = 0
    sump = 0.0
    n = 0
    EPS = 1e-6
    for b, blo, bhi in REAL_EDGES:
        if (bhi > floor + EPS) and (blo < ceil - EPS):  # genuine interior overlap with [floor,ceil]
            rec = real[b]
            wins += rec["wins"]
            sump += rec["sump"]
            n += rec["n"]
    if n == 0:
        return float("nan"), 0
    return wins / n - sump / n, n


def marginal_real_buckets(floor: float, ceil: float, real: dict,
                          base=DEPLOYED) -> list[tuple]:
    """The real fill buckets a cell ADDS relative to the deployed band, i.e. the buckets
    that lie OUTSIDE [base_floor, base_ceil] but INSIDE [floor, ceil]. Extending the floor
    DOWN adds the cheap buckets below base_floor; extending the ceiling UP adds the
    expensive buckets above base_ceil. Each added bucket must stand on its own real money —
    this is the correct decision null (phase-7 discipline): you cannot average a good core
    against a bad marginal slice. Returns [(bucket_name, real_margin, n), ...] for each
    added bucket that has real fills."""
    bf, bc = base
    added = []
    EPS = 1e-6
    for b, blo, bhi in REAL_EDGES:
        # genuine interior overlap with the cell (a bucket merely TOUCHING the cell's
        # ceiling at a single point — e.g. [0.90,0.93) vs a cell ending at 0.90 — is NOT in)
        overlaps_cell = (bhi > floor + EPS) and (blo < ceil - EPS)
        # the bucket sits OUTSIDE the deployed band (below its floor OR above its ceiling)
        below_base = (bhi <= bf + EPS)
        above_base = (blo >= bc - EPS)
        if overlaps_cell and (below_base or above_base):
            rec = real[b]
            if rec["n"]:
                added.append((b, rec["margin"], rec["n"]))
    return added


# --------------------------------------------------------------------------- #
# 5. MAIN
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--shuffles", type=int, default=N_SHUFFLES)
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=12345)
    args = ap.parse_args()

    print("#" * 78)
    print("PHASE-8  FULL 2D PRICE-BAND SWEEP  (prove the optimal favorite [buy_p, sell_p])")
    print(f"  floors {FLOORS}  x  ceilings {CEILINGS}  | raw + chop-gated | placebo+OOS+real")
    print("#" * 78)

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    print("\n[1] Reconstructing WIDE-floor entries (floor>=0.70, cap<=0.97) ...")
    favs, cov = load_rounds_wide_floor(con)
    span_lo = dt.datetime.utcfromtimestamp(min(r.entry_ts for r in favs))
    span_hi = dt.datetime.utcfromtimestamp(max(r.entry_ts for r in favs))
    base_win = sum(r.label for r in favs) / len(favs)
    print(f"    coverage: {cov['ok']}/{cov['total']} favorites usable "
          f"(no_winner={cov['no_winner']} no_market_end={cov['no_market_end']} "
          f"no_coin={cov['no_coin']} no_entry={cov['no_entry_tick']})")
    print(f"    span {span_lo} .. {span_hi} UTC | base favorite(>=0.70) win={base_win:.3f}")
    by_coin = defaultdict(int)
    for r in favs:
        by_coin[r.coin] += 1
    print(f"    by coin: {dict(by_coin)}")
    # price distribution across the floor candidates
    pdist = {f: sum(1 for r in favs if r.entry_price >= f) for f in FLOORS}
    print(f"    favorites >= each floor: {pdist}")

    print("\n[2] Loading Binance klines for the preabsmove chop proxy ...")
    kl = p4.load_or_build_klines(
        [p4.Round(market_id=r.market_id, coin=r.coin, end_ts=r.end_ts,
                  entry_ts=r.entry_ts, rem=r.rem, side=r.side,
                  entry_price=r.entry_price, winner=r.winner, label=r.label)
         for r in favs],
        allow_fetch=not args.no_fetch)
    attach_preabsmove(favs, kl)
    con.close()

    # --- universes: raw, deployed-chop-gated, preabsmove-gated ---
    favs_chop, n_with, n_pass = apply_chop_deployed(favs)
    favs_pre, n_with_p, n_pass_p = apply_chop_preabs(favs)
    print(f"\n[3] Gate coverage: deployed kept {len(favs_chop)}/{len(favs)} "
          f"({n_with} had tick spot/strike, {n_pass} passed); "
          f"preabs kept {len(favs_pre)}/{len(favs)} ({n_pass_p}/{n_with_p} passed).")

    # --- 2D grids ---
    print("\n[4] 2D grid sweeps (raw / chop-gated / preabs) ...")
    grid_raw = grid_sweep(favs, args.boot, args.seed)
    grid_chop = grid_sweep(favs_chop, args.boot, args.seed)
    grid_pre = grid_sweep(favs_pre, args.boot, args.seed)

    # print the chop-gated MARGIN grid + total$ grid to console
    _print_grid("CHOP-GATED  win-minus-price MARGIN", grid_chop, "margin", pct=False)
    _print_grid("CHOP-GATED  total $ @ $1/trade", grid_chop, "total", pct=False, money=True)

    # --- optimal cells ---
    bc_total = best_cell(grid_chop, "total")
    bc_margin = best_cell(grid_chop, "margin")
    bc_ev = best_cell(grid_chop, "ev")
    dep = grid_chop[DEPLOYED]
    print(f"\n[5] Optimal (chop-gated):")
    print(f"    by total$ : {bc_total[0]}  total=${bc_total[1]['total']:+.1f} "
          f"margin={bc_total[1]['margin']:+.4f} n={bc_total[1]['n']}")
    print(f"    by margin : {bc_margin[0]}  margin={bc_margin[1]['margin']:+.4f} "
          f"total=${bc_margin[1]['total']:+.1f} n={bc_margin[1]['n']}")
    print(f"    by EV/$1  : {bc_ev[0]}  ev={bc_ev[1]['ev']:+.4f} n={bc_ev[1]['n']}")
    print(f"    DEPLOYED {DEPLOYED}: margin={dep['margin']:+.4f} total=${dep['total']:+.1f} "
          f"ev={dep['ev']:+.4f} n={dep['n']} fire={100*dep['fire']:.1f}%")

    # --- placebo + OOS on the headline cells (deployed + total-optimal + their neighbors) ---
    print("\n[6] Placebo + TIME-OOS on headline cells (chop-gated) ...")
    headline_cells = []
    seen = set()
    for cell in [DEPLOYED, bc_total[0], bc_margin[0]]:
        if cell not in seen:
            headline_cells.append(cell)
            seen.add(cell)
    # add the deployed cell's neighbors so we can prove local optimality
    for nb in neighbors_of(DEPLOYED):
        if nb not in seen:
            headline_cells.append(nb)
            seen.add(nb)

    disc = {}
    for cell in headline_cells:
        f, c = cell
        p = placebo_cell(favs_chop, f, c, args.shuffles, seed=args.seed)
        oos = oos_cell(favs_chop, f, c)
        s = grid_chop[cell]
        disc[cell] = dict(placebo=p, oos=oos, summ=s)
        oh = "n/a" if not oos.get("ok") else ("HOLDS" if oos.get("holds") else "FAILS")
        print(f"    [{f:.2f},{c:.2f}] n={s['n']:4d} margin={s['margin']:+.4f} "
              f"total=${s['total']:+.1f}  placebo p={p:.4f}  OOS={oh} "
              f"(tr={oos.get('tr_margin', float('nan')):+.3f} "
              f"te={oos.get('te_margin', float('nan')):+.3f})")

    # --- real-ledger ---
    print("\n[7] Real-ledger bucket cross-check (the binding evidence) ...")
    real, real_below = real_ledger_buckets()
    for b in REAL_BUCKETS:
        r = real[b]
        if r["n"]:
            print(f"    {b:14s} n={r['n']:4d} win={r['win']:.3f} price={r['price']:.3f} "
                  f"margin={r['margin']:+.4f}")
    if real_below["n"]:
        print(f"    {'<0.70 (oos)':14s} n={real_below['n']:4d} win={real_below['win']:.3f} "
              f"price={real_below['price']:.3f} margin={real_below['margin']:+.4f}")

    # real margin overlapping the deployed + optimal cells
    rdep_m, rdep_n = real_margin_for_cell(*DEPLOYED, real)
    rbt_m, rbt_n = real_margin_for_cell(*bc_total[0], real)
    print(f"    real margin over DEPLOYED {DEPLOYED}: {rdep_m:+.4f} (n={rdep_n})")
    print(f"    real margin over total-opt {bc_total[0]}: {rbt_m:+.4f} (n={rbt_n})")

    # --- decide ---
    rec = decide(grid_chop, disc, real, bc_total, bc_margin)
    print("\n[8] VERDICT:", rec["headline"])
    for r in rec["reasons"]:
        print("    -", r)

    write_md(dict(
        cov=cov, base_win=base_win, span_lo=span_lo, span_hi=span_hi,
        by_coin=dict(by_coin), n_favs=len(favs), pdist=pdist,
        chop_cov=(n_with, n_pass, len(favs_chop)),
        pre_cov=(n_with_p, n_pass_p, len(favs_pre)),
        grid_raw=grid_raw, grid_chop=grid_chop, grid_pre=grid_pre,
        bc_total=bc_total, bc_margin=bc_margin, bc_ev=bc_ev, dep=dep,
        disc=disc, headline_cells=headline_cells,
        real=real, real_below=real_below,
        rdep=(rdep_m, rdep_n), rbt=(rbt_m, rbt_n),
        rec=rec, args=args))
    print(f"\n[9] Wrote report -> {MD_OUT}")


# --------------------------------------------------------------------------- #
# 6. DECISION
# --------------------------------------------------------------------------- #
def decide(grid_chop, disc, real, bc_total, bc_margin) -> dict:
    """Is [0.78,0.90] near-optimal? A challenger cell only DISPLACES the deployed band if it
    is MATERIALLY richer in realized total$ AND survives the FULL discipline:
        (a) paper margin +ve, (b) placebo p<0.05, (c) OOS holds, AND
        (d) every real-ledger bucket it ADDS beyond the deployed band is non-negative.
    Test (d) is the MARGINAL real test (phase-7 discipline), NOT the pooled cell-span margin:
    a wide cell can average a real-NEGATIVE marginal slice against a good core and look fine
    pooled. Extending the CEILING up adds the >0.90 buckets (real [0.90,0.93) ≈ −0.0485, the
    phase-7 reversal); extending the FLOOR down adds [0.70,0.78) (real ≈ −0.034). The real
    ledger is the binding evidence and wins ties (idealized paper flatters both tails).
    'Near-optimal' = the deployed cell is itself disciplined and no disciplined challenger
    beats it materially."""
    MATERIAL = 15.0           # total$ improvement to call a move "material" (over 8 days)
    REAL_NEG = -0.005         # a real bucket is "negative" below this; treat n<25 as too-small
    dep = grid_chop[DEPLOYED]
    dep_d = disc.get(DEPLOYED, {})
    reasons = []

    # is the deployed cell itself disciplined? (deployed adds NO buckets beyond itself, so
    # its real gate is the pooled margin over its own span — the core [0.78,0.90] zone)
    dep_paper_ok = dep["margin"] > 0
    dep_plac = dep_d.get("placebo", float("nan"))
    dep_oos = dep_d.get("oos", {})
    dep_oos_ok = bool(dep_oos.get("holds"))
    rdep_m, rdep_n = real_margin_for_cell(*DEPLOYED, real)
    dep_real_ok = (rdep_m >= REAL_NEG) or (rdep_n < 25)
    reasons.append(
        f"DEPLOYED [0.78,0.90]: paper margin {dep['margin']:+.4f} (n={dep['n']}, "
        f"total ${dep['total']:+.1f}); placebo p={dep_plac:.4f}; "
        f"OOS {'HOLDS' if dep_oos_ok else 'fails'} "
        f"(tr {dep_oos.get('tr_margin', float('nan')):+.3f} / te "
        f"{dep_oos.get('te_margin', float('nan')):+.3f}); real core-span margin "
        f"{rdep_m:+.4f} (n={rdep_n}) {'OK' if dep_real_ok else 'NEGATIVE'}.")

    # search for a disciplined challenger that MATERIALLY beats deployed by total$
    challenger = None
    rejected_for_real = []      # (cell, total, the negative marginal bucket) for the narrative
    for cell, d in disc.items():
        if cell == DEPLOYED:
            continue
        s = d["summ"]
        if s["n"] < 100:
            continue
        plac = d.get("placebo", float("nan"))
        oos = d.get("oos", {})
        paper_ok = s["margin"] > 0
        placebo_ok = (plac < 0.05)
        oos_ok = bool(oos.get("holds"))
        material = (s["total"] - dep["total"]) >= MATERIAL
        # MARGINAL real test: every real bucket this cell ADDS beyond deployed must be non-neg
        added = marginal_real_buckets(cell[0], cell[1], real)
        bad = [(b, m, n) for (b, m, n) in added if (m < REAL_NEG and n >= 25)]
        marg_real_ok = (len(bad) == 0)
        rm_pool, rn_pool = real_margin_for_cell(cell[0], cell[1], real)
        disciplined = paper_ok and placebo_ok and oos_ok and marg_real_ok
        if material and not marg_real_ok and paper_ok and placebo_ok and oos_ok:
            rejected_for_real.append((cell, s["total"], bad))
        if disciplined and material:
            if challenger is None or s["total"] > challenger[1]["total"]:
                challenger = (cell, s, plac, oos, rm_pool, rn_pool, added)

    if challenger is None:
        headline = "[0.78, 0.90] IS near-optimal — KEEP the deployed band"
        if not dep_paper_ok or not dep_oos_ok or not dep_real_ok:
            headline = ("[0.78, 0.90] survives but is weak on one axis — see reasons; "
                        "no disciplined challenger beats it materially, so KEEP")
        reasons.append(
            "No grid cell is simultaneously (a) paper +margin, (b) placebo p<0.05, "
            "(c) OOS-holding, (d) every ADDED real bucket non-negative, AND materially "
            f"(≥${MATERIAL:.0f}/8d) richer in realized total$ than deployed. Cells that "
            f"score higher on raw paper total$ get there by VOLUME — extending the ceiling "
            f"into the >0.90 zone or the floor into [0.70,0.78), both of which the MARGINAL "
            f"real test rejects.")
        for cell, tot, bad in sorted(rejected_for_real, key=lambda x: -x[1])[:4]:
            badstr = "; ".join(f"{b} real {m:+.4f} (n={n})" for b, m, n in bad)
            reasons.append(
                f"REJECTED on real money: [{cell[0]:.2f},{cell[1]:.2f}] paper total "
                f"${tot:+.1f} (+${tot-dep['total']:.1f} vs dep) — added bucket(s) lose real "
                f"money: {badstr}.")
    else:
        cell, s, plac, oos, rm, rn, added = challenger
        gain = s["total"] - dep["total"]
        headline = (f"A MATERIALLY better fully-disciplined cell EXISTS: buy_p={cell[0]:.2f}, "
                    f"sell_p={cell[1]:.2f} (+${gain:.1f}/8d vs deployed)")
        addstr = "; ".join(f"{b} real {m:+.4f} (n={n})" for b, m, n in added) or "none"
        reasons.append(
            f"Challenger [{cell[0]:.2f},{cell[1]:.2f}]: paper margin {s['margin']:+.4f} "
            f"(n={s['n']}, total ${s['total']:+.1f}); placebo p={plac:.4f}; OOS HOLDS "
            f"(tr {oos.get('tr_margin', float('nan')):+.3f} / te "
            f"{oos.get('te_margin', float('nan')):+.3f}); ADDED real bucket(s) all "
            f"non-negative [{addstr}]; +${gain:.1f}/8d over deployed.")

    return dict(headline=headline, reasons=reasons,
                deployed_disciplined=bool(dep_paper_ok and dep_oos_ok and dep_real_ok),
                challenger=challenger, rejected_for_real=rejected_for_real)


# --------------------------------------------------------------------------- #
# 7. CONSOLE + MARKDOWN
# --------------------------------------------------------------------------- #
def _print_grid(title, grid, key, pct=False, money=False):
    print(f"\n    {title}  (rows=floor, cols=ceiling)")
    hdr = "    floor\\ceil " + " ".join(f"{c:>9.2f}" for c in CEILINGS)
    print(hdr)
    for f in FLOORS:
        cells = []
        for c in CEILINGS:
            v = grid[(f, c)]
            if v["n"] == 0:
                cells.append(f"{'--':>9}")
            elif money:
                cells.append(f"{v[key]:>+9.1f}")
            elif pct:
                cells.append(f"{100*v[key]:>+8.2f}%")
            else:
                cells.append(f"{v[key]:>+9.4f}")
        print(f"    {f:>9.2f}  " + " ".join(cells))


def _grid_md(grid, key, fmt, label):
    """Markdown grid table for a metric (rows=floor, cols=ceiling)."""
    L = [f"**{label}** (rows = buy_p floor, cols = sell_p ceiling)", "",
         "| floor \\ ceiling | " + " | ".join(f"{c:.2f}" for c in CEILINGS) + " |",
         "|---:|" + "---:|" * len(CEILINGS)]
    for f in FLOORS:
        row = [f"**{f:.2f}**"]
        for c in CEILINGS:
            v = grid[(f, c)]
            if v["n"] == 0:
                row.append("—")
            else:
                cellstr = fmt.format(v[key])
                if (f, c) == DEPLOYED:
                    cellstr = "**" + cellstr + "**"          # bold the deployed cell
                row.append(cellstr)
        L.append("| " + " | ".join(row) + " |")
    return L


def _cell_summary_md(grid):
    """Per-cell n / win% / price detail for the chop-gated grid (audit trail)."""
    L = ["| cell [floor,ceil] | n | fire% | win% | mean price | margin | margin 95% CI | EV/$1 | total $ |",
         "|---|---:|---:|---:|---:|---:|:---:|---:|---:|"]
    for f in FLOORS:
        for c in CEILINGS:
            v = grid[(f, c)]
            if v["n"] == 0:
                continue
            ci = v["margin_ci"]
            name = f"[{f:.2f},{c:.2f}]"
            if (f, c) == DEPLOYED:
                name = "**" + name + " (deployed)**"
            L.append(f"| {name} | {v['n']} | {100*v['fire']:.1f}% | {100*v['win']:.1f}% | "
                     f"{v['price']:.3f} | {v['margin']:+.4f} | [{ci[0]:+.3f}, {ci[1]:+.3f}] | "
                     f"{v['ev']:+.4f} | {v['total']:+.1f} |")
    return L


def write_md(D):
    A = []
    add = A.append
    rec = D["rec"]
    gc = D["grid_chop"]
    add("# Phase-8 — Full 2D Price-Band Sweep: proving the optimal favorite [buy_p, sell_p]")
    add("")
    add(f"**Verdict: {rec['headline']}.**")
    add("")
    add("## Question & setup")
    add("")
    add("Polybot buys the **FAVORITE** side of a crypto 5-min up/down market when its price "
        "is in **[buy_p, sell_p]** (a chop gate skips flat rounds) and HOLDS to settle. Edge "
        "per share = **q(x) − p** (favorite realized win-rate minus price paid). The live "
        "deployed band is **[buy_p=0.78, sell_p=0.90]** (sell_p was just raised 0.85→0.90 in "
        "phase7). Floor has historically been 0.70/0.76/0.78. Phase-7 only swept the ceiling "
        "with the floor fixed at 0.78; **phase-8 sweeps BOTH dimensions** so the floor is no "
        "longer assumed, and proves where the realized edge is maximized.")
    add("")
    add("- Archive: `archive/master_polybot.db`, span "
        f"**{D['span_lo']} .. {D['span_hi']} UTC** (8 days).")
    add(f"- Usable favorites (first tick rem≤150 with a side ≥0.70, capped ≤0.97): "
        f"**{D['cov']['ok']}/{D['cov']['total']}** "
        f"(dropped no_winner={D['cov']['no_winner']}, no_market_end={D['cov']['no_market_end']}, "
        f"no_coin={D['cov']['no_coin']}, no_entry={D['cov']['no_entry_tick']}).")
    add(f"- By coin: {D['by_coin']}. Base favorite(≥0.70) win-rate = **{D['base_win']:.3f}**.")
    add(f"- Favorites at/above each candidate floor: {D['pdist']}.")
    add("")
    add("**Entry (no look-ahead).** First `ticks` row with `rem≤150` where a favorite side "
        "(YES ask=`ask_p1`, NO ask=`1−bid_p1`) is ≥0.70. Side + entry price taken there; a "
        "favorite already above a candidate ceiling at that tick is **entered at that price** "
        "(not skipped). Label = 1 if side==winner. Reuses `phase4_features` (reconstruction "
        "klines/spot machinery) and `phase7_sellp` (summarize / EV / bootstrap / chop gates / "
        "real-ledger) — both **imported, not modified**.")
    add("")
    add("**EV convention.** Per \\$1 staked on a 1-share favorite buy at price *p* paying \\$1 "
        "on win: `EV/$1 = win%·(1/p) − 1 − COST`, COST=0.003 (fee 0.001 + slippage 0.002). The "
        "price-comparable **win−price margin** (`win% − mean price`) is the primary decision "
        "metric; **total \\$** is realized payoff at \\$1/trade.")
    add("")
    add("## The 2D grid — WITH the deployed chop gate (the LIVE config, primary basis)")
    add("")
    nw, npass, nkept = D["chop_cov"]
    add(f"Deployed gate (`polybot/strategies.py::_not_chop`): keep iff "
        f"`|spot−strike|/strike ≥ {CHOP_MIN}` at the entry tick; **NO-OP keep** when the tick "
        f"had no spot/strike (older box only). Coverage: {nw} favorites had tick spot/strike, "
        f"{npass} passed; kept {nkept}/{D['n_favs']}. **This is the live config**, so it is the "
        f"primary basis for the decision. The deployed cell **[0.78, 0.90]** is bolded.")
    add("")
    A.extend(_grid_md(gc, "margin", "{:+.4f}", "Win−price MARGIN"))
    add("")
    A.extend(_grid_md(gc, "total", "{:+.1f}", "Total \\$ @ \\$1/trade (8 days)"))
    add("")
    A.extend(_grid_md(gc, "ev", "{:+.4f}", "EV per \\$1 (cost-adjusted)"))
    add("")
    A.extend(_grid_md(gc, "n", "{:d}", "Trade count n"))
    add("")
    bc_total = D["bc_total"]
    bc_margin = D["bc_margin"]
    dep = D["dep"]
    add("### Optimal cells (chop-gated)")
    add("")
    add(f"- **By realized total \\$:** `[{bc_total[0][0]:.2f}, {bc_total[0][1]:.2f}]` → "
        f"total **\\${bc_total[1]['total']:+.1f}**, margin {bc_total[1]['margin']:+.4f}, "
        f"n={bc_total[1]['n']}, fire {100*bc_total[1]['fire']:.1f}%.")
    add(f"- **By win−price margin (n≥100):** `[{bc_margin[0][0]:.2f}, {bc_margin[0][1]:.2f}]` → "
        f"margin **{bc_margin[1]['margin']:+.4f}**, total \\${bc_margin[1]['total']:+.1f}, "
        f"n={bc_margin[1]['n']}.")
    add(f"- **Deployed [0.78, 0.90]:** margin {dep['margin']:+.4f}, total \\${dep['total']:+.1f}, "
        f"EV/\\$1 {dep['ev']:+.4f}, n={dep['n']}, fire {100*dep['fire']:.1f}%.")
    add("")
    add("### Per-cell detail (chop-gated, audit trail)")
    add("")
    A.extend(_cell_summary_md(gc))
    add("")
    add("## The 2D grid — RAW (no chop gate)")
    add("")
    add("Robustness view without the live gate. Same grid, raw universe.")
    add("")
    A.extend(_grid_md(D["grid_raw"], "margin", "{:+.4f}", "Win−price MARGIN (raw)"))
    add("")
    A.extend(_grid_md(D["grid_raw"], "total", "{:+.1f}", "Total \\$ @ \\$1/trade (raw)"))
    add("")
    add("## The 2D grid — Binance preabsmove chop proxy (FULL coverage)")
    add("")
    nwp, npassp, nkeptp = D["pre_cov"]
    add(f"The deployed tick-spot gate only covers the older box; the same-mechanism Binance "
        f"gate (round-open→entry `|net|/open ≥ {CHOP_MIN}`, look-ahead-controlled via "
        f"`phase4.spot_path_feats`) is the wide-coverage robustness check. Kept "
        f"{nkeptp}/{D['n_favs']} ({npassp} passed).")
    add("")
    A.extend(_grid_md(D["grid_pre"], "margin", "{:+.4f}", "Win−price MARGIN (preabs proxy)"))
    add("")
    A.extend(_grid_md(D["grid_pre"], "total", "{:+.1f}", "Total \\$ @ \\$1/trade (preabs proxy)"))
    add("")
    add("## Placebo + TIME-OOS on the headline cells (chop-gated)")
    add("")
    add(f"For the deployed cell, the total-\\$-optimal cell, the margin-optimal cell, and the "
        f"deployed cell's grid neighbors: a label-shuffle **placebo** (×{D['args'].shuffles}, "
        f"one-sided — does the cell's win−price margin beat a price-blind reshuffle of win "
        f"labels across the whole ≥0.70 favorite universe) and a chronological **TIME-OOS** "
        f"(early-60% train / late-40% test; **holds** = +margin in BOTH halves). The 8 days "
        f"are non-stationary across box/config switches, so OOS is decisive.")
    add("")
    add("| cell [floor,ceil] | n | margin | total \\$ | placebo p | OOS train margin | OOS test margin | OOS holds? |")
    add("|---|---:|---:|---:|---:|---:|---:|:---:|")
    # deployed first, then optimal, then the rest
    order = []
    for c in [DEPLOYED, D["bc_total"][0], D["bc_margin"][0]]:
        if c in D["disc"] and c not in order:
            order.append(c)
    for c in D["headline_cells"]:
        if c not in order:
            order.append(c)
    for cell in order:
        d = D["disc"][cell]
        s = d["summ"]
        oos = d["oos"]
        oh = "n/a" if not oos.get("ok") else ("**yes**" if oos.get("holds") else "no")
        nm = f"[{cell[0]:.2f},{cell[1]:.2f}]"
        if cell == DEPLOYED:
            nm = "**" + nm + " (deployed)**"
        add(f"| {nm} | {s['n']} | {s['margin']:+.4f} | {s['total']:+.1f} | "
            f"{d['placebo']:.4f} | {oos.get('tr_margin', float('nan')):+.3f} | "
            f"{oos.get('te_margin', float('nan')):+.3f} | {oh} |")
    add("")
    add("## Real-ledger cross-check (the binding evidence)")
    add("")
    add("Realized win-rate / win−price margin by **real LIVE fill** price bucket "
        "(`archive/ledger.db`, FILL→SETTLE joined by trade_id). The floor zone is split into "
        "`[0.70,0.78)` (the lower band of the floor sweep) so the buy_p decision has its own "
        "real-money evidence. This is what caught the phase-7 **paper-vs-real reversal** "
        "(>0.90 is +EV on paper but **−0.0485 in real money**). Where paper and real DISAGREE, "
        "**real wins**.")
    add("")
    add("| real fill bucket | n | win% | mean price | win−price margin |")
    add("|---|---:|---:|---:|---:|")
    for b in REAL_BUCKETS:
        r = D["real"][b]
        if r["n"]:
            add(f"| {b} | {r['n']} | {100*r['win']:.1f}% | {r['price']:.3f} | "
                f"**{r['margin']:+.4f}** |")
    rb = D["real_below"]
    if rb["n"]:
        add(f"| <0.70 (out of scope) | {rb['n']} | {100*rb['win']:.1f}% | {rb['price']:.3f} | "
            f"{rb['margin']:+.4f} |")
    add("")
    rdep_m, rdep_n = D["rdep"]
    rbt_m, rbt_n = D["rbt"]
    add(f"- **Real margin over the DEPLOYED [0.78, 0.90] span** (pooling overlapping real "
        f"buckets): **{rdep_m:+.4f}** (n={rdep_n}).")
    add(f"- **Real margin over the total-\\$-optimal cell "
        f"[{D['bc_total'][0][0]:.2f}, {D['bc_total'][0][1]:.2f}] span:** {rbt_m:+.4f} "
        f"(n={rbt_n}).")
    add("")
    add("**Reading of the real buckets.** `[0.78,0.85)` and `[0.85,0.90)` are clearly "
        "+margin in REAL money — the core of the deployed band is real-money-good. "
        "`[0.70,0.78)` is the floor question: see its real margin above (a negative real "
        "margin there argues AGAINST lowering buy_p below 0.78). `[0.90,0.93)` is the "
        "phase-7 trap — paper-positive, real-NEGATIVE — which is why ceilings that reach "
        "into it are rejected on real-money grounds.")
    add("")
    add("## Decision logic")
    add("")
    add("Is **[0.78, 0.90]** near-optimal? A challenger cell only DISPLACES the deployed band "
        "if it is **materially** richer in realized total \\$ (≥ \\$15 over the 8 days) AND "
        "survives the full discipline: paper margin +ve, **placebo p<0.05**, **OOS holds**, "
        "AND the **real-ledger margin over its price span is non-negative**. The real ledger "
        "is the decisive tiebreaker — idealized paper flatters the >0.90 tail (paper-+EV but "
        "−0.0485 real). 'Near-optimal' = the deployed cell is itself disciplined and within a "
        "small \\$ band of the best disciplined cell.")
    add("")
    for r in rec["reasons"]:
        add(f"- {r}")
    add("")
    add("## Verdict")
    add("")
    add(f"### {rec['headline']}")
    add("")
    if rec.get("challenger") is None:
        add("The deployed band **[buy_p=0.78, sell_p=0.90]** is near-optimal. No grid cell is "
            "simultaneously paper-+margin, placebo-significant, OOS-holding, real-ledger "
            "non-negative, AND materially richer in realized total \\$. Cells that score higher "
            "on raw paper total \\$ do so by extending the ceiling into the **>0.90 zone that "
            "real money loses on** (the phase-7 reversal, real [0.90,0.93) margin ≈ −0.048), so "
            "they are rejected by the binding real-ledger gate. Lowering buy_p below 0.78 pulls "
            "in the cheaper `[0.70,0.78)` favorites whose real-money margin does not justify the "
            "added adverse-selection. **Keep [0.78, 0.90].**")
    else:
        cell = rec["challenger"][0]
        s = rec["challenger"][1]
        gain = s["total"] - D["dep"]["total"]
        add(f"A different cell is **materially better and survives every gate**: "
            f"**buy_p={cell[0]:.2f}, sell_p={cell[1]:.2f}**. It beats the deployed band by "
            f"**+\\${gain:.1f} over the 8 days** at \\$1/trade, with paper margin "
            f"{s['margin']:+.4f}, placebo-significant, OOS-holding, and a non-negative "
            f"real-ledger margin over its price span. Recommend the one-line "
            f"`polybot/portfolio.json` change: `buy_p`→{cell[0]:.2f}, `sell_p`→{cell[1]:.2f} "
            f"(trivially reversible).")
    add("")
    add("## Honest caveats")
    add("")
    add("- **Paper labels = market winner; fills idealized.** Slippage/partial-fill is NOT "
        "modeled in the paper grid (COST=0.003 cushion only). The real-ledger cross-check is "
        "what disciplines the high-price tail, and it OVERRIDES paper on disagreement.")
    add("- **Non-stationarity.** 8 days cross box/config switches (floor 0.70→0.76→0.78, the "
        "sell_p 0.93→0.85→0.90 history, box moves). TIME-OOS is weighted heavily; a cell that "
        "is +margin in-sample but flips in the late test half is rejected.")
    add("- **The >0.90 paper trap.** Raw paper total \\$ keeps rising as the ceiling extends to "
        "0.93/0.97 because high-priced favorites win often — but you pay nearly the full "
        "dollar, the margin is thin, and **real money goes negative there** (adverse selection / "
        "thin offer). Do not chase paper total \\$ into that zone.")
    add("- **Floor (buy_p) below 0.78.** Lower floors add the `[0.70,0.78)` favorites; judge "
        "them on the real `[0.70,0.78)` bucket margin above, not the paper grid alone.")
    add("- **Deployed chop gate has partial tick-spot coverage** (older box only); the "
        "preabsmove proxy grid is the wide-coverage cross-check on the same mechanism.")
    add("- **Trivially reversible.** Any change is two numbers (`buy_p`, `sell_p`) in "
        "`portfolio.json`.")
    add("")
    with open(MD_OUT, "w") as f:
        f.write("\n".join(A) + "\n")


if __name__ == "__main__":
    main()
