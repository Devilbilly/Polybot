import asyncio
import json
import time
import os
import sys
import logging
import ssl
from collections import deque
from datetime import datetime

import aiohttp
import websockets

# ================= CONFIGURATION =================
WS_URI = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_API = "https://gamma-api.polymarket.com/events?slug={}"
CLOB_BOOK = "https://clob.polymarket.com/book?token_id={}"

WS_PING_INTERVAL = 25
WS_PING_TIMEOUT = 20
WS_RECV_TIMEOUT = 60

RECONNECT_BASE = 2
RECONNECT_CAP = 30
L2_REFRESH_INTERVAL = 1.0
PRINT_EVERY_N = 10

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("poly")

L2_CACHE = {}

# ================= STRATEGY: SWING_DUAL =================
class SwingDualTrader:
    """Buys both YES and NO when cheap, profits at settlement."""

    def __init__(self, genome, capital):
        self.name = "SD"
        self.cash = capital
        self.inv_yes = 0.0
        self.inv_no = 0.0
        self.b_cnt_yes = 0
        self.b_cnt_no = 0
        self.has_stopped_yes = False
        self.has_stopped_no = False
        self.depth_stable_ticks = 0

        self.fee_rate = 0.001
        self.slippage = 0.002
        self.bullet_usd = capital * 0.05  # 5% of allocated capital per trade

        self.buy_p = genome.get('buy_p', 0.24)
        self.sell_p = genome.get('sell_p', 0.92)
        self.stop_p = genome.get('stop_p', 0.05)
        self.max_buy = genome.get('max_buy', 10)
        self.time_cutoff = genome.get('time_cutoff', 0.40)
        self.depth_threshold = genome.get('depth_threshold', 350.0)

    def reset_market(self):
        self.b_cnt_yes = 0
        self.b_cnt_no = 0
        self.has_stopped_yes = False
        self.has_stopped_no = False
        self.inv_yes = 0.0
        self.inv_no = 0.0
        self.depth_stable_ticks = 0

    def force_liquidate(self, ws_bid, ws_ask, bp1, ap1):
        if self.inv_yes > 0:
            exec_bp1 = max(0.0001, bp1 - self.slippage)
            rev = self.inv_yes * exec_bp1
            self.cash += rev * (1.0 - self.fee_rate)
            log.info("[LIQUIDATE][%s] Sold YES %.1f at %.4f", self.name, self.inv_yes, exec_bp1)
        if self.inv_no > 0:
            exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - self.slippage)
            rev = self.inv_no * exec_no_bid_p1
            self.cash += rev * (1.0 - self.fee_rate)
            log.info("[LIQUIDATE][%s] Sold NO %.1f at %.4f", self.name, self.inv_no, exec_no_bid_p1)
        self.reset_market()

    def process_tick(self, ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, momentum=0.0):
        exec_bp1 = max(0.0001, bp1 - self.slippage)
        exec_ap1 = min(0.9999, ap1 + self.slippage)
        no_ask_ws = 1.0 - ws_bid
        no_bid_ws = 1.0 - ws_ask
        exec_no_ask_p1 = min(0.9999, (1.0 - bp1) + self.slippage)
        exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - self.slippage)

        # Depth stability filter
        l2_total_depth = bs1 + as1
        if l2_total_depth >= self.depth_threshold:
            self.depth_stable_ticks += 1
        else:
            self.depth_stable_ticks = 0
        is_depth_valid = (self.depth_stable_ticks >= 15)

        action = False

        # Sell YES
        if self.inv_yes >= 10.0:
            if ws_bid >= self.sell_p and bp1 >= self.sell_p and bs1 > 0.0:
                sold = min(self.inv_yes, bs1)
                self.inv_yes -= sold
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                log.info("[%s][%s] SELL YES | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_bp1, sold, self.cash)
                action = True
            elif ws_bid <= self.stop_p and bp1 > 0.0 and bs1 > 0.0:
                sold = min(self.inv_yes, bs1)
                self.inv_yes -= sold
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                self.has_stopped_yes = True
                log.info("[%s][%s] STOP YES | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_bp1, sold, self.cash)
                action = True

        # Sell NO
        if self.inv_no >= 10.0:
            if no_bid_ws >= self.sell_p and (1.0 - ap1) >= self.sell_p and as1 > 0.0:
                sold = min(self.inv_no, as1)
                self.inv_no -= sold
                self.cash += (sold * exec_no_bid_p1) * (1.0 - self.fee_rate)
                log.info("[%s][%s] SELL NO  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_no_bid_p1, sold, self.cash)
                action = True
            elif no_bid_ws <= self.stop_p and (1.0 - ap1) > 0.0 and as1 > 0.0:
                sold = min(self.inv_no, as1)
                self.inv_no -= sold
                self.cash += (sold * exec_no_bid_p1) * (1.0 - self.fee_rate)
                self.has_stopped_no = True
                log.info("[%s][%s] STOP NO  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_no_bid_p1, sold, self.cash)
                action = True

        # Buy YES
        if time_progress < self.time_cutoff and is_depth_valid:
            if not self.has_stopped_yes and self.b_cnt_yes < self.max_buy and ws_ask <= self.buy_p:
                if ap1 > 0.0 and ap1 <= self.buy_p and as1 > 0.0:
                    target = self.bullet_usd / exec_ap1
                    affordable = self.cash / (exec_ap1 * (1.0 + self.fee_rate))
                    bought = min(target, min(as1, affordable))
                    if bought >= 1.0:
                        self.inv_yes += bought
                        self.cash -= (bought * exec_ap1) * (1.0 + self.fee_rate)
                        self.b_cnt_yes += 1
                        log.info("[%s][%s] BUY YES  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_ap1, bought, self.cash)
                        action = True

        # Buy NO
        if time_progress < self.time_cutoff and is_depth_valid:
            if not self.has_stopped_no and self.b_cnt_no < self.max_buy and no_ask_ws <= self.buy_p:
                if (1.0 - bp1) > 0.0 and (1.0 - bp1) <= self.buy_p and bs1 > 0.0:
                    target = self.bullet_usd / exec_no_ask_p1
                    affordable = self.cash / (exec_no_ask_p1 * (1.0 + self.fee_rate))
                    bought = min(target, min(bs1, affordable))
                    if bought >= 1.0:
                        self.inv_no += bought
                        self.cash -= (bought * exec_no_ask_p1) * (1.0 + self.fee_rate)
                        self.b_cnt_no += 1
                        log.info("[%s][%s] BUY NO   | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_no_ask_p1, bought, self.cash)
                        action = True

        return action

    def est_value(self, ws_bid, ws_ask):
        no_bid_ws = 1.0 - ws_ask
        return self.cash + (self.inv_yes * ws_bid) + (self.inv_no * no_bid_ws)


# ================= STRATEGY: MOMENTUM_DUAL =================
class MomentumDualTrader:
    """Directional strategy: uses early momentum to pick YES or NO side."""

    def __init__(self, genome, capital):
        self.name = "MD"
        self.cash = capital
        self.inv_yes = 0.0
        self.inv_no = 0.0
        self.b_cnt_yes = 0
        self.b_cnt_no = 0
        self.depth_stable_ticks = 0

        self.fee_rate = 0.001
        self.slippage = 0.002
        self.bullet_usd = capital * 0.05

        self.buy_p = genome.get('buy_p', 0.28)
        self.sell_p = genome.get('sell_p', 0.84)
        self.max_buy = genome.get('max_buy', 30)
        self.lookback_frac = max(0.05, abs(genome.get('stop_p', 0.027)))
        self.time_cutoff = genome.get('time_cutoff', 0.67)
        self.depth_threshold = genome.get('depth_threshold', 118.0)

    def reset_market(self):
        self.b_cnt_yes = 0
        self.b_cnt_no = 0
        self.inv_yes = 0.0
        self.inv_no = 0.0
        self.depth_stable_ticks = 0

    def force_liquidate(self, ws_bid, ws_ask, bp1, ap1):
        if self.inv_yes > 0:
            exec_bp1 = max(0.0001, bp1 - self.slippage)
            self.cash += (self.inv_yes * exec_bp1) * (1.0 - self.fee_rate)
            log.info("[LIQUIDATE][%s] Sold YES %.1f at %.4f", self.name, self.inv_yes, exec_bp1)
        if self.inv_no > 0:
            exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - self.slippage)
            self.cash += (self.inv_no * exec_no_bid_p1) * (1.0 - self.fee_rate)
            log.info("[LIQUIDATE][%s] Sold NO %.1f at %.4f", self.name, self.inv_no, exec_no_bid_p1)
        self.reset_market()

    def process_tick(self, ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, momentum=0.0):
        exec_bp1 = max(0.0001, bp1 - self.slippage)
        exec_ap1 = min(0.9999, ap1 + self.slippage)
        no_ask_ws = 1.0 - ws_bid
        no_bid_ws = 1.0 - ws_ask
        exec_no_ask_p1 = min(0.9999, (1.0 - bp1) + self.slippage)
        exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - self.slippage)

        # Depth filter
        l2_total_depth = bs1 + as1
        if l2_total_depth >= self.depth_threshold:
            self.depth_stable_ticks += 1
        else:
            self.depth_stable_ticks = 0
        is_depth_valid = (self.depth_stable_ticks >= 15)

        favor_yes = (momentum > 0.02)
        favor_no = (momentum < -0.02)

        action = False

        # Sell YES (take profit or late-market exit)
        if self.inv_yes >= 10.0:
            if ws_bid >= self.sell_p and bp1 >= self.sell_p and bs1 > 0.0:
                sold = min(self.inv_yes, bs1)
                self.inv_yes -= sold
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                log.info("[%s][%s] SELL YES | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_bp1, sold, self.cash)
                action = True
            elif time_progress > 0.85 and bp1 > 0.0 and bs1 > 0.0:
                sold = min(self.inv_yes, bs1)
                self.inv_yes -= sold
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                log.info("[%s][%s] EXIT YES | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_bp1, sold, self.cash)
                action = True

        # Sell NO
        if self.inv_no >= 10.0:
            if no_bid_ws >= self.sell_p and (1.0 - ap1) >= self.sell_p and as1 > 0.0:
                sold = min(self.inv_no, as1)
                self.inv_no -= sold
                self.cash += (sold * exec_no_bid_p1) * (1.0 - self.fee_rate)
                log.info("[%s][%s] SELL NO  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_no_bid_p1, sold, self.cash)
                action = True
            elif time_progress > 0.85 and (1.0 - ap1) > 0.0 and as1 > 0.0:
                sold = min(self.inv_no, as1)
                self.inv_no -= sold
                self.cash += (sold * exec_no_bid_p1) * (1.0 - self.fee_rate)
                log.info("[%s][%s] EXIT NO  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_no_bid_p1, sold, self.cash)
                action = True

        # Buy YES (only when momentum favors YES)
        if time_progress < self.time_cutoff and is_depth_valid and favor_yes:
            if self.b_cnt_yes < self.max_buy and ws_ask <= self.buy_p:
                if ap1 > 0.0 and ap1 <= self.buy_p and as1 > 0.0:
                    target = self.bullet_usd / exec_ap1
                    affordable = self.cash / (exec_ap1 * (1.0 + self.fee_rate))
                    bought = min(target, min(as1, affordable))
                    if bought >= 1.0:
                        self.inv_yes += bought
                        self.cash -= (bought * exec_ap1) * (1.0 + self.fee_rate)
                        self.b_cnt_yes += 1
                        log.info("[%s][%s] BUY YES  | P:%.4f S:%.1f Mom:%.3f Cash:%.2f",
                                 ts, self.name, exec_ap1, bought, momentum, self.cash)
                        action = True

        # Buy NO (only when momentum favors NO)
        if time_progress < self.time_cutoff and is_depth_valid and favor_no:
            if self.b_cnt_no < self.max_buy and no_ask_ws <= self.buy_p:
                if (1.0 - bp1) > 0.0 and (1.0 - bp1) <= self.buy_p and bs1 > 0.0:
                    target = self.bullet_usd / exec_no_ask_p1
                    affordable = self.cash / (exec_no_ask_p1 * (1.0 + self.fee_rate))
                    bought = min(target, min(bs1, affordable))
                    if bought >= 1.0:
                        self.inv_no += bought
                        self.cash -= (bought * exec_no_ask_p1) * (1.0 + self.fee_rate)
                        self.b_cnt_no += 1
                        log.info("[%s][%s] BUY NO   | P:%.4f S:%.1f Mom:%.3f Cash:%.2f",
                                 ts, self.name, exec_no_ask_p1, bought, momentum, self.cash)
                        action = True

        return action

    def est_value(self, ws_bid, ws_ask):
        no_bid_ws = 1.0 - ws_ask
        return self.cash + (self.inv_yes * ws_bid) + (self.inv_no * no_bid_ws)


# ================= STRATEGY: TREND_SCALP =================
class TrendScalpTrader:
    """Momentum scalper: enters with trend, takes quick profits, can re-enter."""

    def __init__(self, genome, capital):
        self.name = "TS"
        self.cash = capital
        self.inv_yes = 0.0
        self.inv_no = 0.0
        self.b_cnt_yes = 0
        self.b_cnt_no = 0
        self.depth_stable_ticks = 0

        self.fee_rate = 0.001
        self.slippage = 0.002
        self.bullet_usd = capital * 0.05

        self.buy_p = genome.get('buy_p', 0.17)
        self.sell_p = genome.get('sell_p', 0.94)
        self.max_buy = genome.get('max_buy', 29)
        self.lookback_frac = max(0.02, abs(genome.get('stop_p', 0.02)))
        self.time_cutoff = genome.get('time_cutoff', 0.62)
        self.depth_threshold = genome.get('depth_threshold', 394.0)

    def reset_market(self):
        self.b_cnt_yes = 0
        self.b_cnt_no = 0
        self.inv_yes = 0.0
        self.inv_no = 0.0
        self.depth_stable_ticks = 0

    def force_liquidate(self, ws_bid, ws_ask, bp1, ap1):
        if self.inv_yes > 0:
            exec_bp1 = max(0.0001, bp1 - self.slippage)
            self.cash += (self.inv_yes * exec_bp1) * (1.0 - self.fee_rate)
            log.info("[LIQUIDATE][%s] Sold YES %.1f at %.4f", self.name, self.inv_yes, exec_bp1)
        if self.inv_no > 0:
            exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - self.slippage)
            self.cash += (self.inv_no * exec_no_bid_p1) * (1.0 - self.fee_rate)
            log.info("[LIQUIDATE][%s] Sold NO %.1f at %.4f", self.name, self.inv_no, exec_no_bid_p1)
        self.reset_market()

    def process_tick(self, ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, momentum=0.0):
        exec_bp1 = max(0.0001, bp1 - self.slippage)
        exec_ap1 = min(0.9999, ap1 + self.slippage)
        no_ask_ws = 1.0 - ws_bid
        no_bid_ws = 1.0 - ws_ask
        exec_no_ask_p1 = min(0.9999, (1.0 - bp1) + self.slippage)
        exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - self.slippage)

        # Depth filter
        l2_total_depth = bs1 + as1
        if l2_total_depth >= self.depth_threshold:
            self.depth_stable_ticks += 1
        else:
            self.depth_stable_ticks = 0
        is_depth_valid = (self.depth_stable_ticks >= 10)

        action = False

        # Scalp sell YES (re-enables buy slot on success)
        if self.inv_yes >= 10.0 and bp1 > 0.0 and bs1 > 0.0:
            if ws_bid >= self.sell_p:
                sold = min(self.inv_yes, bs1)
                self.inv_yes -= sold
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                self.b_cnt_yes = max(0, self.b_cnt_yes - 1)
                log.info("[%s][%s] SCALP YES | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_bp1, sold, self.cash)
                action = True

        # Scalp sell NO
        if self.inv_no >= 10.0 and (1.0 - ap1) > 0.0 and as1 > 0.0:
            if no_bid_ws >= self.sell_p:
                sold = min(self.inv_no, as1)
                self.inv_no -= sold
                self.cash += (sold * exec_no_bid_p1) * (1.0 - self.fee_rate)
                self.b_cnt_no = max(0, self.b_cnt_no - 1)
                log.info("[%s][%s] SCALP NO  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_no_bid_p1, sold, self.cash)
                action = True

        # Buy with momentum
        if time_progress < self.time_cutoff and is_depth_valid:
            if momentum > 0.02 and self.b_cnt_yes < self.max_buy and ws_ask <= self.buy_p:
                if ap1 > 0.0 and ap1 <= self.buy_p and as1 > 0.0:
                    target = self.bullet_usd / exec_ap1
                    affordable = self.cash / (exec_ap1 * (1.0 + self.fee_rate))
                    bought = min(target, min(as1, affordable))
                    if bought >= 1.0:
                        self.inv_yes += bought
                        self.cash -= (bought * exec_ap1) * (1.0 + self.fee_rate)
                        self.b_cnt_yes += 1
                        log.info("[%s][%s] BUY YES  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_ap1, bought, self.cash)
                        action = True

            if momentum < -0.02 and self.b_cnt_no < self.max_buy and no_ask_ws <= self.buy_p:
                if (1.0 - bp1) > 0.0 and (1.0 - bp1) <= self.buy_p and bs1 > 0.0:
                    target = self.bullet_usd / exec_no_ask_p1
                    affordable = self.cash / (exec_no_ask_p1 * (1.0 + self.fee_rate))
                    bought = min(target, min(bs1, affordable))
                    if bought >= 1.0:
                        self.inv_no += bought
                        self.cash -= (bought * exec_no_ask_p1) * (1.0 + self.fee_rate)
                        self.b_cnt_no += 1
                        log.info("[%s][%s] BUY NO   | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_no_ask_p1, bought, self.cash)
                        action = True

        return action

    def est_value(self, ws_bid, ws_ask):
        no_bid_ws = 1.0 - ws_ask
        return self.cash + (self.inv_yes * ws_bid) + (self.inv_no * no_bid_ws)


# ================= PORTFOLIO MANAGER =================
class PortfolioManager:
    """Runs multiple strategies simultaneously with split capital."""

    def __init__(self, portfolio_file="genome_portfolio.json"):
        with open(portfolio_file, 'r') as f:
            config = json.load(f)

        total_capital = 1000.0
        self.traders = []
        self.bid_history = deque(maxlen=2000)  # for momentum calculation

        for strat in config["strategies"]:
            name = strat["name"]
            weight = strat["weight"]
            capital = total_capital * weight
            genome_file = f"genome_{name}.json"

            with open(genome_file, 'r') as f:
                genome = json.load(f)

            if name == "swing_dual":
                trader = SwingDualTrader(genome, capital)
            elif name == "momentum_dual":
                trader = MomentumDualTrader(genome, capital)
            elif name == "trend_scalp":
                trader = TrendScalpTrader(genome, capital)
            else:
                log.warning("[PORTFOLIO] Unknown strategy: %s, skipping", name)
                continue

            self.traders.append(trader)
            log.info("[PORTFOLIO] Loaded %s (w=%.0f%%, capital=$%.0f)", name, weight * 100, capital)

        log.info("[PORTFOLIO] Total strategies: %d", len(self.traders))

    def reset_all(self):
        self.bid_history.clear()
        for t in self.traders:
            t.reset_market()

    def force_liquidate_all(self, ws_bid, ws_ask, bp1, ap1):
        for t in self.traders:
            t.force_liquidate(ws_bid, ws_ask, bp1, ap1)
        total = sum(t.cash for t in self.traders)
        log.info("[PORTFOLIO] Market closed. Total Cash: $%.2f", total)
        self.bid_history.clear()

    def process_tick(self, ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress):
        # Update momentum history
        self.bid_history.append(ws_bid)

        # Calculate momentum for momentum-based strategies
        momentum = 0.0
        # Use lookback based on the momentum_dual strategy's setting
        for t in self.traders:
            if hasattr(t, 'lookback_frac'):
                lookback_ticks = max(10, int(t.lookback_frac * 4800))  # ~4800 ticks per market
                if len(self.bid_history) > lookback_ticks:
                    momentum = ws_bid - self.bid_history[-lookback_ticks]
                break

        # If no momentum trader, compute a default
        if momentum == 0.0 and len(self.bid_history) > 100:
            momentum = ws_bid - self.bid_history[-100]

        any_action = False
        for t in self.traders:
            acted = t.process_tick(ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, momentum)
            any_action = any_action or acted

        if any_action:
            total_val = sum(t.est_value(ws_bid, ws_ask) for t in self.traders)
            log.info("[%s] [PORTFOLIO] Est Total Value: $%.2f | TP: %.1f%%",
                     ts, total_val, time_progress * 100)

    def total_cash(self):
        return sum(t.cash for t in self.traders)

    def total_value(self, ws_bid, ws_ask):
        return sum(t.est_value(ws_bid, ws_ask) for t in self.traders)


# ================= NETWORK FUNCTIONS =================
async def _http_get_json(session, url, timeout=5):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            return await resp.json(content_type=None)
    except Exception:
        return None

async def get_predicted_live_slug(session):
    now = int(time.time())
    base = now - (now % 300)
    candidates = [
        "btc-updown-5m-{}".format(base),
        "btc-updown-5m-{}".format(base + 300),
    ]
    for slug in candidates:
        data = await _http_get_json(session, GAMMA_API.format(slug), timeout=5)
        if data and len(data) > 0 and not data[0].get("closed"):
            return slug
    return None

async def get_tokens_from_slug(session, slug):
    data = await _http_get_json(session, GAMMA_API.format(slug), timeout=5)
    if not data:
        return []
    markets = data[0].get("markets", [])
    tokens = []
    for m in markets:
        if not m.get("closed") and m.get("active"):
            clob_ids = m.get("clobTokenIds", [])
            if isinstance(clob_ids, str):
                try:
                    clob_ids = json.loads(clob_ids)
                except Exception:
                    clob_ids = clob_ids.replace("[", "").replace("]", "").replace('"', "").replace("'", "").split(",")
            if isinstance(clob_ids, list) and len(clob_ids) >= 2:
                tokens.extend(clob_ids)
    return list(set(tokens))

async def update_l2_cache(session, tokens):
    while True:
        tasks = [_http_get_json(session, CLOB_BOOK.format(t), timeout=3) for t in tokens]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for t, data in zip(tokens, results):
            if isinstance(data, dict):
                record = {
                    'b_p1': 0.0, 'b_s1': 0.0,
                    'a_p1': 0.0, 'a_s1': 0.0,
                }
                if "bids" in data and len(data["bids"]) > 0:
                    bids = sorted(data["bids"], key=lambda x: float(x["price"]), reverse=True)
                    record['b_p1'] = float(bids[0]["price"])
                    record['b_s1'] = float(bids[0]["size"])
                if "asks" in data and len(data["asks"]) > 0:
                    asks = sorted(data["asks"], key=lambda x: float(x["price"]))
                    record['a_p1'] = float(asks[0]["price"])
                    record['a_s1'] = float(asks[0]["size"])
                L2_CACHE[t] = record
        await asyncio.sleep(L2_REFRESH_INTERVAL)

# ================= MAIN LOOP =================
async def monitor_strategy():
    portfolio = PortfolioManager()

    msg_counter = 0
    backoff = RECONNECT_BASE
    empty_l2 = {'b_p1': 0.0, 'b_s1': 0.0, 'a_p1': 0.0, 'a_s1': 0.0}

    connector = aiohttp.TCPConnector(ssl=False)

    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla"}, connector=connector) as session:
        while True:
            current_slug = await get_predicted_live_slug(session)
            if not current_slug:
                await asyncio.sleep(2)
                continue

            try:
                end_timestamp = int(current_slug.split("-")[-1]) + 300
            except Exception:
                end_timestamp = int(time.time()) + 300

            log.info("\n[SYSTEM] Target Locked: %s", current_slug)
            tokens = await get_tokens_from_slug(session, current_slug)
            if not tokens:
                await asyncio.sleep(2)
                continue

            portfolio.reset_all()
            l2_task = asyncio.ensure_future(update_l2_cache(session, tokens))

            last_known_bids = {}
            last_known_asks = {}

            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            try:
                async with websockets.connect(
                    WS_URI,
                    ssl=ssl_context,
                    ping_interval=WS_PING_INTERVAL,
                    ping_timeout=WS_PING_TIMEOUT,
                    close_timeout=5,
                    max_size=2**22
                ) as websocket:
                    log.info("[SYSTEM] WS Connection established.")
                    backoff = RECONNECT_BASE

                    sub_msg = {
                        "assets_ids": tokens,
                        "type": "market",
                        "custom_feature_enabled": True,
                    }
                    await websocket.send(json.dumps(sub_msg))

                    while True:
                        try:
                            res = await asyncio.wait_for(websocket.recv(), timeout=WS_RECV_TIMEOUT)
                        except asyncio.TimeoutError:
                            log.info("[SYSTEM] No data for %ds, reconnecting...", WS_RECV_TIMEOUT)
                            break
                        except websockets.exceptions.ConnectionClosed as cc:
                            log.info("[SYSTEM] Connection closed: %s", cc)
                            break

                        try:
                            data = json.loads(res)
                        except json.JSONDecodeError:
                            continue

                        now_dt = datetime.now()
                        ms_ts = now_dt.strftime("%H:%M:%S.%f")[:-3]
                        rem_f = end_timestamp - now_dt.timestamp()

                        if rem_f <= 0:
                            log.info("[SYSTEM] Market expired. Liquidating all positions...")
                            for t_id in tokens:
                                if t_id in last_known_bids and t_id in last_known_asks:
                                    l2 = L2_CACHE.get(t_id, empty_l2)
                                    portfolio.force_liquidate_all(
                                        last_known_bids[t_id], last_known_asks[t_id],
                                        l2['b_p1'], l2['a_p1']
                                    )
                                    break
                            break

                        items = data if isinstance(data, list) else [data]
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            if item.get("event_type") not in ("price_change", "best_bid_ask"):
                                continue

                            t_id_full = item.get("asset_id", "")
                            raw_ask = item.get("best_ask")
                            raw_bid = item.get("best_bid")
                            ws_ask = float(raw_ask) if raw_ask not in (None, "none") else 0.0
                            ws_bid = float(raw_bid) if raw_bid not in (None, "none") else 0.0

                            if t_id_full:
                                last_known_bids[t_id_full] = ws_bid
                                last_known_asks[t_id_full] = ws_ask

                            if t_id_full and ws_ask > 0 and ws_bid > 0:
                                l2 = L2_CACHE.get(t_id_full, empty_l2)
                                bp1 = l2['b_p1']
                                bs1 = l2['b_s1']
                                ap1 = l2['a_p1']
                                as1 = l2['a_s1']

                                elapsed = now_dt.timestamp() - (end_timestamp - 300)
                                time_progress = max(0.0, min(1.0, elapsed / 300.0))

                                portfolio.process_tick(ws_bid, ws_ask, bp1, bs1, ap1, as1, ms_ts, time_progress)

                                msg_counter += 1
                                if msg_counter % PRINT_EVERY_N == 0:
                                    rem_m = int(max(0, rem_f) // 60)
                                    rem_s = int(max(0, rem_f) % 60)
                                    rem_ms = int((max(0, rem_f) - int(max(0, rem_f))) * 1000)
                                    rem_str = f"{rem_m:02d}:{rem_s:02d}:{rem_ms:03d}"

                                    log.info("[INFO] %s (%s) ID:%s | WS: %.3f/%.3f | Portfolio: $%.2f",
                                             ms_ts, rem_str, t_id_full[:6], ws_bid, ws_ask,
                                             portfolio.total_value(ws_bid, ws_ask))

            except Exception as e:
                log.error("[ERROR] WS Exception: %s", e)
                await asyncio.sleep(2.0)
                backoff = min(backoff * 2, RECONNECT_CAP)
            finally:
                l2_task.cancel()

if __name__ == "__main__":
    log.info("=== Polymarket Portfolio Paper Trading Engine (v2) ===")
    log.info("=== Strategies: swing_dual + momentum_dual + trend_scalp ===")
    try:
        asyncio.run(monitor_strategy())
    except KeyboardInterrupt:
        log.info("\n[EXIT] Process terminated. Final cash: $%.2f", 0.0)
