"""Offline test of the ClobExecutor adverse-selection / floor gate (max_slip_below).
Bypasses __init__ (no network/creds) and injects fake py-clob-client-v2 modules so place_order's
lazy imports resolve. Verifies: a collapsing favorite (price dropped below the decision) is REJECTED,
while a stable / mildly-slipped favorite still fills."""
import sys
import types
import unittest


def _inject_fake_clob():
    m = types.ModuleType("py_clob_client_v2")
    ct = types.ModuleType("py_clob_client_v2.clob_types")
    ct.OrderArgs = lambda **kw: kw
    ob = types.ModuleType("py_clob_client_v2.order_builder")
    obc = types.ModuleType("py_clob_client_v2.order_builder.constants")
    obc.BUY = "BUY"
    sys.modules.update({
        "py_clob_client_v2": m, "py_clob_client_v2.clob_types": ct,
        "py_clob_client_v2.order_builder": ob, "py_clob_client_v2.order_builder.constants": obc})


_inject_fake_clob()
from polybot.execution import ClobExecutor   # noqa: E402


def _build(ask, slip=0.02):
    """A ClobExecutor with __init__ bypassed; _book_ask returns `ask`, dry_run builds nothing real."""
    ex = object.__new__(ClobExecutor)
    ex.max_slip_below = slip
    ex.min_price = 0.5
    ex.desync_tol = 0.05
    ex.price_buffer_ticks = 3
    ex.min_usd = 1.0
    ex.max_shares = 5.0
    ex.dry_run = True
    ex._tick_cache = {}
    ex._book_ask = lambda tok: ask
    ex._tick = lambda tok: 0.01
    ex.client = types.SimpleNamespace(create_order=lambda a: None)
    return ex


class FloorGate(unittest.TestCase):
    def test_collapsing_favorite_is_rejected(self):
        f = _build(ask=0.70).place_order("tok", "YES", 1.0, price_hint=0.80)
        self.assertEqual(f.shares, 0.0)
        self.assertIn("slipped", f.status)

    def test_deep_collapse_rejected(self):
        f = _build(ask=0.53).place_order("tok", "YES", 1.0, price_hint=0.78)
        self.assertEqual(f.shares, 0.0)
        self.assertTrue(f.status.startswith("REJECTED:slipped"))

    def test_stable_favorite_fills(self):
        f = _build(ask=0.81).place_order("tok", "YES", 1.0, price_hint=0.80)
        self.assertGreater(f.shares, 0.0)
        self.assertEqual(f.status, "DRYRUN")

    def test_small_slip_within_tolerance_fills(self):
        f = _build(ask=0.79).place_order("tok", "YES", 1.0, price_hint=0.80)  # 1c below, within 2c tol
        self.assertGreater(f.shares, 0.0)

    def test_no_side_collapse_also_gated(self):
        # NO side: _book_ask is the Down-token ask; a collapse below the decision is rejected too.
        f = _build(ask=0.68).place_order("tok", "NO", 1.0, price_hint=0.80, no_token="down")
        self.assertEqual(f.shares, 0.0)

    def test_no_hint_does_not_over_reject(self):
        # if the strategy passed no price_hint, the gate must not fire (nothing to compare to)
        f = _build(ask=0.82).place_order("tok", "YES", 1.0, price_hint=None)
        self.assertGreater(f.shares, 0.0)


if __name__ == "__main__":
    unittest.main()
