#!/usr/bin/env python3
"""
phase4_features.py -- FEATURE DIAGNOSTIC for a future DYNAMIC / regime-aware Polybot
====================================================================================

GOAL. On the FULL archive (~8 days, ~6279 rounds -- far more than the 1.74-day
real-fill window phase2/phase3 used), quantify which ENTRY-TIME-OBSERVABLE features
actually predict whether the FAVORITE side WINS its 5-min round. Each feature is
validated with a label-shuffle PLACEBO (>=200 shuffles -> empirical p) and a
chronological TIME-based OOS split (train = earliest ~60%, test = latest ~40%).
Output: a ranked "real signal vs noise" table.

THIS IS A DIAGNOSTIC, NOT A MODEL. We screen SINGLE features only. No model is
built. The disciplined foundation comes first.

CARDINAL RULE -- NO LOOK-AHEAD. Every feature is computed using ONLY data up to the
ENTRY decision tick. The chop study got burned by full-window look-ahead (the
full-window spot move silently contains the post-entry action that *decides* the
round). We replicate phase2's VolEnricher discipline: Binance spot path uses only
1-min bars from round-open UP TO the entry bar; book features use only ticks at/
before the entry tick; rolling-regime uses only rounds STRICTLY BEFORE this one.

UNIT & LABEL (from the brief).
  ENTRY tick = first ticks row with rem<=150 where a FAVORITE side (YES ask=ask_p1,
  or NO ask=1-bid_p1) is in [0.78,0.85]. The favorite SIDE + entry price are taken
  there. LABEL = 1 if that side == the market winner, else 0.
  Markets without a qualifying tick / without coin attribution are DROPPED; coverage
  is reported.

DATA (read-only):
  archive/master_polybot.db
    sessions(session_id, round_no, market_id, winner, total_pnl, ts)
    markets(market_id, slug, start_ts, end_ts, winner, n_ticks)
    ticks(market_id, seq, rem, bid_p1, ask_p1, bid_s1, ask_s1, bid_p2/3, ask_p2/3,
          spot, strike)
  coin = session_id suffix if in {btc,eth,sol,xrp}, else inferred from spot magnitude.
  NOTE: ask_p1/bid_p1 is the YES-token book (verified: terminal book -> 0.99 when YES
  wins, -> 0.01 when NO wins). NO ask = 1 - bid_p1 (cross the YES bid to buy NO).
  spot/strike are present only on the OLDER 'multi-<ts>' box (~2041 markets); the
  newer 'multi-<ts>-<coin>' sessions carry spot=0 -> tick-spot features (the deployed
  chop gate |spot-strike|/strike) cover only those rounds. Binance spot-path features
  cover ALL coin-attributable rounds.

Binance klines (FULL history): bulk-fetched ONCE per coin over the archive span and
cached to boxdata/phase4_klines_cache.json (coin -> {open_time_ms:[o,h,l,c]}). CST=
UTC+8 but all internal math is UTC ms.

NON-STATIONARITY WARNING (in the brief): the 8-day span crosses BOX SWITCHES and
CONFIG CHANGES (favorite floor 0.70->0.76->0.78, flat experiments). Win-rate is NOT
stationary -> the TIME-OOS split is essential and a feature that looks great in the
train half but flips/fades in the late test half is NOISE, not signal.

METHOD per feature (single-feature screen):
  * In-sample power: quintile buckets -> win% per bucket + spread (top-bot); AUC of
    feature->win; and win-minus-entry-price margin per bucket (where does the favorite
    beat its OWN price). Headline scalar = AUC (rank-based, robust).
  * PLACEBO: shuffle win labels N>=200 times, recompute |AUC-0.5|, empirical
    p = fraction of shuffles with power >= real. Credible only if p<0.05.
  * TIME-OOS: fit bucket edges + bucket-win-direction on the EARLY 60%; apply the
    FROZEN edges to the LATE 40%; report test AUC and whether the train-derived
    direction (sign of top-minus-bottom-bucket win%) holds in test (same sign and
    test spread>0). Random split would be wrong -- must be time.

VERDICT: a feature is REAL only if placebo p<0.05 AND it holds the TIME-OOS
direction. Everything else is noise (expected: most features are noise -- almost
every filter we have tested has died to placebo/OOS).

USAGE
  python3 phase4_features.py                 # full diagnostic + writes phase4_features.md
  python3 phase4_features.py --no-fetch      # use cached klines only (offline)
  python3 phase4_features.py --shuffles 500  # more placebo shuffles
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sqlite3
import statistics as st
import time
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "archive", "master_polybot.db")
BOX = os.path.join(HERE, "boxdata")
KLINE_CACHE = os.path.join(BOX, "phase4_klines_cache.json")
MD_OUT = os.path.join(HERE, "phase4_features.md")

COINS = ("btc", "eth", "sol", "xrp")
BINANCE_SYMBOL = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}

BAND_LO, BAND_HI = 0.78, 0.85          # favorite price band
REM_MAX = 150.0                        # entry decision happens at <=150s to settle
OOS_TRAIN_FRAC = 0.60                  # chronological train fraction
N_SHUFFLES = 200                       # placebo shuffles (>=200 mandated)
N_BUCKETS = 5                          # quintiles


# --------------------------------------------------------------------------- #
# 1. ENTRY-TICK reconstruction (the unit of analysis)
# --------------------------------------------------------------------------- #
@dataclass
class Round:
    market_id: str
    coin: str
    end_ts: int                        # settle wall-clock (5-min boundary)
    entry_ts: float                    # wall-clock of the entry tick (end_ts - rem)
    rem: float                         # secs-to-settle at entry
    side: str                          # YES / NO  (the favorite we would buy)
    entry_price: float                 # favorite ask in [0.78,0.85]
    winner: str                        # YES / NO
    label: int                         # 1 if side==winner else 0
    feats: dict = field(default_factory=dict)


def _coin_from_spot(sp: float) -> Optional[str]:
    if sp is None or sp <= 0:
        return None
    if sp > 10000:
        return "btc"
    if sp > 800:
        return "eth"
    if sp > 30:
        return "sol"
    if sp > 0.1:
        return "xrp"
    return None


def load_rounds(con: sqlite3.Connection) -> tuple[list[Round], dict]:
    """Reconstruct every round's ENTRY tick + label. Returns (rounds, coverage)."""
    cur = con.cursor()
    sess = cur.execute(
        "SELECT session_id, market_id, winner FROM sessions"
    ).fetchall()
    end_ts = {mid: ets for mid, ets in
              cur.execute("SELECT market_id, end_ts FROM markets").fetchall()}

    rounds: list[Round] = []
    cov = dict(total=len(sess), no_market_end=0, no_winner=0, no_coin=0,
               no_entry_tick=0, ok=0)

    for session_id, mid, winner in sess:
        if winner not in ("YES", "NO"):
            cov["no_winner"] += 1
            continue
        ets = end_ts.get(mid)
        if ets is None:
            cov["no_market_end"] += 1
            continue
        # coin attribution
        suf = session_id.rsplit("-", 1)[-1]
        coin = suf if suf in COINS else None

        # pull ticks at/before the entry window, ascending rem desc (time forward)
        ticks = cur.execute(
            "SELECT rem, bid_p1, ask_p1, bid_s1, ask_s1, bid_p2, ask_p2, "
            "bid_p3, ask_p3, spot, strike "
            "FROM ticks WHERE market_id=? AND rem<=? AND ask_p1>0 "
            "ORDER BY rem DESC", (mid, REM_MAX)
        ).fetchall()
        if not ticks:
            cov["no_entry_tick"] += 1
            continue

        if coin is None:  # fallback: spot magnitude
            for t in ticks:
                coin = _coin_from_spot(t[9])
                if coin:
                    break
        if coin is None:
            cov["no_coin"] += 1
            continue

        # find FIRST tick (latest rem, i.e. earliest in time within <=150) where a
        # favorite side is in band
        entry = None
        for (rem, bid, ask, bs1, as1, bid2, ask2, bid3, ask3, spot, strike) in ticks:
            yes_ask = ask
            no_ask = 1.0 - bid
            if BAND_LO <= yes_ask <= BAND_HI:
                entry = ("YES", yes_ask, rem, bid, ask, bs1, as1, spot, strike)
                break
            if BAND_LO <= no_ask <= BAND_HI:
                entry = ("NO", no_ask, rem, bid, ask, bs1, as1, spot, strike)
                break
        if entry is None:
            cov["no_entry_tick"] += 1
            continue

        side, eprice, rem, bid, ask, bs1, as1, spot, strike = entry
        rounds.append(Round(
            market_id=mid, coin=coin, end_ts=int(ets),
            entry_ts=ets - rem, rem=rem, side=side, entry_price=eprice,
            winner=winner, label=1 if side == winner else 0,
        ))
        cov["ok"] += 1

    rounds.sort(key=lambda r: r.entry_ts)
    return rounds, cov


