"""Guard the shipped deployable configs: they must load, build, and reference real strategies."""
import json
import os
import unittest
from polybot.live import build_portfolio
from polybot.strategies import available

CFG_DIR = os.path.join(os.path.dirname(__file__), "..", "polybot")


def _load(name):
    with open(os.path.join(CFG_DIR, name)) as f:
        cfg = json.load(f)
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


class TestDeployableConfigs(unittest.TestCase):
    def test_portfolio_json_builds(self):
        cfg = _load("portfolio.json")
        pf = build_portfolio(cfg)
        self.assertEqual(len(pf.strategies), len(cfg["strategies"]))
        # weights sum to 1
        self.assertAlmostEqual(sum(s["weight"] for s in cfg["strategies"]), 1.0, places=6)

    def test_live_config_has_both_edges(self):
        cfg = _load("portfolio_live.json")
        names = [s["name"] for s in cfg["strategies"]]
        self.assertIn("btc_spot_divergence", names)                 # informational edge
        self.assertTrue(any("fav" in n or "favorite" in n for n in names))  # behavioural edge
        self.assertAlmostEqual(sum(s["weight"] for s in cfg["strategies"]), 1.0, places=6)
        pf = build_portfolio(cfg)
        self.assertEqual(len(set(s.name for s in pf.strategies)), len(cfg["strategies"]))  # unique ids

    def test_all_referenced_strategies_registered(self):
        for name in ("portfolio.json", "portfolio_live.json"):
            for s in _load(name)["strategies"]:
                self.assertIn(s["name"], available(), f"{name}: {s['name']} not registered")


if __name__ == "__main__":
    unittest.main()
