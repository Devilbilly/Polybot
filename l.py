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

# ================= LOGGING SETUP =================
log = logging.getLogger("poly")
log.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(message)s')
console_handler.setFormatter(console_formatter)

file_handler = logging.FileHandler("paper_trader_detail.log", encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s | %(message)s')
file_handler.setFormatter(file_formatter)

log.addHandler(console_handler)
log.addHandler(file_handler)

L2_CACHE = {}

# ================= STRATEGY BASE =================
class StrategySlot:
    """One strategy slot within the portfolio."""

    def __init__(self, name, genome, capital):
        self.name = name
        self.strategy = genome.get('strategy', name)
        self.cash = capital
        self.inv_yes = 0.0
        self.inv_no = 0.0
        self.b_cnt_yes = 0
        self.b_cnt_no = 0
        self.has_stopped_yes = False
        self.has_stopped_no = False

        self.depth_ok_since = 0.0
        self.depth_wait_sec = 3.0

        # Round tracking
        self.r_buy_count = 0
        self.r_yes_bought = 0.0
        self.r_no_bought = 0.0
        self.r_yes_sold = 0.0
        self.r_no_sold = 0.0
        self.r_start_cash = self.cash

        self.fee_rate = 0.001
        self.slippage = 0.002
        self.initial_capital = capital
        self.base_bullet_pct = genome.get('bullet_pct', 0.02)  # per-strategy sizing (validated via backtest_sizing.py)
        self.bullet_pct = self.base_bullet_pct

        self.buy_p = genome.get('buy_p', 0.24)
        self.sell_p = genome.get('sell_p', 0.92)
        self.stop_p = genome.get('stop_p', 0.05)
        self.max_buy = genome.get('max_buy', 10)
        self.time_cutoff = genome.get('time_cutoff', 0.40)
        self.depth_threshold = genome.get('depth_threshold', 350.0)
        self.lookback_frac = max(0.02, abs(genome.get('stop_p', 0.03)))

    def reset_market_state(self):
        self.b_cnt_yes = 0
        self.b_cnt_no = 0
        self.has_stopped_yes = False
        self.has_stopped_no = False
        self.inv_yes = 0.0
        self.inv_no = 0.0
        self.depth_ok_since = 0.0
        self.r_buy_count = 0
        self.r_yes_bought = 0.0
        self.r_no_bought = 0.0
        self.r_yes_sold = 0.0
        self.r_no_sold = 0.0
        self.r_start_cash = self.cash

    def _check_depth(self, bs1, as1):
        current_time = time.time()
        l2_total_depth = bs1 + as1
        if l2_total_depth >= self.depth_threshold:
            if self.depth_ok_since == 0.0:
                self.depth_ok_since = current_time
        else:
            self.depth_ok_since = 0.0
        return (self.depth_ok_since > 0.0) and ((current_time - self.depth_ok_since) >= self.depth_wait_sec)

    def smart_settle(self, ws_bid, ws_ask):
        winner_yes = ws_ask > 0.5
        payout = self.inv_yes * 1.0 if winner_yes else self.inv_no * 1.0
        self.cash += payout
        round_pnl = self.cash - self.r_start_cash
        return {
            'name': self.name,
            'strategy': self.strategy,
            'winner': 'YES' if winner_yes else 'NO',
            'buy_count': self.r_buy_count,
            'max_buy_slots': self.max_buy * 2,
            'yes_bought': self.r_yes_bought,
            'yes_sold': self.r_yes_sold,
            'yes_held': self.inv_yes,
            'no_bought': self.r_no_bought,
            'no_sold': self.r_no_sold,
            'no_held': self.inv_no,
            'round_pnl': round_pnl,
            'cash': self.cash,
        }

    def est_value(self, ws_bid, ws_ask):
        no_bid_ws = 1.0 - ws_ask
        return self.cash + (self.inv_yes * ws_bid) + (self.inv_no * no_bid_ws)


# ================= SWING STOP LOGIC =================
class SwingStopSlot(StrategySlot):
    """Buy YES when cheap, sell at take-profit, stop-loss on reversal. YES side only."""

    def process_tick(self, ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, momentum=0.0):
        if bs1 <= 0.0 or as1 <= 0.0:
            return
        exec_bp1 = max(0.0001, bp1 - self.slippage)
        exec_ap1 = min(0.9999, ap1 + self.slippage)

        is_depth_valid = self._check_depth(bs1, as1)

        # Sell YES (take profit)
        if self.inv_yes >= 1.0:
            if ws_bid >= self.sell_p and bp1 >= self.sell_p:
                sold = min(self.inv_yes, bs1)
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                self.inv_yes -= sold
                self.r_yes_sold += sold
                log.debug("[%s][%s] SELL YES | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_bp1, sold, self.cash)
            elif ws_bid <= self.stop_p and bp1 > 0.0:
                sold = min(self.inv_yes, bs1)
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                self.inv_yes -= sold
                self.has_stopped_yes = True
                self.r_yes_sold += sold
                log.debug("[%s][%s] STOP YES | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_bp1, sold, self.cash)

        # Buy YES
        if time_progress < self.time_cutoff and is_depth_valid:
            if not self.has_stopped_yes and self.b_cnt_yes < self.max_buy and ws_ask <= self.buy_p:
                if ap1 <= self.buy_p:
                    target = self.cash * self.bullet_pct / exec_ap1
                    affordable = self.cash / (exec_ap1 * (1.0 + self.fee_rate))
                    bought = min(target, as1, affordable)
                    if bought >= 1.0:
                        self.cash -= (bought * exec_ap1) * (1.0 + self.fee_rate)
                        self.inv_yes += bought
                        self.b_cnt_yes += 1
                        self.r_buy_count += 1
                        self.r_yes_bought += bought
                        log.debug("[%s][%s] BUY YES  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_ap1, bought, self.cash)


# ================= SWING DUAL LOGIC =================
class SwingDualSlot(StrategySlot):
    def process_tick(self, ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, momentum=0.0):
        if bs1 <= 0.0 or as1 <= 0.0:
            return
        exec_bp1 = max(0.0001, bp1 - self.slippage)
        exec_ap1 = min(0.9999, ap1 + self.slippage)
        no_ask_ws = 1.0 - ws_bid
        no_bid_ws = 1.0 - ws_ask
        exec_no_ask_p1 = min(0.9999, (1.0 - bp1) + self.slippage)
        exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - self.slippage)

        is_depth_valid = self._check_depth(bs1, as1)

        # Sell YES
        if self.inv_yes >= 1.0:
            if ws_bid >= self.sell_p and bp1 >= self.sell_p:
                sold = min(self.inv_yes, bs1)
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                self.inv_yes -= sold
                self.r_yes_sold += sold
                log.debug("[%s][%s] SELL YES | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_bp1, sold, self.cash)
            elif ws_bid <= self.stop_p and bp1 > 0.0:
                sold = min(self.inv_yes, bs1)
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                self.inv_yes -= sold
                self.has_stopped_yes = True
                self.r_yes_sold += sold
                log.debug("[%s][%s] STOP YES | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_bp1, sold, self.cash)

        # Sell NO
        if self.inv_no >= 1.0:
            if no_bid_ws >= self.sell_p and (1.0 - ap1) >= self.sell_p:
                sold = min(self.inv_no, as1)
                self.cash += (sold * exec_no_bid_p1) * (1.0 - self.fee_rate)
                self.inv_no -= sold
                self.r_no_sold += sold
                log.debug("[%s][%s] SELL NO  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_no_bid_p1, sold, self.cash)
            elif no_bid_ws <= self.stop_p and (1.0 - ap1) > 0.0:
                sold = min(self.inv_no, as1)
                self.cash += (sold * exec_no_bid_p1) * (1.0 - self.fee_rate)
                self.inv_no -= sold
                self.has_stopped_no = True
                self.r_no_sold += sold
                log.debug("[%s][%s] STOP NO  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_no_bid_p1, sold, self.cash)

        # Buy YES
        if time_progress < self.time_cutoff and is_depth_valid:
            if not self.has_stopped_yes and self.b_cnt_yes < self.max_buy and ws_ask <= self.buy_p:
                if ap1 <= self.buy_p:
                    target = self.cash * self.bullet_pct / exec_ap1
                    affordable = self.cash / (exec_ap1 * (1.0 + self.fee_rate))
                    bought = min(target, as1, affordable)
                    if bought >= 1.0:
                        self.cash -= (bought * exec_ap1) * (1.0 + self.fee_rate)
                        self.inv_yes += bought
                        self.b_cnt_yes += 1
                        self.r_buy_count += 1
                        self.r_yes_bought += bought
                        log.debug("[%s][%s] BUY YES  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_ap1, bought, self.cash)

        # Buy NO
        if time_progress < self.time_cutoff and is_depth_valid:
            if not self.has_stopped_no and self.b_cnt_no < self.max_buy and no_ask_ws <= self.buy_p:
                if (1.0 - bp1) <= self.buy_p:
                    target = self.cash * self.bullet_pct / exec_no_ask_p1
                    affordable = self.cash / (exec_no_ask_p1 * (1.0 + self.fee_rate))
                    bought = min(target, bs1, affordable)
                    if bought >= 1.0:
                        self.cash -= (bought * exec_no_ask_p1) * (1.0 + self.fee_rate)
                        self.inv_no += bought
                        self.b_cnt_no += 1
                        self.r_buy_count += 1
                        self.r_no_bought += bought
                        log.debug("[%s][%s] BUY NO   | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_no_ask_p1, bought, self.cash)


# ================= MOMENTUM DUAL LOGIC =================
class MomentumDualSlot(StrategySlot):
    def process_tick(self, ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, momentum=0.0):
        if bs1 <= 0.0 or as1 <= 0.0:
            return
        exec_bp1 = max(0.0001, bp1 - self.slippage)
        exec_ap1 = min(0.9999, ap1 + self.slippage)
        no_ask_ws = 1.0 - ws_bid
        no_bid_ws = 1.0 - ws_ask
        exec_no_ask_p1 = min(0.9999, (1.0 - bp1) + self.slippage)
        exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - self.slippage)

        is_depth_valid = self._check_depth(bs1, as1)
        favor_yes = (momentum > 0.02)
        favor_no = (momentum < -0.02)

        # Sell YES (take profit or late-market exit)
        if self.inv_yes >= 1.0:
            if ws_bid >= self.sell_p and bp1 >= self.sell_p:
                sold = min(self.inv_yes, bs1)
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                self.inv_yes -= sold
                self.r_yes_sold += sold
                log.debug("[%s][%s] SELL YES | P:%.4f S:%.1f Mom:%.3f Cash:%.2f", ts, self.name, exec_bp1, sold, momentum, self.cash)
            elif time_progress > 0.85 and bp1 > 0.0:
                sold = min(self.inv_yes, bs1)
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                self.inv_yes -= sold
                self.r_yes_sold += sold
                log.debug("[%s][%s] EXIT YES | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_bp1, sold, self.cash)

        # Sell NO
        if self.inv_no >= 1.0:
            if no_bid_ws >= self.sell_p and (1.0 - ap1) >= self.sell_p:
                sold = min(self.inv_no, as1)
                self.cash += (sold * exec_no_bid_p1) * (1.0 - self.fee_rate)
                self.inv_no -= sold
                self.r_no_sold += sold
                log.debug("[%s][%s] SELL NO  | P:%.4f S:%.1f Mom:%.3f Cash:%.2f", ts, self.name, exec_no_bid_p1, sold, momentum, self.cash)
            elif time_progress > 0.85 and (1.0 - ap1) > 0.0:
                sold = min(self.inv_no, as1)
                self.cash += (sold * exec_no_bid_p1) * (1.0 - self.fee_rate)
                self.inv_no -= sold
                self.r_no_sold += sold
                log.debug("[%s][%s] EXIT NO  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_no_bid_p1, sold, self.cash)

        # Buy YES (only when momentum favors YES)
        if time_progress < self.time_cutoff and is_depth_valid and favor_yes:
            if self.b_cnt_yes < self.max_buy and ws_ask <= self.buy_p:
                if ap1 > 0.0 and ap1 <= self.buy_p:
                    target = self.cash * self.bullet_pct / exec_ap1
                    affordable = self.cash / (exec_ap1 * (1.0 + self.fee_rate))
                    bought = min(target, as1, affordable)
                    if bought >= 1.0:
                        self.cash -= (bought * exec_ap1) * (1.0 + self.fee_rate)
                        self.inv_yes += bought
                        self.b_cnt_yes += 1
                        self.r_buy_count += 1
                        self.r_yes_bought += bought
                        log.debug("[%s][%s] BUY YES  | P:%.4f S:%.1f Mom:%.3f Cash:%.2f", ts, self.name, exec_ap1, bought, momentum, self.cash)

        # Buy NO (only when momentum favors NO)
        if time_progress < self.time_cutoff and is_depth_valid and favor_no:
            if self.b_cnt_no < self.max_buy and no_ask_ws <= self.buy_p:
                if (1.0 - bp1) > 0.0 and (1.0 - bp1) <= self.buy_p:
                    target = self.cash * self.bullet_pct / exec_no_ask_p1
                    affordable = self.cash / (exec_no_ask_p1 * (1.0 + self.fee_rate))
                    bought = min(target, bs1, affordable)
                    if bought >= 1.0:
                        self.cash -= (bought * exec_no_ask_p1) * (1.0 + self.fee_rate)
                        self.inv_no += bought
                        self.b_cnt_no += 1
                        self.r_buy_count += 1
                        self.r_no_bought += bought
                        log.debug("[%s][%s] BUY NO   | P:%.4f S:%.1f Mom:%.3f Cash:%.2f", ts, self.name, exec_no_ask_p1, bought, momentum, self.cash)


# ================= TREND SCALP LOGIC =================
class TrendScalpSlot(StrategySlot):
    def process_tick(self, ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, momentum=0.0):
        if bs1 <= 0.0 or as1 <= 0.0:
            return
        exec_bp1 = max(0.0001, bp1 - self.slippage)
        exec_ap1 = min(0.9999, ap1 + self.slippage)
        no_ask_ws = 1.0 - ws_bid
        no_bid_ws = 1.0 - ws_ask
        exec_no_ask_p1 = min(0.9999, (1.0 - bp1) + self.slippage)
        exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - self.slippage)

        is_depth_valid = self._check_depth(bs1, as1)

        # Scalp sell YES (re-enables buy slot)
        if self.inv_yes >= 1.0 and bp1 > 0.0:
            if ws_bid >= self.sell_p:
                sold = min(self.inv_yes, bs1)
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                self.inv_yes -= sold
                self.b_cnt_yes = max(0, self.b_cnt_yes - 1)
                self.r_yes_sold += sold
                log.debug("[%s][%s] SCALP YES | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_bp1, sold, self.cash)

        # Scalp sell NO
        if self.inv_no >= 1.0 and (1.0 - ap1) > 0.0:
            if no_bid_ws >= self.sell_p:
                sold = min(self.inv_no, as1)
                self.cash += (sold * exec_no_bid_p1) * (1.0 - self.fee_rate)
                self.inv_no -= sold
                self.b_cnt_no = max(0, self.b_cnt_no - 1)
                self.r_no_sold += sold
                log.debug("[%s][%s] SCALP NO  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_no_bid_p1, sold, self.cash)

        # Buy with momentum
        if time_progress < self.time_cutoff and is_depth_valid:
            if momentum > 0.02 and self.b_cnt_yes < self.max_buy and ws_ask <= self.buy_p:
                if ap1 > 0.0 and ap1 <= self.buy_p:
                    target = self.cash * self.bullet_pct / exec_ap1
                    affordable = self.cash / (exec_ap1 * (1.0 + self.fee_rate))
                    bought = min(target, as1, affordable)
                    if bought >= 1.0:
                        self.cash -= (bought * exec_ap1) * (1.0 + self.fee_rate)
                        self.inv_yes += bought
                        self.b_cnt_yes += 1
                        self.r_buy_count += 1
                        self.r_yes_bought += bought
                        log.debug("[%s][%s] BUY YES  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_ap1, bought, self.cash)

            if momentum < -0.02 and self.b_cnt_no < self.max_buy and no_ask_ws <= self.buy_p:
                if (1.0 - bp1) > 0.0 and (1.0 - bp1) <= self.buy_p:
                    target = self.cash * self.bullet_pct / exec_no_ask_p1
                    affordable = self.cash / (exec_no_ask_p1 * (1.0 + self.fee_rate))
                    bought = min(target, bs1, affordable)
                    if bought >= 1.0:
                        self.cash -= (bought * exec_no_ask_p1) * (1.0 + self.fee_rate)
                        self.inv_no += bought
                        self.b_cnt_no += 1
                        self.r_buy_count += 1
                        self.r_no_bought += bought
                        log.debug("[%s][%s] BUY NO   | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_no_ask_p1, bought, self.cash)


# ================= STRATEGY: ACCEL_MOMENTUM =================
class AccelMomentumSlot(StrategySlot):
    """Requires both momentum AND acceleration in same direction for 60% accuracy."""

    def process_tick(self, ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, momentum=0.0, accel=0.0):
        if bs1 <= 0.0 or as1 <= 0.0:
            return
        exec_bp1 = max(0.0001, bp1 - self.slippage)
        exec_ap1 = min(0.9999, ap1 + self.slippage)
        no_ask_ws = 1.0 - ws_bid
        no_bid_ws = 1.0 - ws_ask
        exec_no_ask_p1 = min(0.9999, (1.0 - bp1) + self.slippage)
        exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - self.slippage)

        is_depth_valid = self._check_depth(bs1, as1)

        # Signal: both momentum and acceleration must agree
        favor_yes = (momentum > 0.02) and (accel > 0.01)
        favor_no = (momentum < -0.02) and (accel < -0.01)

        # Sell YES (take profit or late exit)
        if self.inv_yes >= 1.0:
            if ws_bid >= self.sell_p and bp1 >= self.sell_p:
                sold = min(self.inv_yes, bs1)
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                self.inv_yes -= sold
                self.r_yes_sold += sold
                log.debug("[%s][%s] SELL YES | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_bp1, sold, self.cash)
            elif time_progress > 0.85 and bp1 > 0.0:
                sold = min(self.inv_yes, bs1)
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                self.inv_yes -= sold
                self.r_yes_sold += sold
                log.debug("[%s][%s] EXIT YES | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_bp1, sold, self.cash)

        # Sell NO
        if self.inv_no >= 1.0:
            if no_bid_ws >= self.sell_p and (1.0 - ap1) >= self.sell_p:
                sold = min(self.inv_no, as1)
                self.cash += (sold * exec_no_bid_p1) * (1.0 - self.fee_rate)
                self.inv_no -= sold
                self.r_no_sold += sold
                log.debug("[%s][%s] SELL NO  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_no_bid_p1, sold, self.cash)
            elif time_progress > 0.85 and (1.0 - ap1) > 0.0:
                sold = min(self.inv_no, as1)
                self.cash += (sold * exec_no_bid_p1) * (1.0 - self.fee_rate)
                self.inv_no -= sold
                self.r_no_sold += sold
                log.debug("[%s][%s] EXIT NO  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_no_bid_p1, sold, self.cash)

        # Buy YES (only when momentum+accel both favor YES)
        if time_progress < self.time_cutoff and is_depth_valid and favor_yes:
            if self.b_cnt_yes < self.max_buy and ws_ask <= self.buy_p:
                if ap1 > 0.0 and ap1 <= self.buy_p:
                    target = self.cash * self.bullet_pct / exec_ap1
                    affordable = self.cash / (exec_ap1 * (1.0 + self.fee_rate))
                    bought = min(target, as1, affordable)
                    if bought >= 1.0:
                        self.cash -= (bought * exec_ap1) * (1.0 + self.fee_rate)
                        self.inv_yes += bought
                        self.b_cnt_yes += 1
                        self.r_buy_count += 1
                        self.r_yes_bought += bought
                        log.debug("[%s][%s] BUY YES  | P:%.4f S:%.1f M:%.3f A:%.3f Cash:%.2f",
                                  ts, self.name, exec_ap1, bought, momentum, accel, self.cash)

        # Buy NO (only when momentum+accel both favor NO)
        if time_progress < self.time_cutoff and is_depth_valid and favor_no:
            if self.b_cnt_no < self.max_buy and no_ask_ws <= self.buy_p:
                if (1.0 - bp1) > 0.0 and (1.0 - bp1) <= self.buy_p:
                    target = self.cash * self.bullet_pct / exec_no_ask_p1
                    affordable = self.cash / (exec_no_ask_p1 * (1.0 + self.fee_rate))
                    bought = min(target, bs1, affordable)
                    if bought >= 1.0:
                        self.cash -= (bought * exec_no_ask_p1) * (1.0 + self.fee_rate)
                        self.inv_no += bought
                        self.b_cnt_no += 1
                        self.r_buy_count += 1
                        self.r_no_bought += bought
                        log.debug("[%s][%s] BUY NO   | P:%.4f S:%.1f M:%.3f A:%.3f Cash:%.2f",
                                  ts, self.name, exec_no_ask_p1, bought, momentum, accel, self.cash)


# ================= STRATEGY: ACCEL_MOMENTUM_L2 =================
class AccelMomentumL2Slot(AccelMomentumSlot):
    """Walks L2 order book for better fills on entries."""

    def process_tick(self, ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, momentum=0.0, accel=0.0, l2=None):
        if bs1 <= 0.0 or as1 <= 0.0:
            return
        exec_bp1 = max(0.0001, bp1 - self.slippage)
        no_ask_ws = 1.0 - ws_bid
        no_bid_ws = 1.0 - ws_ask
        exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - self.slippage)

        is_depth_valid = self._check_depth(bs1, as1)

        favor_yes = (momentum > 0.02) and (accel > 0.01)
        favor_no = (momentum < -0.02) and (accel < -0.01)

        # Sell YES (same as parent — L1 only for sells)
        if self.inv_yes >= 1.0:
            if ws_bid >= self.sell_p and bp1 >= self.sell_p:
                sold = min(self.inv_yes, bs1)
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                self.inv_yes -= sold
                self.r_yes_sold += sold
                log.debug("[%s][%s] SELL YES | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_bp1, sold, self.cash)
            elif time_progress > 0.85 and bp1 > 0.0:
                sold = min(self.inv_yes, bs1)
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                self.inv_yes -= sold
                self.r_yes_sold += sold

        # Sell NO
        if self.inv_no >= 1.0:
            if no_bid_ws >= self.sell_p and (1.0 - ap1) >= self.sell_p:
                sold = min(self.inv_no, as1)
                self.cash += (sold * exec_no_bid_p1) * (1.0 - self.fee_rate)
                self.inv_no -= sold
                self.r_no_sold += sold
            elif time_progress > 0.85 and (1.0 - ap1) > 0.0:
                sold = min(self.inv_no, as1)
                self.cash += (sold * exec_no_bid_p1) * (1.0 - self.fee_rate)
                self.inv_no -= sold
                self.r_no_sold += sold

        # Buy YES — walk L2 book
        if time_progress < self.time_cutoff and is_depth_valid and favor_yes:
            if self.b_cnt_yes < self.max_buy and ws_ask <= self.buy_p:
                remaining = self.cash * self.bullet_pct
                total_bought = 0.0
                total_cost = 0.0
                levels = [(ap1, as1)]
                if l2:
                    levels.append((l2.get('a_p2', 0.0), l2.get('a_s2', 0.0)))
                    levels.append((l2.get('a_p3', 0.0), l2.get('a_s3', 0.0)))
                for lp, ls in levels:
                    if lp <= 0.0 or lp > self.buy_p or ls <= 0.0 or remaining <= 0.0:
                        continue
                    exec_p = min(0.9999, lp + self.slippage)
                    can_buy = remaining / (exec_p * (1.0 + self.fee_rate))
                    take = min(can_buy, ls)
                    if take >= 1.0:
                        cost = take * exec_p * (1.0 + self.fee_rate)
                        total_bought += take
                        total_cost += cost
                        remaining -= cost
                if total_bought >= 1.0 and self.cash >= total_cost:
                    self.inv_yes += total_bought
                    self.cash -= total_cost
                    self.b_cnt_yes += 1
                    self.r_buy_count += 1
                    self.r_yes_bought += total_bought
                    log.debug("[%s][%s] BUY YES L2 | S:%.1f Cash:%.2f", ts, self.name, total_bought, self.cash)

        # Buy NO — walk L2 book
        if time_progress < self.time_cutoff and is_depth_valid and favor_no:
            if self.b_cnt_no < self.max_buy and no_ask_ws <= self.buy_p:
                remaining = self.cash * self.bullet_pct
                total_bought = 0.0
                total_cost = 0.0
                levels = [(bp1, bs1)]
                if l2:
                    levels.append((l2.get('b_p2', 0.0), l2.get('b_s2', 0.0)))
                    levels.append((l2.get('b_p3', 0.0), l2.get('b_s3', 0.0)))
                for lp_raw, ls in levels:
                    no_price = 1.0 - lp_raw
                    if no_price <= 0.0 or no_price > self.buy_p or ls <= 0.0 or remaining <= 0.0:
                        continue
                    exec_p = min(0.9999, no_price + self.slippage)
                    can_buy = remaining / (exec_p * (1.0 + self.fee_rate))
                    take = min(can_buy, ls)
                    if take >= 1.0:
                        cost = take * exec_p * (1.0 + self.fee_rate)
                        total_bought += take
                        total_cost += cost
                        remaining -= cost
                if total_bought >= 1.0 and self.cash >= total_cost:
                    self.inv_no += total_bought
                    self.cash -= total_cost
                    self.b_cnt_no += 1
                    self.r_buy_count += 1
                    self.r_no_bought += total_bought
                    log.debug("[%s][%s] BUY NO  L2 | S:%.1f Cash:%.2f", ts, self.name, total_bought, self.cash)


# ================= STRATEGY: FAV_CONVERGENCE (validated +EV edge) =================
class FavConvergenceSlot(StrategySlot):
    """Buy the FAVORITE side (price in [buy_p, sell_p]) in the late window and HOLD
    to settlement. Exploits the measured favorite-longshot bias (favorites are
    underpriced ~2-4c). No take-profit, no stop: convergence to 1.0 is the thesis.

    Reinterpreted genome params:
      buy_p       -> band LOW   (e.g. 0.70)   sell_p -> band HIGH (e.g. 0.93)
      time_cutoff -> EARLIEST entry progress (only buy when time_progress >= this)
    """

    def process_tick(self, ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, momentum=0.0):
        if bs1 <= 0.0 or as1 <= 0.0:
            return

        # Reversal stop-loss (runs any time we hold): exit if our side fell below stop_p
        # (it is no longer the favorite -> the edge has flipped against us). Cuts the upset tail.
        if self.stop_p > 0.0:
            if self.inv_yes >= 1.0 and ws_bid < self.stop_p:
                exec_bp1 = max(0.0001, bp1 - self.slippage)
                sold = min(self.inv_yes, bs1)
                self.cash += (sold * exec_bp1) * (1.0 - self.fee_rate)
                self.inv_yes -= sold
                self.r_yes_sold += sold
                log.debug("[%s][%s] STOP FAV YES | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_bp1, sold, self.cash)
            if self.inv_no >= 1.0 and (1.0 - ws_ask) < self.stop_p:
                exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - self.slippage)
                sold = min(self.inv_no, as1)
                self.cash += (sold * exec_no_bid_p1) * (1.0 - self.fee_rate)
                self.inv_no -= sold
                self.r_no_sold += sold
                log.debug("[%s][%s] STOP FAV NO  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_no_bid_p1, sold, self.cash)

        # Late window only (note inverted meaning of time_cutoff for this strategy)
        if time_progress < self.time_cutoff:
            return
        if not self._check_depth(bs1, as1):
            return

        # Buy YES if YES is the favorite and inside the band.
        # Cap TOTAL entries (yes+no) per market so we never flip sides (validated: single entry).
        if (self.b_cnt_yes + self.b_cnt_no) < self.max_buy and ap1 > 0.0:
            yes_ask = ap1
            if self.buy_p <= yes_ask <= self.sell_p:
                exec_p = min(0.9999, yes_ask + self.slippage)
                target = self.cash * self.bullet_pct / exec_p
                affordable = self.cash / (exec_p * (1.0 + self.fee_rate))
                bought = min(target, as1, affordable)
                if bought >= 1.0:
                    self.cash -= (bought * exec_p) * (1.0 + self.fee_rate)
                    self.inv_yes += bought
                    self.b_cnt_yes += 1
                    self.r_buy_count += 1
                    self.r_yes_bought += bought
                    log.debug("[%s][%s] BUY FAV YES | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_p, bought, self.cash)

        # Buy NO if NO is the favorite and inside the band (NO ask = 1 - YES bid)
        if (self.b_cnt_yes + self.b_cnt_no) < self.max_buy and bp1 > 0.0:
            no_ask = 1.0 - bp1
            if self.buy_p <= no_ask <= self.sell_p:
                exec_p = min(0.9999, no_ask + self.slippage)
                target = self.cash * self.bullet_pct / exec_p
                affordable = self.cash / (exec_p * (1.0 + self.fee_rate))
                bought = min(target, bs1, affordable)
                if bought >= 1.0:
                    self.cash -= (bought * exec_p) * (1.0 + self.fee_rate)
                    self.inv_no += bought
                    self.b_cnt_no += 1
                    self.r_buy_count += 1
                    self.r_no_bought += bought
                    log.debug("[%s][%s] BUY FAV NO  | P:%.4f S:%.1f Cash:%.2f", ts, self.name, exec_p, bought, self.cash)


# ================= PORTFOLIO MANAGER =================
SLOT_CLASSES = {
    "swing_dual": SwingDualSlot,
    "momentum_dual": MomentumDualSlot,
    "trend_scalp": TrendScalpSlot,
    "accel_momentum": AccelMomentumSlot,
    "accel_momentum_l2": AccelMomentumL2Slot,
    "accel_momentum_l2_wide": AccelMomentumL2Slot,
    "swing_stop": SwingStopSlot,
    "fav_convergence": FavConvergenceSlot,
    "fav_convergence_late": FavConvergenceSlot,
}

class PortfolioManager:
    """Runs multiple strategies simultaneously with split capital."""

    def __init__(self, portfolio_file="genome_portfolio.json"):
        with open(portfolio_file, 'r') as f:
            config = json.load(f)

        total_capital = 1000.0
        self.slots = []
        self.bid_history = deque(maxlen=2000)
        self.round_count = 0

        for strat in config["strategies"]:
            name = strat["name"]
            weight = strat["weight"]
            capital = total_capital * weight
            genome_file = f"genome_{name}.json"

            if not os.path.exists(genome_file):
                log.warning("[PORTFOLIO] Missing %s, skipping", genome_file)
                continue

            with open(genome_file, 'r') as f:
                genome = json.load(f)

            cls = SLOT_CLASSES.get(name)
            if cls is None:
                log.warning("[PORTFOLIO] Unknown strategy: %s, skipping", name)
                continue

            slot = cls(name, genome, capital)
            self.slots.append(slot)
            log.info("[PORTFOLIO] Loaded %s (w=%.0f%%, capital=$%.0f, buy_p=%.3f, sell_p=%.3f)",
                     name, weight * 100, capital, slot.buy_p, slot.sell_p)

        self.min_early_depth = config.get("min_early_depth", 300.0)
        self.min_tick_rate = config.get("min_tick_rate", 5.0)  # ticks/sec; ~4500 ticks/market
        self.depth_check_ticks = 0
        self.depth_check_sum = 0.0
        self.depth_check_samples = 50  # check first 50 ticks
        self.market_approved = False
        self.gate_start_time = 0.0

        self.peak_cash = total_capital
        self.dd_threshold = config.get("dd_threshold", 0.40)  # 40% drawdown triggers reduction

        # ---- Risk governor (prevents correlated wipeouts) ----
        self.initial_capital = total_capital
        self.kill_switch_dd = config.get("kill_switch_dd", 0.25)    # halt ALL new entries after 25% session drawdown
        self.round_loss_limit = config.get("round_loss_limit", 0.03)  # stop entries once a single round is down 3% of equity
        self.min_capital = config.get("min_capital", 50.0)         # below this we are effectively dead -> halt, log loudly
        self.killed = False            # sticky: tripped kill switch
        self.halted_logged = False     # sticky: logged the depletion halt once
        self._round_entry_block = False
        self.round_start_equity = total_capital

        log.info("[PORTFOLIO] %d strategies active. Total capital: $%.0f | MinEarlyDepth: %.0f | DD_Thresh: %.0f%% | KillDD: %.0f%% | RoundStop: %.0f%%",
                 len(self.slots), total_capital, self.min_early_depth, self.dd_threshold * 100,
                 self.kill_switch_dd * 100, self.round_loss_limit * 100)

    def _update_risk(self):
        """Adjust position sizing based on drawdown from peak."""
        current = self.total_cash()
        self.peak_cash = max(self.peak_cash, current)
        if self.peak_cash > 0:
            dd = (self.peak_cash - current) / self.peak_cash
            if dd > self.dd_threshold:
                # Halve position sizes during drawdown (relative to each strategy's base)
                for s in self.slots:
                    s.bullet_pct = s.base_bullet_pct * 0.5
            else:
                for s in self.slots:
                    s.bullet_pct = s.base_bullet_pct

    def reset_all(self):
        self.bid_history.clear()
        self.depth_check_ticks = 0
        self.depth_check_sum = 0.0
        self.market_approved = False
        self.gate_start_time = 0.0
        self._update_risk()
        self.round_start_equity = self.total_cash()  # positions are flat post-settle, so equity == cash here
        self._round_entry_block = False
        for s in self.slots:
            s.reset_market_state()

    def smart_settle_all(self, ws_bid, ws_ask):
        self.round_count += 1
        total_pnl = 0.0

        summary_lines = [
            f"\n{'='*70}",
            f"[ROUND {self.round_count}] 5-Min Market Settled (Smart Contract)",
            f"{'='*70}",
        ]

        for s in self.slots:
            result = s.smart_settle(ws_bid, ws_ask)
            total_pnl += result['round_pnl']
            s.reset_market_state()

            summary_lines.append(
                f"  [{result['name']:16s}] Winner: {result['winner']} | "
                f"Buys: {result['buy_count']:2d}/{result['max_buy_slots']} | "
                f"YES: +{result['yes_bought']:.1f} -{result['yes_sold']:.1f} held:{result['yes_held']:.1f} | "
                f"NO: +{result['no_bought']:.1f} -{result['no_sold']:.1f} held:{result['no_held']:.1f} | "
                f"PnL: ${result['round_pnl']:+.2f} | Cash: ${result['cash']:.2f}"
            )

        total_cash = sum(s.cash for s in self.slots)
        summary_lines.append(f"{'─'*70}")
        summary_lines.append(f"  PORTFOLIO | Round PnL: ${total_pnl:+.2f} | Total Cash: ${total_cash:.2f}")
        summary_lines.append(f"{'='*70}\n")

        log.info('\n'.join(summary_lines))
        self.bid_history.clear()

    def process_tick(self, ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, l2=None):
        self.bid_history.append(ws_bid)

        # Market quality gate: check depth + tick rate in first N ticks
        if not self.market_approved:
            if self.depth_check_ticks == 0:
                self.gate_start_time = time.time()
            self.depth_check_ticks += 1
            self.depth_check_sum += (bs1 + as1)
            if self.depth_check_ticks == self.depth_check_samples:
                avg_depth = self.depth_check_sum / self.depth_check_ticks
                elapsed = time.time() - self.gate_start_time
                tick_rate = self.depth_check_ticks / max(0.01, elapsed)
                depth_ok = avg_depth >= self.min_early_depth
                rate_ok = tick_rate >= self.min_tick_rate
                # Always approve — individual strategies have their own depth filters
                # The gate just logs quality for monitoring
                self.market_approved = True
                if depth_ok and rate_ok:
                    log.info("[PORTFOLIO] Market APPROVED (depth: %.0f, rate: %.1f t/s)", avg_depth, tick_rate)
                else:
                    log.info("[PORTFOLIO] Market APPROVED* (depth: %.0f/%s, rate: %.1f/%s)",
                             avg_depth, "OK" if depth_ok else "LOW",
                             tick_rate, "OK" if rate_ok else "SLOW")
            if not self.market_approved:
                return  # still collecting quality check samples

        # Calculate momentum from bid history
        momentum = 0.0
        accel = 0.0
        lookback = 130  # ~2.7% of 4800 ticks (matching optimized lookback_frac)
        half_lb = lookback // 2
        if len(self.bid_history) > lookback:
            momentum = ws_bid - self.bid_history[-lookback]
            if len(self.bid_history) > lookback + half_lb:
                early_vel = self.bid_history[-half_lb] - self.bid_history[-lookback]
                late_vel = ws_bid - self.bid_history[-half_lb]
                accel = late_vel - early_vel

        # Conviction-based scaling: wide spread + strong momentum = bet bigger
        spread = ws_ask - ws_bid
        strong_momentum = abs(momentum) > 0.04
        wide_spread = spread > 0.025
        if wide_spread and strong_momentum:
            conviction_mult = 1.5  # 50% bigger bets on high-conviction setups
        else:
            conviction_mult = 1.0

        # ================= RISK GOVERNOR =================
        # Three sticky/transient circuit breakers. Each only blocks NEW ENTRIES;
        # exits (take-profit / stop / settlement) always remain enabled so we can de-risk.
        equity = self.total_value(ws_bid, ws_ask)
        self.peak_cash = max(self.peak_cash, equity)
        allow_entries = not self.killed

        # 1. Depletion halt: capital gone -> stop entering, log once. Fixes the silent $0.00 flatline.
        if self.total_cash() < self.min_capital:
            if not self.halted_logged:
                log.info("[RISK] CAPITAL DEPLETED (<$%.0f, cash=$%.2f). Halting new entries; exits only.",
                         self.min_capital, self.total_cash())
                self.halted_logged = True
            allow_entries = False

        # 2. Session kill switch: drawdown from peak equity exceeds threshold -> stop for good.
        if self.peak_cash > 0 and (self.peak_cash - equity) / self.peak_cash > self.kill_switch_dd:
            if not self.killed:
                log.info("[RISK] KILL SWITCH TRIPPED: session drawdown %.1f%% > %.0f%%. New entries DISABLED.",
                         (self.peak_cash - equity) / self.peak_cash * 100, self.kill_switch_dd * 100)
                self.killed = True
            allow_entries = False

        # 3. Per-round stop-loss: this single market is already losing too much -> stop adding to it.
        #    This is the direct fix for correlated -$300 rounds where all 5 slots pile the same wrong side.
        if self.round_start_equity > 0:
            round_loss_frac = (self.round_start_equity - equity) / self.round_start_equity
            if round_loss_frac > self.round_loss_limit:
                if not self._round_entry_block:
                    log.debug("[RISK] Round stop-loss hit (-%.1f%%). No new entries this market.", round_loss_frac * 100)
                    self._round_entry_block = True
                allow_entries = False

        for s in self.slots:
            # Apply conviction scaling temporarily; force size to 0 when entries are blocked (exits still fire).
            # Conviction scaling is a momentum-era hack and is NOT part of the validated FAV strategy.
            base_pct = s.bullet_pct
            cm = 1.0 if isinstance(s, FavConvergenceSlot) else conviction_mult
            s.bullet_pct = (base_pct * cm) if allow_entries else 0.0
            if isinstance(s, AccelMomentumL2Slot):
                s.process_tick(ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, momentum, accel, l2=l2)
            elif isinstance(s, AccelMomentumSlot):
                s.process_tick(ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, momentum, accel)
            else:
                s.process_tick(ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, momentum)
            s.bullet_pct = base_pct

    def total_cash(self):
        return sum(s.cash for s in self.slots)

    def total_value(self, ws_bid, ws_ask):
        return sum(s.est_value(ws_bid, ws_ask) for s in self.slots)


# ================= SINGLE STRATEGY MODE (backward compat) =================
class SingleStrategyManager:
    """Wraps a single SwingDualSlot for backward compatibility."""

    def __init__(self, genome_file):
        with open(genome_file, 'r') as f:
            genome = json.load(f)
        name = genome.get('strategy', 'swing_dual')
        cls = SLOT_CLASSES.get(name, SwingDualSlot)
        self.slots = [cls(name, genome, 1000.0)]
        self.bid_history = deque(maxlen=2000)
        self.round_count = 0

        log.info("[SINGLE] Loaded %s (capital=$1000, buy_p=%.3f, sell_p=%.3f)",
                 name, self.slots[0].buy_p, self.slots[0].sell_p)

    def reset_all(self):
        self.bid_history.clear()
        for s in self.slots:
            s.reset_market_state()

    def smart_settle_all(self, ws_bid, ws_ask):
        self.round_count += 1
        s = self.slots[0]
        result = s.smart_settle(ws_bid, ws_ask)
        s.reset_market_state()

        summary = (
            f"\n{'─'*60}\n"
            f"[ROUND {self.round_count}] 5-Min Market Settled (Smart Contract)\n"
            f"Strategy     : {result['strategy']}\n"
            f"Winner       : {result['winner']}\n"
            f"Buy Count    : {result['buy_count']} / {result['max_buy_slots']}\n"
            f"YES Tokens   : Bought {result['yes_bought']:.1f} | Sold {result['yes_sold']:.1f} | Held: {result['yes_held']:.1f}\n"
            f"NO Tokens    : Bought {result['no_bought']:.1f}  | Sold {result['no_sold']:.1f} | Held: {result['no_held']:.1f}\n"
            f"Round PnL    : ${result['round_pnl']:+.2f}\n"
            f"Total Cash   : ${result['cash']:.2f}\n"
            f"{'─'*60}\n"
        )
        log.info(summary)
        self.bid_history.clear()

    def process_tick(self, ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress):
        self.bid_history.append(ws_bid)
        momentum = 0.0
        accel = 0.0
        if len(self.bid_history) > 130:
            momentum = ws_bid - self.bid_history[-130]
            if len(self.bid_history) > 195:
                early_vel = self.bid_history[-65] - self.bid_history[-130]
                late_vel = ws_bid - self.bid_history[-65]
                accel = late_vel - early_vel
        for s in self.slots:
            if isinstance(s, AccelMomentumSlot):
                s.process_tick(ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, momentum, accel)
            else:
                s.process_tick(ws_bid, ws_ask, bp1, bs1, ap1, as1, ts, time_progress, momentum)

    def total_cash(self):
        return self.slots[0].cash

    def total_value(self, ws_bid, ws_ask):
        return self.slots[0].est_value(ws_bid, ws_ask)


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
    candidates = ["btc-updown-5m-{}".format(base), "btc-updown-5m-{}".format(base + 300)]
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
    for m in markets:
        if not m.get("closed") and m.get("active"):
            clob_ids = m.get("clobTokenIds", [])
            if isinstance(clob_ids, str):
                clob_ids = clob_ids.replace("[", "").replace("]", "").replace('"', "").replace("'", "").split(",")
            if isinstance(clob_ids, list) and len(clob_ids) >= 1:
                return [clob_ids[0]]
    return []

async def update_l2_cache(session, tokens):
    while True:
        tasks = [_http_get_json(session, CLOB_BOOK.format(t), timeout=3) for t in tokens]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for t, data in zip(tokens, results):
            if isinstance(data, dict):
                record = {
                    'b_p1': 0.0, 'b_s1': 0.0, 'a_p1': 0.0, 'a_s1': 0.0,
                    'b_p2': 0.0, 'b_s2': 0.0, 'a_p2': 0.0, 'a_s2': 0.0,
                    'b_p3': 0.0, 'b_s3': 0.0, 'a_p3': 0.0, 'a_s3': 0.0,
                }
                if "bids" in data and len(data["bids"]) > 0:
                    bids = sorted(data["bids"], key=lambda x: float(x["price"]), reverse=True)
                    for lvl in range(min(3, len(bids))):
                        record[f'b_p{lvl+1}'] = float(bids[lvl]["price"])
                        record[f'b_s{lvl+1}'] = float(bids[lvl]["size"])
                if "asks" in data and len(data["asks"]) > 0:
                    asks = sorted(data["asks"], key=lambda x: float(x["price"]))
                    for lvl in range(min(3, len(asks))):
                        record[f'a_p{lvl+1}'] = float(asks[lvl]["price"])
                        record[f'a_s{lvl+1}'] = float(asks[lvl]["size"])
                L2_CACHE[t] = record
        await asyncio.sleep(L2_REFRESH_INTERVAL)

# ================= MAIN LOOP =================
async def monitor_strategy(manager):
    msg_counter, backoff = 0, RECONNECT_BASE
    empty_l2 = {
        'b_p1': 0.0, 'b_s1': 0.0, 'a_p1': 0.0, 'a_s1': 0.0,
        'b_p2': 0.0, 'b_s2': 0.0, 'a_p2': 0.0, 'a_s2': 0.0,
        'b_p3': 0.0, 'b_s3': 0.0, 'a_p3': 0.0, 'a_s3': 0.0,
    }
    connector = aiohttp.TCPConnector(ssl=False)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname, ssl_context.verify_mode = False, ssl.CERT_NONE

    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla", "Accept-Encoding": "gzip, deflate"}, connector=connector) as session:
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

            manager.reset_all()
            l2_task = asyncio.ensure_future(update_l2_cache(session, tokens))
            last_known_bids, last_known_asks = {}, {}

            try:
                async with websockets.connect(WS_URI, ssl=ssl_context, ping_interval=WS_PING_INTERVAL, ping_timeout=WS_PING_TIMEOUT) as websocket:
                    await websocket.send(json.dumps({"assets_ids": tokens, "type": "market", "custom_feature_enabled": True}))
                    while True:
                        res = await asyncio.wait_for(websocket.recv(), timeout=WS_RECV_TIMEOUT)
                        data = json.loads(res)
                        now_dt = datetime.now()
                        ms_ts = now_dt.strftime("%H:%M:%S.%f")[:-3]
                        rem_f = end_timestamp - now_dt.timestamp()
                        time_progress = max(0.0, min(1.0, 1.0 - (rem_f / 300.0)))

                        if rem_f <= 0:
                            for t_id in tokens:
                                if t_id in last_known_bids:
                                    manager.smart_settle_all(last_known_bids[t_id], last_known_asks[t_id])
                                    break
                            break

                        items = data if isinstance(data, list) else [data]
                        for item in items:
                            if item.get("event_type") not in ("price_change", "best_bid_ask"):
                                continue
                            t_id_full = item.get("asset_id", "")
                            ws_ask = float(item.get("best_ask")) if item.get("best_ask") not in (None, "none") else 0.0
                            ws_bid = float(item.get("best_bid")) if item.get("best_bid") not in (None, "none") else 0.0
                            last_known_bids[t_id_full], last_known_asks[t_id_full] = ws_bid, ws_ask

                            if t_id_full and ws_ask > 0 and ws_bid > 0:
                                l2 = L2_CACHE.get(t_id_full, empty_l2)
                                manager.process_tick(ws_bid, ws_ask, l2['b_p1'], l2['b_s1'], l2['a_p1'], l2['a_s1'], ms_ts, time_progress, l2=l2)
                                msg_counter += 1
                                if msg_counter % PRINT_EVERY_N == 0:
                                    log.debug("[TICK] %s | P: %.0f%% | Bid: %.3f | Ask: %.3f | BS: %.1f | AS: %.1f | PV: $%.2f",
                                             ms_ts, time_progress*100, ws_bid, ws_ask, l2['b_s1'], l2['a_s1'], manager.total_value(ws_bid, ws_ask))
            except Exception as e:
                log.debug("[ERROR] WS Exception: %s", e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_CAP)
            finally:
                l2_task.cancel()

if __name__ == "__main__":
    # Usage:
    #   python l.py                        -> Portfolio mode (3 strategies)
    #   python l.py genome_swing_dual.json -> Single strategy mode (backward compat)
    if len(sys.argv) > 1:
        genome_file = sys.argv[1]
        log.info("=== Polymarket Paper Trading Engine (Single Strategy) ===")
        mgr = SingleStrategyManager(genome_file)
    else:
        log.info("=== Polymarket Paper Trading Engine (Portfolio Mode) ===")
        mgr = PortfolioManager()

    try:
        asyncio.run(monitor_strategy(mgr))
    except KeyboardInterrupt:
        log.info("\n[EXIT] Process terminated. Final cash: $%.2f", mgr.total_cash())