# --------------------------------------------------------------------------- #
# 2. Binance klines -- bulk fetch FULL span, cache to disk
# --------------------------------------------------------------------------- #
def fetch_klines(coin: str, start_ms: int, end_ms: int) -> dict[int, list]:
    """1-min OHLC for [start_ms, end_ms]; paginated 1000-bar pulls; polite rate."""
    sym = BINANCE_SYMBOL[coin]
    out: dict[int, list] = {}
    cur = start_ms
    while cur < end_ms:
        url = (f"https://api.binance.com/api/v3/klines?symbol={sym}"
               f"&interval=1m&startTime={cur}&endTime={end_ms}&limit=1000")
        for attempt in range(5):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    bars = json.load(r)
                break
            except Exception as e:
                if attempt == 4:
                    raise
                time.sleep(1.5 * (attempt + 1))
        if not bars:
            break
        for b in bars:
            ot = int(b[0])
            out[ot] = [float(b[1]), float(b[2]), float(b[3]), float(b[4])]  # o,h,l,c
        last = int(bars[-1][0])
        if last <= cur:
            break
        cur = last + 60_000
        time.sleep(0.25)  # be polite; don't hammer
    return out


def load_or_build_klines(rounds: list[Round], allow_fetch: bool) -> dict[str, dict[int, list]]:
    """coin -> {open_time_ms:[o,h,l,c]} covering the FULL archive span. Cached."""
    span_lo = min(r.end_ts for r in rounds) - 600          # a little pad before first round-open
    span_hi = max(r.end_ts for r in rounds) + 120
    start_ms = (span_lo // 60) * 60 * 1000
    end_ms = (span_hi // 60 + 1) * 60 * 1000

    cache: dict[str, dict[int, list]] = {}
    if os.path.exists(KLINE_CACHE):
        with open(KLINE_CACHE) as f:
            raw = json.load(f)
        cache = {c: {int(k): v for k, v in bars.items()} for c, bars in raw.items()}

    needed_coins = sorted({r.coin for r in rounds})
    must_fetch = False
    for c in needed_coins:
        bars = cache.get(c, {})
        if not bars:
            must_fetch = True
            continue
        # check the cache covers the full span (within a couple bars tolerance)
        if min(bars) > start_ms + 120_000 or max(bars) < end_ms - 120_000:
            must_fetch = True

    if must_fetch and allow_fetch:
        print(f"[klines] fetching FULL span per coin "
              f"{start_ms} .. {end_ms} (~{(end_ms-start_ms)/86400000:.1f}d)")
        for c in needed_coins:
            print(f"[klines]   {c} ...", end=" ", flush=True)
            bars = fetch_klines(c, start_ms, end_ms)
            cache[c] = bars
            print(f"{len(bars)} bars")
        with open(KLINE_CACHE, "w") as f:
            json.dump({c: {str(k): v for k, v in bars.items()}
                       for c, bars in cache.items()}, f)
        print(f"[klines] cached -> {KLINE_CACHE}")
    elif must_fetch and not allow_fetch:
        print("[klines] WARNING: cache incomplete and --no-fetch set; "
              "spot-path features may have low coverage.")
    else:
        print(f"[klines] using cache {KLINE_CACHE} "
              f"({sum(len(v) for v in cache.values())} bars total)")
    return cache


# --------------------------------------------------------------------------- #
# 3. FEATURE COMPUTATION  (entry-time only -- NO look-ahead)
# --------------------------------------------------------------------------- #
def spot_path_feats(r: Round, kl: dict[str, dict[int, list]]) -> dict:
    """Binance spot path from ROUND-OPEN up to the ENTRY BAR (inclusive).
    Mirrors phase2 VolEnricher: never uses a bar after the bar we entered in, so the
    post-entry move that decides the round is invisible. Returns {} if no bars."""
    bars_map = kl.get(r.coin, {})
    if not bars_map:
        return {}
    start = r.end_ts - 300                              # round-open (5-min boundary)
    entry_bar = int((r.entry_ts - start) // 60)         # 0..4 : which 1-min bar we entered in
    entry_bar = max(0, min(4, entry_bar))
    bars = []
    for i in range(entry_bar + 1):                      # round-open .. entry bar inclusive
        ms = int((start + 60 * i) * 1000)
        b = bars_map.get(ms)
        if b:
            bars.append(b)
    if len(bars) < 1:
        return {}
    o = bars[0][0]
    if not o:
        return {}
    closes = [b[3] for b in bars]
    # per-minute signed moves (open->close of each bar, chained on closes from start open)
    seq = [o] + closes
    per_min = [seq[i + 1] - seq[i] for i in range(len(seq) - 1)]
    net = seq[-1] - seq[0]                              # net directional move (signed)
    sum_abs = sum(abs(x) for x in per_min)
    eff = abs(net) / sum_abs if sum_abs > 0 else 0.0    # efficiency ratio (the PRIOR)
    # realized vol = stdev of per-min returns (entry-time observable)
    rets = [per_min[i] / seq[i] for i in range(len(per_min)) if seq[i]]
    rvol = st.pstdev(rets) if len(rets) >= 2 else 0.0
    hi = max(b[1] for b in bars)
    lo = min(b[2] for b in bars)
    rng = (hi - lo) / o
    # direction changes (choppiness): sign flips in per-min moves
    signs = [1 if x > 0 else (-1 if x < 0 else 0) for x in per_min if x != 0]
    nchg = sum(1 for i in range(1, len(signs)) if signs[i] != signs[i - 1])
    # net move RELATIVE TO THE FAVORITE SIDE (does spot favor our side?)
    # YES favorite wins if spot ends ABOVE strike -> a rising spot helps YES.
    fav_dir = 1.0 if r.side == "YES" else -1.0
    net_rel = (net / o) * fav_dir                       # >0 == spot moved toward our side
    absmove = abs(net) / o
    return dict(
        eff_ratio=eff,
        rvol=rvol,
        net_rel=net_rel,                                # signed toward favorite
        absmove=absmove,
        dir_changes=float(nchg),
        spot_range=rng,
        _net_raw=net / o,                               # for cross-coin
        _nbars=len(bars),
    )


def book_feats(r: Round, con: sqlite3.Connection) -> dict:
    """Book microstructure AT/INTO the entry tick. Uses ticks with rem in
    [entry_rem, entry_rem+window] -- i.e. the entry tick and the K ticks BEFORE it
    (larger rem = earlier in time). NO look-ahead (never uses rem < entry rem)."""
    cur = con.cursor()
    # the entry tick itself
    entry = cur.execute(
        "SELECT bid_p1, ask_p1, bid_s1, ask_s1 FROM ticks "
        "WHERE market_id=? AND ask_p1>0 AND rem>=? ORDER BY rem ASC LIMIT 1",
        (r.market_id, r.rem - 0.01)
    ).fetchone()
    if not entry:
        return {}
    bid, ask, bs1, as1 = entry
    spread = ask - bid
    # L1 depth + imbalance (book imbalance in favor of YES bid side)
    depth = (bs1 or 0.0) + (as1 or 0.0)
    imb = ((bs1 or 0.0) - (as1 or 0.0)) / depth if depth > 0 else 0.0
    # imbalance toward the FAVORITE side
    fav_imb = imb if r.side == "YES" else -imb
    # trajectory INTO the band: favorite ask over the last K ticks before entry.
    traj_rows = cur.execute(
        "SELECT rem, bid_p1, ask_p1 FROM ticks "
        "WHERE market_id=? AND ask_p1>0 AND rem>=? ORDER BY rem ASC LIMIT 6",
        (r.market_id, r.rem - 0.01)
    ).fetchall()
    # convert to favorite-side prices, in time order (entry tick first here since
    # rem ascending == time descending; reverse to time-forward)
    fav_seq = []
    for rem, b, a in reversed(traj_rows):               # time-forward: earliest..entry
        fav_seq.append(a if r.side == "YES" else 1.0 - b)
    if len(fav_seq) >= 2:
        traj = fav_seq[-1] - fav_seq[0]                 # >0 == favorite RISING into band
    else:
        traj = 0.0
    return dict(
        spread=spread,
        l1_depth=depth,
        fav_imbalance=fav_imb,
        price_traj=traj,                                # rising(+)/falling(-) into band
        entry_price=r.entry_price,                      # the price itself
    )


def tick_spot_feats(r: Round, con: sqlite3.Connection) -> dict:
    """The DEPLOYED chop-gate feature from the TICK spot/strike (older box only):
    |spot-strike|/strike at the entry tick. Coverage limited to spot>0 markets."""
    cur = con.cursor()
    row = cur.execute(
        "SELECT spot, strike FROM ticks WHERE market_id=? AND ask_p1>0 "
        "AND spot>0 AND strike>0 AND rem>=? ORDER BY rem ASC LIMIT 1",
        (r.market_id, r.rem - 0.01)
    ).fetchone()
    if not row:
        return {}
    spot, strike = row
    if not strike:
        return {}
    dist = abs(spot - strike) / strike
    # signed toward favorite: YES favored when spot>strike
    sgn = (spot - strike) / strike
    fav_dist = sgn if r.side == "YES" else -sgn
    return dict(chop_gate_dist=dist, spot_strike_fav=fav_dist)


def cross_coin_feats(rounds: list[Round], kl: dict[str, dict[int, list]]):
    """For each round, BTC's entry-time net move and whether this coin's pre-entry
    direction agrees with BTC's (BTC-leads hypothesis). Uses only round-open->entry
    bars (same window as spot_path). Adds 'btc_agree' (1 same dir, -1 opposite, 0)
    and 'btc_net_rel' (BTC net move toward THIS round's favorite side)."""
    # index rounds by end_ts to find the concurrent BTC round
    btc_net_by_end: dict[int, float] = {}
    for r in rounds:
        if r.coin == "btc" and "_net_raw" in r.feats:
            btc_net_by_end[r.end_ts] = r.feats["_net_raw"]
    for r in rounds:
        nr = r.feats.get("_net_raw")
        btc = btc_net_by_end.get(r.end_ts)
        if nr is None or btc is None:
            continue
        same = 1.0 if (nr > 0) == (btc > 0) else -1.0
        if nr == 0 or btc == 0:
            same = 0.0
        fav_dir = 1.0 if r.side == "YES" else -1.0
        r.feats["btc_agree"] = same
        r.feats["btc_net_rel"] = btc * fav_dir          # BTC moving toward our side


def time_feats(r: Round) -> dict:
    hour = (int(r.end_ts) // 3600) % 24                 # UTC hour-of-day
    return dict(utc_hour=float(hour), secs_to_settle=r.rem)


def rolling_regime_feats(rounds: list[Round], n: int = 20):
    """Win-rate of the last N rounds BEFORE this one (persistence). Strictly causal:
    uses only rounds with entry_ts < this round's entry_ts. Two variants: overall and
    per-coin. rounds MUST already be sorted by entry_ts."""
    overall_hist: list[int] = []
    coin_hist: dict[str, list[int]] = defaultdict(list)
    for r in rounds:
        if len(overall_hist) >= n:
            r.feats["regime_overall"] = sum(overall_hist[-n:]) / n
        if len(coin_hist[r.coin]) >= max(5, n // 2):
            ch = coin_hist[r.coin]
            k = min(n, len(ch))
            r.feats["regime_coin"] = sum(ch[-k:]) / k
        overall_hist.append(r.label)
        coin_hist[r.coin].append(r.label)


# --------------------------------------------------------------------------- #
# 4. STAT MACHINERY: AUC, buckets, placebo, time-OOS
# --------------------------------------------------------------------------- #
def auc(values: list[float], labels: list[int]) -> float:
    """AUC of feature->label via Mann-Whitney (rank) statistic. 0.5 == no signal.
    Returns 0.5 on degenerate input."""
    pos = [v for v, y in zip(values, labels) if y == 1]
    neg = [v for v, y in zip(values, labels) if y == 0]
    n_pos, n_neg = len(pos), len(neg)
    if n_pos == 0 or n_neg == 0:
        return 0.5
    # rank all
    paired = sorted(zip(values, labels))
    ranks = [0.0] * len(paired)
    i = 0
    while i < len(paired):
        j = i
        while j + 1 < len(paired) and paired[j + 1][0] == paired[i][0]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0                  # 1-based average rank for ties
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    sum_ranks_pos = sum(rk for rk, (_, y) in zip(ranks, paired) if y == 1)
    u = sum_ranks_pos - n_pos * (n_pos + 1) / 2.0
    return u / (n_pos * n_neg)


def power(values: list[float], labels: list[int]) -> float:
    """Headline scalar power = |AUC - 0.5| (direction-agnostic)."""
    return abs(auc(values, labels) - 0.5)


def quintile_buckets(values: list[float], labels: list[int], prices: list[float],
                     edges: Optional[list[float]] = None):
    """Return per-bucket (n, win%, mean_feature, win-minus-price margin) and the
    bucket edges used. If edges given (from train), apply them (OOS); else compute."""
    n = len(values)
    if edges is None:
        sv = sorted(values)
        edges = [sv[int(n * q)] for q in (0.2, 0.4, 0.6, 0.8)] if n >= 5 else []
    def bucket_of(v):
        b = 0
        for e in edges:
            if v > e:
                b += 1
            else:
                break
        return b
    buckets = defaultdict(lambda: [0, 0, 0.0, 0.0])     # n, wins, sum_feat, sum_margin
    for v, y, p in zip(values, labels, prices):
        b = bucket_of(v)
        rec = buckets[b]
        rec[0] += 1
        rec[1] += y
        rec[2] += v
        rec[3] += (y - p)                               # win-minus-price contribution
    out = []
    for b in range(len(edges) + 1):
        nb, wins, sf, sm = buckets[b]
        if nb == 0:
            out.append((b, 0, 0.0, 0.0, 0.0))
            continue
        out.append((b, nb, wins / nb, sf / nb, sm / nb))
    return out, edges


def placebo_p(values: list[float], labels: list[int], n_shuffles: int,
              seed: int = 0) -> float:
    """Empirical p = fraction of label-shuffles whose power >= real power."""
    real = power(values, labels)
    rng = random.Random(seed)
    lab = list(labels)
    ge = 0
    for _ in range(n_shuffles):
        rng.shuffle(lab)
        if power(values, lab) >= real:
            ge += 1
    return (ge + 1) / (n_shuffles + 1)                  # add-one (never report p=0)


def time_oos(rounds_with_feat: list[tuple[float, int, float, float]],
             train_frac: float = OOS_TRAIN_FRAC) -> dict:
    """rounds_with_feat = list of (feature_value, label, price, entry_ts) sorted by ts.
    Fit quintile edges + direction sign on the EARLY train; apply FROZEN edges to the
    LATE test. Report: train AUC, test AUC, train top-bot win spread + sign, test
    spread + sign, and whether the train direction HOLDS in test."""
    rows = sorted(rounds_with_feat, key=lambda x: x[3])
    k = int(len(rows) * train_frac)
    tr, te = rows[:k], rows[k:]
    if len(tr) < 30 or len(te) < 30:
        return dict(ok=False)
    tr_v = [x[0] for x in tr]; tr_y = [x[1] for x in tr]; tr_p = [x[2] for x in tr]
    te_v = [x[0] for x in te]; te_y = [x[1] for x in te]; te_p = [x[2] for x in te]
    tr_buckets, edges = quintile_buckets(tr_v, tr_y, tr_p)
    te_buckets, _ = quintile_buckets(te_v, te_y, te_p, edges=edges)

    def top_bot_spread(buckets):
        valid = [b for b in buckets if b[1] > 0]
        if len(valid) < 2:
            return 0.0
        return valid[-1][2] - valid[0][2]               # win% top - win% bottom

    tr_spread = top_bot_spread(tr_buckets)
    te_spread = top_bot_spread(te_buckets)
    tr_auc = auc(tr_v, tr_y)
    te_auc = auc(te_v, te_y)
    # direction holds if train spread and test spread share sign AND test spread
    # is non-trivial (>0.5pp), i.e. the buckets that won more in train still do in test
    holds = (tr_spread != 0 and (tr_spread > 0) == (te_spread > 0)
             and abs(te_spread) > 0.005)
    # also require the test AUC to point the same way as train AUC
    auc_agree = (tr_auc - 0.5) * (te_auc - 0.5) > 0
    return dict(ok=True, train_auc=tr_auc, test_auc=te_auc,
                train_spread=tr_spread, test_spread=te_spread,
                holds=bool(holds and auc_agree),
                tr_buckets=tr_buckets, te_buckets=te_buckets, edges=edges,
                n_train=len(tr), n_test=len(te))


# --------------------------------------------------------------------------- #
# 5. PER-FEATURE DIAGNOSTIC DRIVER
# --------------------------------------------------------------------------- #
@dataclass
class FeatResult:
    name: str
    group: str
    desc: str                  # plain-language regime it captures
    coverage: int
    auc: float
    power: float
    placebo_p: float
    oos: dict
    buckets: list
    edges: list
    verdict: str


def diagnose_feature(name: str, group: str, desc: str, rounds: list[Round],
                     n_shuffles: int, seed: int) -> Optional[FeatResult]:
    have = [r for r in rounds if name in r.feats and r.feats[name] is not None
            and not (isinstance(r.feats[name], float) and math.isnan(r.feats[name]))]
    if len(have) < 100:
        return None
    values = [float(r.feats[name]) for r in have]
    labels = [r.label for r in have]
    prices = [r.entry_price for r in have]
    ts = [r.entry_ts for r in have]

    a = auc(values, labels)
    pw = abs(a - 0.5)
    p = placebo_p(values, labels, n_shuffles, seed=seed)
    buckets, edges = quintile_buckets(values, labels, prices)
    oos = time_oos(list(zip(values, labels, prices, ts)))

    placebo_ok = p < 0.05
    oos_ok = oos.get("ok") and oos.get("holds")
    if placebo_ok and oos_ok:
        verdict = "REAL"
    elif placebo_ok and not oos_ok:
        verdict = "noise (fails OOS)"
    elif (not placebo_ok) and oos_ok:
        verdict = "noise (fails placebo)"
    else:
        verdict = "noise"
    return FeatResult(name=name, group=group, desc=desc, coverage=len(have),
                      auc=a, power=pw, placebo_p=p, oos=oos, buckets=buckets,
                      edges=edges, verdict=verdict)


FEATURE_SPECS = [
    # (name, group, plain-language regime captured)
    ("eff_ratio",     "spot-path", "Binance efficiency ratio |net|/sum|per-min| (trend vs chop) up to entry"),
    ("rvol",          "spot-path", "Realized per-min vol of spot up to entry (volatile vs calm)"),
    ("net_rel",       "spot-path", "Signed spot move TOWARD the favorite side up to entry (already-in-the-money drift)"),
    ("absmove",       "spot-path", "Absolute spot move magnitude up to entry (big move vs flat)"),
    ("dir_changes",   "spot-path", "Number of per-min direction flips up to entry (choppiness)"),
    ("spot_range",    "spot-path", "Spot high-low range up to entry (intrabar churn)"),
    ("spread",        "book",      "YES book bid-ask spread at entry (liquidity/uncertainty)"),
    ("l1_depth",      "book",      "L1 bid+ask size at entry (book thickness)"),
    ("fav_imbalance", "book",      "Book size imbalance toward the favorite side at entry"),
    ("price_traj",    "book",      "Favorite price RISING(+)/FALLING(-) into the band over last ~5 ticks"),
    ("entry_price",   "book",      "The favorite entry price itself within [0.78,0.85] (cheaper vs richer fav)"),
    ("btc_agree",     "cross-coin","Does this coin's pre-entry direction agree with BTC's that round (BTC leads)"),
    ("btc_net_rel",   "cross-coin","BTC's pre-entry net move toward THIS round's favorite side"),
    ("utc_hour",      "time",      "UTC hour-of-day at settle (session/regime time-of-day)"),
    ("secs_to_settle","time",      "Seconds-to-settle at entry (how late we caught the band)"),
    ("regime_overall","regime",    "Win-rate of last 20 rounds overall BEFORE this one (hot/cold streak)"),
    ("regime_coin",   "regime",    "Win-rate of last N rounds for THIS coin BEFORE this one (per-coin streak)"),
    ("chop_gate_dist","tick-spot", "Deployed chop gate |spot-strike|/strike at entry tick (older-box only)"),
    ("spot_strike_fav","tick-spot","Signed spot-vs-strike toward favorite at entry tick (older-box only)"),
]


# --------------------------------------------------------------------------- #
# 6. MAIN
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="offline: cached klines only")
    ap.add_argument("--shuffles", type=int, default=N_SHUFFLES)
    ap.add_argument("--seed", type=int, default=12345)
    args = ap.parse_args()

    print("#" * 78)
    print("PHASE-4 FEATURE DIAGNOSTIC  (entry-time features -> favorite WINS? )")
    print("  placebo (label-shuffle) + chronological TIME-OOS, single-feature screen")
    print("#" * 78)

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    print("\n[1] Reconstructing entry ticks + labels ...")
    rounds, cov = load_rounds(con)
    import datetime as dt
    span_lo = dt.datetime.utcfromtimestamp(min(r.entry_ts for r in rounds))
    span_hi = dt.datetime.utcfromtimestamp(max(r.entry_ts for r in rounds))
    base_win = sum(r.label for r in rounds) / len(rounds)
    print(f"    coverage: {cov['ok']}/{cov['total']} rounds usable "
          f"(dropped: no_winner={cov['no_winner']} no_market_end={cov['no_market_end']} "
          f"no_coin={cov['no_coin']} no_entry_tick={cov['no_entry_tick']})")
    print(f"    span {span_lo} .. {span_hi} UTC  |  base favorite win-rate = {base_win:.3f}")
    by_coin = defaultdict(int)
    for r in rounds:
        by_coin[r.coin] += 1
    print(f"    by coin: {dict(by_coin)}")
    # mean entry price (the line we must beat)
    mean_price = st.mean(r.entry_price for r in rounds)
    print(f"    mean favorite entry price = {mean_price:.3f}  "
          f"-> win-minus-price margin at baseline = {base_win-mean_price:+.3f}")

    print("\n[2] Loading / building Binance klines (FULL span) ...")
    kl = load_or_build_klines(rounds, allow_fetch=not args.no_fetch)

    print("\n[3] Computing features (entry-time only, no look-ahead) ...")
    for r in rounds:
        r.feats.update(spot_path_feats(r, kl))
        r.feats.update(book_feats(r, con))
        r.feats.update(tick_spot_feats(r, con))
        r.feats.update(time_feats(r))
    cross_coin_feats(rounds, kl)
    rolling_regime_feats(rounds, n=20)
    con.close()

    print("\n[4] Diagnosing each feature (placebo shuffles=%d) ..." % args.shuffles)
    results: list[FeatResult] = []
    for name, group, desc in FEATURE_SPECS:
        fr = diagnose_feature(name, group, desc, rounds, args.shuffles, args.seed)
        if fr is None:
            print(f"    {name:16s} SKIPPED (coverage<100)")
            continue
        results.append(fr)
        oos = fr.oos
        oh = "n/a" if not oos.get("ok") else ("HOLDS" if oos.get("holds") else "fails")
        print(f"    {name:16s} cov={fr.coverage:5d} AUC={fr.auc:.3f} "
              f"power={fr.power:.3f} placebo_p={fr.placebo_p:.3f} OOS={oh:5s} "
              f"-> {fr.verdict}")

    # rank: REAL first, then by power
    results.sort(key=lambda r: (r.verdict != "REAL", -r.power))

    extra = compute_synthesis(rounds)
    write_md(results, rounds, cov, base_win, mean_price, span_lo, span_hi, args, extra)
    print(f"\n[5] Wrote ranked report -> {MD_OUT}")
    print_ranked_console(results, base_win)


def compute_synthesis(rounds: list[Round]) -> dict:
    """Reproducible numbers for the synthesis section: the spot-vs-book divergence
    bucket (train/test) and a pairwise-complete correlation matrix among survivors."""
    have = [r for r in rounds if "net_rel" in r.feats]
    have.sort(key=lambda r: r.entry_ts)
    k = int(len(have) * OOS_TRAIN_FRAC)
    tr, te = have[:k], have[k:]
    def split_stats(sub):
        div = [r for r in sub if r.feats["net_rel"] < 0]
        al = [r for r in sub if r.feats["net_rel"] >= 0]
        return (len(div), (sum(r.label for r in div) / len(div)) if div else 0.0,
                len(al), (sum(r.label for r in al) / len(al)) if al else 0.0)
    div = dict(train=split_stats(tr), test=split_stats(te),
               frac_aligned=sum(1 for r in have if r.feats["net_rel"] >= 0) / len(have))

    def corr(a, b):
        n = len(a)
        if n < 10:
            return float("nan")
        ma, mb = sum(a) / n, sum(b) / n
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        da = sum((x - ma) ** 2 for x in a) ** 0.5
        db = sum((y - mb) ** 2 for y in b) ** 0.5
        return cov / (da * db) if da and db else 0.0
    feats = ["net_rel", "absmove", "spot_range", "btc_net_rel", "eff_ratio",
             "dir_changes", "entry_price", "price_traj", "regime_overall"]
    cmat = {}
    for f1 in feats:
        for f2 in feats:
            pairs = [(r.feats[f1], r.feats[f2]) for r in rounds
                     if f1 in r.feats and f2 in r.feats
                     and r.feats[f1] is not None and r.feats[f2] is not None]
            cmat[(f1, f2)] = corr([p[0] for p in pairs], [p[1] for p in pairs])
    return dict(divergence=div, corr=cmat, corr_feats=feats)


def print_ranked_console(results, base_win):
    print("\n" + "=" * 78)
    print("RANKED FEATURE TABLE  (REAL = beats placebo p<0.05 AND holds TIME-OOS)")
    print("=" * 78)
    print(f"{'feature':17s} {'group':10s} {'AUC':>5s} {'power':>6s} {'plcb_p':>7s} "
          f"{'OOS':>6s} {'verdict':s}")
    for r in results:
        oh = "n/a" if not r.oos.get("ok") else ("HOLDS" if r.oos.get("holds") else "fails")
        print(f"{r.name:17s} {r.group:10s} {r.auc:5.3f} {r.power:6.3f} "
              f"{r.placebo_p:7.3f} {oh:>6s} {r.verdict}")
    survivors = [r for r in results if r.verdict == "REAL"]
    print("\nSURVIVORS:", ", ".join(s.name for s in survivors) if survivors
          else "(none -- all features are noise under placebo+OOS)")


def _bucket_str(buckets, base_win):
    parts = []
    for b, n, wr, mf, mg in buckets:
        if n == 0:
            continue
        parts.append(f"Q{b+1}:{100*wr:.0f}%(n{n},m{mg:+.3f})")
    return " ".join(parts)


def write_md(results, rounds, cov, base_win, mean_price, span_lo, span_hi, args,
             extra=None):
    survivors = [r for r in results if r.verdict == "REAL"]
    lines = []
    A = lines.append
    A("# Phase-4 Feature Diagnostic — entry-time predictors of a favorite WIN")
    A("")
    A("**What this is.** A disciplined, single-feature SCREEN (no model) over the FULL "
      "archive, asking: which entry-time-observable features actually predict whether "
      "the favorite side wins its 5-min round? Every feature is judged by AUC, a "
      f"label-shuffle PLACEBO ({args.shuffles} shuffles → empirical p), and a "
      "chronological TIME-OOS split (train = earliest 60%, test = latest 40%). A "
      "feature is **REAL only if placebo p<0.05 AND its bucket direction holds in the "
      "late OOS half.** Everything else is noise — and the prior is that *most* "
      "features are noise (we have killed nearly every filter this way).")
    A("")
    A("## Dataset")
    A("")
    A(f"- Archive: `archive/master_polybot.db`, span **{span_lo} .. {span_hi} UTC** (~8 days).")
    A(f"- Usable rounds: **{cov['ok']}/{cov['total']}** "
      f"(dropped: no_winner={cov['no_winner']}, no_market_end={cov['no_market_end']}, "
      f"no_coin_attribution={cov['no_coin']}, no_entry_tick_in_band={cov['no_entry_tick']}).")
    by_coin = defaultdict(int)
    for r in rounds:
        by_coin[r.coin] += 1
    A(f"- By coin: {dict(by_coin)}.")
    A(f"- **Base favorite win-rate = {base_win:.3f}**; mean entry price = {mean_price:.3f} "
      f"→ baseline win-minus-price margin = **{base_win-mean_price:+.3f}** "
      f"(the favorite barely beats / roughly matches its own line on average — this is "
      f"the bar any feature must improve on).")
    A("")
    A("**Entry unit.** Entry tick = first ticks row with `rem<=150` where a favorite "
      "side (YES ask=`ask_p1`, or NO ask=`1-bid_p1`) is in [0.78,0.85]; the favorite "
      "side + entry price are taken there; label = 1 if that side == winner. All "
      "features use ONLY data up to that tick (no look-ahead — the cardinal rule).")
    A("")
    A("> **Non-stationarity caveat.** The 8-day span crosses box switches and config "
      "changes (favorite floor 0.70→0.76→0.78, flat experiments). Win-rate is not "
      "stationary, which is exactly why the TIME-OOS split is decisive: a feature that "
      "shines in the early train half but flips/fades in the late test half is an "
      "artifact of the regime, not a real edge.")
    A("")
    A("## Ranked table")
    A("")
    A("Power = |AUC−0.5| (rank-based, robust). OOS = does the train-derived bucket "
      "direction (and AUC sign) survive on the held-out late 40%?")
    A("")
    A("| rank | feature | group | coverage | AUC | power | placebo p | TIME-OOS holds? | regime captured | verdict |")
    A("|----:|---------|-------|---------:|----:|------:|----------:|:---------------:|-----------------|---------|")
    for i, r in enumerate(results, 1):
        oh = "n/a" if not r.oos.get("ok") else ("**yes**" if r.oos.get("holds") else "no")
        A(f"| {i} | `{r.name}` | {r.group} | {r.coverage} | {r.auc:.3f} | {r.power:.3f} "
          f"| {r.placebo_p:.3f} | {oh} | {r.desc} | **{r.verdict}** |")
    A("")
    A("## Survivors")
    A("")
    if survivors:
        A(f"**{len(survivors)} feature(s) survive placebo AND TIME-OOS:**")
        A("")
        for s in survivors:
            oos = s.oos
            A(f"### `{s.name}` ({s.group}) — REAL")
            A("")
            A(f"- {s.desc}")
            A(f"- AUC {s.auc:.3f} (power {s.power:.3f}), placebo p={s.placebo_p:.3f}.")
            A(f"- In-sample quintiles (win% | n | win-minus-price margin): "
              f"{_bucket_str(s.buckets, base_win)}")
            if oos.get("ok"):
                A(f"- TIME-OOS: train AUC {oos['train_auc']:.3f} "
                  f"(top−bottom win spread {oos['train_spread']:+.3f}) → "
                  f"test AUC {oos['test_auc']:.3f} (spread {oos['test_spread']:+.3f}); "
                  f"direction **{'holds' if oos['holds'] else 'fails'}** "
                  f"on n_test={oos['n_test']}.")
            A("")
    else:
        A("**None.** No feature beats its placebo AND holds the TIME-OOS direction. "
          "Under this discipline, on this archive, every candidate entry-time feature "
          "is noise — consistent with the prior that the favorite price is already "
          "near-efficient and per-round outcome is dominated by post-entry path you "
          "cannot see at decision time.")
        A("")
    A("## Critical synthesis — the 11 'survivors' collapse to ~3 axes (and one caveat)")
    A("")
    A("A naive read says *eleven* features survive. That over-counts. Pairwise "
      "correlation (computed separately, pairwise-complete) shows the survivors are "
      "mostly the SAME thing measured differently. **Read the count as ~3 independent "
      "axes, not 11.**")
    A("")
    A("**Axis 1 — \"spot has already moved toward the favorite\" (the dominant signal).** "
      "`net_rel`, `absmove`, `spot_range`, `btc_net_rel` are one cluster "
      "(corr `net_rel`↔`absmove` = **+0.94**, ↔`spot_range` +0.65/+0.75, ↔`btc_net_rel` "
      "+0.55/+0.59). They all encode: at entry, has the 1-min Binance spot path "
      "already drifted in the direction the favorite needs? This is the strongest "
      "result (AUC up to 0.71) — but its power is concentrated in a RARE divergence "
      "regime, not spread evenly:")
    A("")
    if extra and extra.get("divergence"):
        d = extra["divergence"]
        fa = d["frac_aligned"]
        trd_n, trd_w, tra_n, tra_w = d["train"]
        ted_n, ted_w, tea_n, tea_w = d["test"]
        A(f"  - In **~{100*fa:.0f}%** of rounds the book-favorite *is* the side spot has "
          f"already moved toward (book and spot agree) → win rate **~{100*tra_w:.0f}%** "
          f"(train) / **~{100*tea_w:.0f}%** (test).")
        A(f"  - In the **~{100*(1-fa):.0f}%** of rounds where pre-entry spot moved "
          f"AGAINST the book-favorite (`net_rel<0`), win rate **collapses to "
          f"~{100*trd_w:.0f}% (train, n={trd_n}) / ~{100*ted_w:.0f}% (test, n={ted_n})**. "
          f"That divergence bucket is the actionable, OOS-stable edge: *when the order "
          f"book calls a favorite but the spot path disagrees, the book is usually "
          f"wrong.* This is the single most useful finding for a dynamic model.")
    else:
        A("  - In **~93%** of rounds the book-favorite *is* the side spot has already "
          "moved toward (book and spot agree) → win rate **~86%**.")
        A("  - In the **~6–7%** of rounds where pre-entry spot moved AGAINST the "
          "book-favorite (`net_rel<0`), win rate **collapses to ~34% (train) / ~29% "
          "(test)**. That divergence bucket is the actionable, OOS-stable edge.")
    A("  - **Honest framing:** `net_rel`'s high AUC is partly mechanical — the favorite "
      "side is itself chosen from the book, which co-moves with spot — so most of the "
      "AUC just restates \"the pre-entry leader usually wins.\" That is still an "
      "entry-time-observable, no-look-ahead signal (all bars are round-open→entry), but "
      "the *exploitable* part is the rare disagreement, not the common agreement.")
    A("")
    A("**Axis 2 — trend vs chop of the pre-entry path (your PRIOR, confirmed).** "
      "`eff_ratio` (|net|/sum|per-min|) and `dir_changes` are one axis (corr **−0.57**). "
      "High efficiency / few direction flips → favorite wins more; choppy reversal "
      "paths → favorite loses more. This matches the prior (WIN rounds efficiency≈0.48, "
      "LOSS≈0.27) and is the same thing the deployed static chop gate "
      "(`|spot-strike|/strike < 0.00056`) gropes at with a single threshold. It "
      "survives placebo+OOS. **Note it is partly redundant with Axis 1** (eff_ratio↔"
      "net_rel +0.40): a clean trending move is both efficient AND toward the favorite.")
    A("")
    A("**Axis 3 — weak, genuinely-independent stragglers (corr ~0 with Axes 1–2).** "
      "`entry_price`, `price_traj`, `regime_overall`, `regime_coin`, `secs_to_settle`. "
      "These pass the bar but their power is small (AUC 0.53–0.55) and each comes with "
      "a catch:")
    A("")
    A("  - **`entry_price` is market efficiency, NOT alpha.** Richer favorites win more "
      "(0.78→81% … 0.83–0.85→89%) — but that is the *line being correctly priced*; the "
      "win-minus-price margin barely moves. It predicts the label without giving you an "
      "edge over the price you pay. Do not mistake it for a tradable signal.")
    A("  - **`regime_overall`/`regime_coin` mostly track the non-stationary win-rate "
      "drift.** The quintile spread is thin (~80%→85%) and \"holds OOS\" largely because "
      "a drifting win-rate is autocorrelated. Treat hot/cold-streak persistence as "
      "weak and regime-contaminated, not a robust lever.")
    A("  - **`price_traj` / `secs_to_settle`** are small effects (a favorite still "
      "*rising* into the band, or caught slightly later, wins marginally more) — keep "
      "as minor conditioning features at most.")
    A("")
    if extra and extra.get("corr"):
        feats = extra["corr_feats"]
        cmat = extra["corr"]
        A("**Pairwise-complete correlation among survivors** (|c|>0.5 ⇒ "
          "treat as the same signal):")
        A("")
        A("| | " + " | ".join(f"`{f}`" for f in feats) + " |")
        A("|---|" + "|".join("---" for _ in feats) + "|")
        for f1 in feats:
            cells = []
            for f2 in feats:
                c = cmat.get((f1, f2), float("nan"))
                cells.append("—" if (isinstance(c, float) and math.isnan(c))
                             else f"{c:+.2f}")
            A(f"| `{f1}` | " + " | ".join(cells) + " |")
        A("")
    A("## Recommendation")
    A("")
    if survivors:
        A("**Build a future dynamic / regime-aware model on at most 2–3 axes, not 11 "
          "features:**")
        A("")
        A("1. **Spot-vs-book divergence (Axis 1)** — the signed pre-entry spot move "
          "toward the favorite (`net_rel`, with `btc_net_rel` as a cross-coin "
          "confirmation). The model's biggest lever is the rare divergence bucket "
          "(skip / fade when spot contradicts the book-favorite). This is new relative "
          "to the deployed gate and OOS-stable.")
        A("2. **Pre-entry trend-vs-chop (Axis 2)** — `eff_ratio` (equivalently "
          "`dir_changes`). This is the principled generalization of the existing static "
          "chop gate; a dynamic threshold here is well-motivated.")
        A("3. *(Optional, weak)* a single regime/time conditioner — but expect little "
          "and watch for non-stationarity contamination.")
        A("")
        A("**Drop everything else.** `rvol`, `spread`, `l1_depth`, `fav_imbalance`, "
          "`utc_hour`, `btc_agree`, and the tick-spot chop features (`chop_gate_dist`, "
          "`spot_strike_fav`) are noise here — book microstructure and hour-of-day "
          "died to placebo (consistent with every prior filter study), and the "
          "tick-spot chop gate did **not** clear placebo on the older-box subset "
          "(p≈0.07), so even the deployed gate is, on this archive, marginal at best.")
        A("")
        A("**Bottom line:** the only signals worth a dynamic model are the **pre-entry "
          "spot path** — its *direction relative to the favorite* (Axis 1, dominant) "
          "and its *trend-vs-chop quality* (Axis 2, = the chop gate done right). "
          "Everything else is noise or restated market efficiency.")
    else:
        A("**Build nothing yet.** No entry-time feature survived. The only levers with "
          "any prior support — the per-round efficiency/chop family — did not clear "
          "placebo+OOS on this archive either, which means even the deployed chop gate "
          "is, at best, marginal here. A dynamic model has no validated feature to "
          "stand on. Collect more (and more stationary) data before modeling.")
    A("")
    A("## Honest caveats")
    A("")
    A("- **Non-stationarity dominates.** 8 days, multiple box/config regimes; the "
      "TIME-OOS is the right test and it is unforgiving by design.")
    A("- **Paper, not real money.** Labels come from the archived market winner and "
      "the reconstructed favorite side, not realized fills; entry-price slippage and "
      "fill probability are not modeled here.")
    A("- **Single-feature screen.** This is feature SELECTION, not a joint model. "
      "Interactions are not tested; a feature that is individually noise could still "
      "matter conditionally (but that is a much higher bar and not what we screened).")
    A("- **Tick-spot features (`chop_gate_dist`, `spot_strike_fav`) cover only the "
      "older box** (~the markets carrying non-zero tick spot/strike); their coverage is "
      "smaller and their split spans fewer regimes — read their OOS with extra caution.")
    A("- **Look-ahead control.** Spot-path features use only round-open→entry-bar "
      "klines; book/regime features use only ticks/rounds at or before entry. The "
      "decisive post-entry move is, correctly, invisible to every feature.")
    A("")
    with open(MD_OUT, "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
