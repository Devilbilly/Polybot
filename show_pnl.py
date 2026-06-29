#!/usr/bin/env python3
"""Show the DOLLAR P&L effect of the Phase-2 gates on the validated real harness.
Baseline reproduces our real -$ result; gates reuse real fill/shares/winner (exact).
In-sample is shown for context but the HELD-OUT test half is the honest number."""
import backtest_real as B
import phase2_entry as P2

fills = B.load_fills()
settled = [f for f in fills if f.settled]
enr = P2.VolEnricher()

band = lambda f: 0.78 <= f.fill_price <= 0.84
floor = lambda f: f.fill_price >= 0.78
chop = P2.make_vol_pred(enr, "preabsmove", 0.00056, drop_below=True)
combo = lambda f: band(f) and chop(f)
GATES = [("baseline (all trades, = real)", lambda f: True),
         ("floor >= 0.78", floor),
         ("band [0.78,0.84]", band),
         ("chop (skip flat pre-entry)", chop),
         ("band AND chop (combo)", combo)]


def row(name, fl):
    m = P2.margin_stats(fl)
    return (f"{name:32} {m['n']:5} {m['pnl']:+9.2f} {m['pnl_per_trade']:+9.4f} "
            f"{100*m['win_rate']:4.0f}% {m['mean_price']:6.3f}")


hdr = f"{'gate':32} {'n':>5} {'total$':>9} {'$/trade':>9} {'win%':>5} {'entry':>6}"

print("#" * 78)
print("DOLLAR P&L by gate -- harness reproduces our REAL result; gates are EXACT reuse")
print("#" * 78)
print(f"\n=== FULL SAMPLE (in-sample, 1.74 days, {len(settled)} fills) ===")
print("  (in-sample ALWAYS flatters a gate -- that's the overfit trap; OOS below is the real test)")
print(hdr)
for name, pred in GATES:
    print("  " + row(name, [f for f in settled if pred(f)]))

train, test = P2.chrono_split(settled, 0.60)
print(f"\n=== OUT-OF-SAMPLE: held-out TEST half only ({len(test)} fills, ~0.7 days) ===")
print("  *** THE HONEST NUMBER -- gate thresholds were frozen on the train half, never saw this ***")
print(hdr)
base = P2.margin_stats(test)
for name, pred in GATES:
    print("  " + row(name, [f for f in test if pred(f)]))
print(f"\n  baseline test total = ${base['pnl']:+.2f} on {base['n']} trades "
      f"({base['pnl_per_trade']:+.4f}/trade)")
for name, pred in GATES[1:]:
    m = P2.margin_stats([f for f in test if pred(f)])
    print(f"  {name:32} test = ${m['pnl']:+.2f} on {m['n']} trades "
          f"({100*m['n']/base['n']:.0f}% fire)  -> delta vs baseline ${m['pnl']-base['pnl']:+.2f}")
