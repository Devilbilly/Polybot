import pandas as pd
import numpy as np
import glob
import os
import argparse
import optuna
import json
from numba import njit, prange

# ===================== [GLOBAL DATA LOADER] =====================
def load_data_to_numpy(data_dir="market_data"):
    search_path = os.path.join(data_dir, "Token_*.csv")
    files = sorted(glob.glob(search_path))
    if not files:
        print(f"[-] Error: No data found in '{data_dir}'. Please check the path.")
        exit(1)

    print(f"[*] Compiling & Pre-loading {len(files)} files from '{data_dir}' into memory...")
    data_list = []
    names_list = []
    l1_cols = ['WS_Bid', 'WS_Ask', 'Bid_P1', 'Bid_S1', 'Ask_P1', 'Ask_S1']
    l2_cols = ['Bid_P2', 'Bid_S2', 'Ask_P2', 'Ask_S2', 'Bid_P3', 'Bid_S3', 'Ask_P3', 'Ask_S3']
    for f in files:
        df = pd.read_csv(f).fillna(0.0)
        # Load L1 (always available) + L2/L3 (if available, else zeros)
        cols = l1_cols.copy()
        for c in l2_cols:
            if c not in df.columns:
                df[c] = 0.0
            cols.append(c)
        # Look-ahead-free time base (col index 14): real seconds remaining if logged,
        # else -1.0 sentinel (sim falls back to tick-fraction for legacy reconstructed data).
        df['__rem__'] = df['Rem_Float'] if 'Rem_Float' in df.columns else -1.0
        cols.append('__rem__')
        data = df[cols].values.astype(np.float64)
        data_list.append(data)
        names_list.append(os.path.basename(f).split('_')[1].split('.')[0])

    return data_list, names_list

# ===================== [CORE ENGINE - NUMBA JIT] =====================
@njit(nogil=True)
def fast_simulate(data, strat_idx, buy_p, sell_p, max_buy, stop_p, fee_rate, slippage, time_cutoff, depth_threshold):
    cap = 1000.0
    if strat_idx == 3: 
        cash, inv_yes, inv_no = 0.0, 1000.0, 1000.0
    else:
        cash, inv_yes, inv_no = cap, 0.0, 0.0
        
    b_cnt = 0
    has_stopped = False 
    
    b_cnt_yes = 0
    b_cnt_no = 0
    has_stopped_yes = False
    has_stopped_no = False
    
    # [反釣魚升級] 深度穩定計數器
    depth_stable_ticks = 0 
    
    bullet_usd = 50.0 # Fixed position sizing per trade
    total_ticks = len(data)

    for i in range(total_ticks):
        ws_bid = data[i, 0]; ws_ask = data[i, 1]
        bp1    = data[i, 2]; bs1    = data[i, 3] 
        ap1    = data[i, 4]; as1    = data[i, 5] 

        exec_bp1 = max(0.0001, bp1 - slippage) 
        exec_ap1 = min(0.9999, ap1 + slippage) 
        
        # Calculate time progress (0.0 to 1.0) from REAL seconds remaining (no look-ahead).
        # Falls back to tick-fraction only for legacy data lacking a Rem_Float column.
        rem = data[i, 14]
        if rem >= 0.0:
            time_progress = min(1.0, max(0.0, 1.0 - rem / 300.0))
        else:
            time_progress = i / total_ticks
        
        # ==========================================
        # 核心計算：訂單簿總深度 (Total L2 Depth) 與 穩定度
        # ==========================================
        l2_total_depth = bs1 + as1
        if l2_total_depth >= depth_threshold:
            depth_stable_ticks += 1
        else:
            depth_stable_ticks = 0
            
        # 必須連續 15 個 Ticks (約 2~3 秒) 深度都達標，才視為真實造市商防守
        is_depth_valid = (depth_stable_ticks >= 15)

        # ---------------------------------------------------------
        # Strategy 5: SWING_DUAL
        # ---------------------------------------------------------
        if strat_idx == 5:
            no_ask_ws = 1.0 - ws_bid
            no_bid_ws = 1.0 - ws_ask
            
            exec_no_ask_p1 = min(0.9999, (1.0 - bp1) + slippage) 
            exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - slippage) 

            # 1. Take Profit / Stop Loss (YES) - Can execute anytime
            if inv_yes >= 10.0:
                if ws_bid >= sell_p and bp1 >= sell_p and bs1 > 0.0:
                    sold = min(inv_yes, bs1)
                    rev = sold * exec_bp1
                    inv_yes -= sold
                    cash += rev * (1.0 - fee_rate)
                elif ws_bid <= stop_p and bp1 > 0.0 and bs1 > 0.0:
                    sold = min(inv_yes, bs1)
                    rev = sold * exec_bp1
                    inv_yes -= sold
                    cash += rev * (1.0 - fee_rate)
                    has_stopped_yes = True 

            # 2. Take Profit / Stop Loss (NO) - Can execute anytime
            if inv_no >= 10.0:
                if no_bid_ws >= sell_p and (1.0 - ap1) >= sell_p and as1 > 0.0:
                    sold = min(inv_no, as1)
                    rev = sold * exec_no_bid_p1
                    inv_no -= sold
                    cash += rev * (1.0 - fee_rate)
                elif no_bid_ws <= stop_p and (1.0 - ap1) > 0.0 and as1 > 0.0:
                    sold = min(inv_no, as1)
                    rev = sold * exec_no_bid_p1
                    inv_no -= sold
                    cash += rev * (1.0 - fee_rate)
                    has_stopped_no = True 

            # 3. Buy YES (Protected by time_cutoff AND is_depth_valid)
            if time_progress < time_cutoff and is_depth_valid:
                if not has_stopped_yes and b_cnt_yes < max_buy and ws_ask <= buy_p:
                    if ap1 > 0.0 and ap1 <= buy_p and as1 > 0.0:
                        target_amount = bullet_usd / exec_ap1
                        max_affordable = cash / (exec_ap1 * (1.0 + fee_rate))
                        bought = min(target_amount, min(as1, max_affordable)) 
                        
                        if bought >= 1.0:
                            cost = bought * exec_ap1
                            inv_yes += bought
                            cash -= cost * (1.0 + fee_rate)
                            b_cnt_yes += 1

            # 4. Buy NO (Protected by time_cutoff AND is_depth_valid)
            if time_progress < time_cutoff and is_depth_valid:
                if not has_stopped_no and b_cnt_no < max_buy and no_ask_ws <= buy_p:
                    if (1.0 - bp1) > 0.0 and (1.0 - bp1) <= buy_p and bs1 > 0.0:
                        target_amount = bullet_usd / exec_no_ask_p1
                        max_affordable = cash / (exec_no_ask_p1 * (1.0 + fee_rate))
                        bought = min(target_amount, min(bs1, max_affordable))
                        
                        if bought >= 1.0:
                            cost = bought * exec_no_ask_p1
                            inv_no += bought
                            cash -= cost * (1.0 + fee_rate)
                            b_cnt_no += 1
                            
            continue

        # ---------------------------------------------------------
        # Strategy 6: MOMENTUM_DUAL
        # Uses early price momentum to go directional.
        # Waits for a lookback window, then buys the side
        # aligned with the detected trend.
        # ---------------------------------------------------------
        if strat_idx == 6:
            no_ask_ws = 1.0 - ws_bid
            no_bid_ws = 1.0 - ws_ask
            exec_no_ask_p1 = min(0.9999, (1.0 - bp1) + slippage)
            exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - slippage)

            # depth filter
            l2_total_depth = bs1 + as1
            if l2_total_depth >= depth_threshold:
                depth_stable_ticks += 1
            else:
                depth_stable_ticks = 0
            is_depth_valid = (depth_stable_ticks >= 15)

            # Momentum detection: use stop_p as lookback fraction (repurposed)
            # We track a simple price EMA via running sum
            lookback_frac = max(0.05, abs(stop_p))  # repurpose stop_p as lookback
            lookback_ticks = int(lookback_frac * total_ticks)

            momentum = 0.0
            if i >= lookback_ticks and lookback_ticks > 0:
                momentum = ws_bid - data[i - lookback_ticks, 0]  # current bid - past bid

            # Determine direction: positive momentum → favor YES, negative → favor NO
            favor_yes = (momentum > 0.02)
            favor_no = (momentum < -0.02)

            # 1. Take Profit / Stop Loss YES
            if inv_yes >= 10.0:
                if ws_bid >= sell_p and bp1 >= sell_p and bs1 > 0.0:
                    sold = min(inv_yes, bs1)
                    inv_yes -= sold
                    cash += (sold * exec_bp1) * (1.0 - fee_rate)
                elif time_progress > 0.85:  # late-market forced exit
                    if bp1 > 0.0 and bs1 > 0.0:
                        sold = min(inv_yes, bs1)
                        inv_yes -= sold
                        cash += (sold * exec_bp1) * (1.0 - fee_rate)

            # 2. Take Profit / Stop Loss NO
            if inv_no >= 10.0:
                if no_bid_ws >= sell_p and (1.0 - ap1) >= sell_p and as1 > 0.0:
                    sold = min(inv_no, as1)
                    inv_no -= sold
                    cash += (sold * exec_no_bid_p1) * (1.0 - fee_rate)
                elif time_progress > 0.85:
                    if (1.0 - ap1) > 0.0 and as1 > 0.0:
                        sold = min(inv_no, as1)
                        inv_no -= sold
                        cash += (sold * exec_no_bid_p1) * (1.0 - fee_rate)

            # 3. Buy YES (only when momentum favors YES)
            if time_progress < time_cutoff and is_depth_valid and favor_yes:
                if b_cnt_yes < max_buy and ws_ask <= buy_p:
                    if ap1 > 0.0 and ap1 <= buy_p and as1 > 0.0:
                        target_amount = bullet_usd / exec_ap1
                        max_affordable = cash / (exec_ap1 * (1.0 + fee_rate))
                        bought = min(target_amount, min(as1, max_affordable))
                        if bought >= 1.0:
                            cost = bought * exec_ap1
                            inv_yes += bought
                            cash -= cost * (1.0 + fee_rate)
                            b_cnt_yes += 1

            # 4. Buy NO (only when momentum favors NO)
            if time_progress < time_cutoff and is_depth_valid and favor_no:
                if b_cnt_no < max_buy and no_ask_ws <= buy_p:
                    if (1.0 - bp1) > 0.0 and (1.0 - bp1) <= buy_p and bs1 > 0.0:
                        target_amount = bullet_usd / exec_no_ask_p1
                        max_affordable = cash / (exec_no_ask_p1 * (1.0 + fee_rate))
                        bought = min(target_amount, min(bs1, max_affordable))
                        if bought >= 1.0:
                            cost = bought * exec_no_ask_p1
                            inv_no += bought
                            cash -= cost * (1.0 + fee_rate)
                            b_cnt_no += 1

            continue

        # ---------------------------------------------------------
        # Strategy 7: TREND_SCALP
        # Waits for strong momentum, enters with the trend,
        # uses a tighter sell_p for quick scalps. Can trade
        # multiple times per market.
        # ---------------------------------------------------------
        if strat_idx == 7:
            no_ask_ws = 1.0 - ws_bid
            no_bid_ws = 1.0 - ws_ask
            exec_no_ask_p1 = min(0.9999, (1.0 - bp1) + slippage)
            exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - slippage)

            # depth filter
            l2_total_depth = bs1 + as1
            if l2_total_depth >= depth_threshold:
                depth_stable_ticks += 1
            else:
                depth_stable_ticks = 0
            is_depth_valid = (depth_stable_ticks >= 10)

            lookback_ticks = max(10, int(abs(stop_p) * total_ticks))
            momentum = 0.0
            if i >= lookback_ticks:
                momentum = ws_bid - data[i - lookback_ticks, 0]

            # Scalp: sell YES when profitable (lower threshold than swing)
            if inv_yes >= 10.0 and bp1 > 0.0 and bs1 > 0.0:
                if ws_bid >= sell_p:
                    sold = min(inv_yes, bs1)
                    inv_yes -= sold
                    cash += (sold * exec_bp1) * (1.0 - fee_rate)
                    has_stopped_yes = False  # allow re-entry
                    b_cnt_yes = max(0, b_cnt_yes - 1)  # refund a buy slot

            if inv_no >= 10.0 and (1.0 - ap1) > 0.0 and as1 > 0.0:
                if no_bid_ws >= sell_p:
                    sold = min(inv_no, as1)
                    inv_no -= sold
                    cash += (sold * exec_no_bid_p1) * (1.0 - fee_rate)
                    has_stopped_no = False
                    b_cnt_no = max(0, b_cnt_no - 1)

            # Buy with momentum
            if time_progress < time_cutoff and is_depth_valid:
                if momentum > 0.02 and b_cnt_yes < max_buy and ws_ask <= buy_p:
                    if ap1 > 0.0 and ap1 <= buy_p and as1 > 0.0:
                        target_amount = bullet_usd / exec_ap1
                        max_affordable = cash / (exec_ap1 * (1.0 + fee_rate))
                        bought = min(target_amount, min(as1, max_affordable))
                        if bought >= 1.0:
                            inv_yes += bought
                            cash -= (bought * exec_ap1) * (1.0 + fee_rate)
                            b_cnt_yes += 1

                if momentum < -0.02 and b_cnt_no < max_buy and no_ask_ws <= buy_p:
                    if (1.0 - bp1) > 0.0 and (1.0 - bp1) <= buy_p and bs1 > 0.0:
                        target_amount = bullet_usd / exec_no_ask_p1
                        max_affordable = cash / (exec_no_ask_p1 * (1.0 + fee_rate))
                        bought = min(target_amount, min(bs1, max_affordable))
                        if bought >= 1.0:
                            inv_no += bought
                            cash -= (bought * exec_no_ask_p1) * (1.0 + fee_rate)
                            b_cnt_no += 1

            continue

        # ---------------------------------------------------------
        # Strategy 8: ACCEL_MOMENTUM
        # Requires BOTH momentum AND acceleration in same direction.
        # Higher accuracy (60%) but fewer entries than momentum_dual.
        # Uses sell_p as take-profit, late-market forced exit at 85%.
        # ---------------------------------------------------------
        if strat_idx == 8:
            no_ask_ws = 1.0 - ws_bid
            no_bid_ws = 1.0 - ws_ask
            exec_no_ask_p1 = min(0.9999, (1.0 - bp1) + slippage)
            exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - slippage)

            # depth filter
            l2_total_depth = bs1 + as1
            if l2_total_depth >= depth_threshold:
                depth_stable_ticks += 1
            else:
                depth_stable_ticks = 0
            is_depth_valid = (depth_stable_ticks >= 15)

            lookback_ticks = max(10, int(abs(stop_p) * total_ticks))
            half_lookback = max(5, lookback_ticks // 2)

            momentum = 0.0
            accel = 0.0
            if i >= lookback_ticks:
                momentum = ws_bid - data[i - lookback_ticks, 0]
                if i >= lookback_ticks + half_lookback:
                    early_vel = data[i - half_lookback, 0] - data[i - lookback_ticks, 0]
                    late_vel = ws_bid - data[i - half_lookback, 0]
                    accel = late_vel - early_vel

            # Signal: both momentum and acceleration must agree
            favor_yes = (momentum > 0.02) and (accel > 0.01)
            favor_no = (momentum < -0.02) and (accel < -0.01)

            # Sell YES
            if inv_yes >= 10.0:
                if ws_bid >= sell_p and bp1 >= sell_p and bs1 > 0.0:
                    sold = min(inv_yes, bs1)
                    inv_yes -= sold
                    cash += (sold * exec_bp1) * (1.0 - fee_rate)
                elif time_progress > 0.85 and bp1 > 0.0 and bs1 > 0.0:
                    sold = min(inv_yes, bs1)
                    inv_yes -= sold
                    cash += (sold * exec_bp1) * (1.0 - fee_rate)

            # Sell NO
            if inv_no >= 10.0:
                if no_bid_ws >= sell_p and (1.0 - ap1) >= sell_p and as1 > 0.0:
                    sold = min(inv_no, as1)
                    inv_no -= sold
                    cash += (sold * exec_no_bid_p1) * (1.0 - fee_rate)
                elif time_progress > 0.85 and (1.0 - ap1) > 0.0 and as1 > 0.0:
                    sold = min(inv_no, as1)
                    inv_no -= sold
                    cash += (sold * exec_no_bid_p1) * (1.0 - fee_rate)

            # Buy YES (only when momentum+accel both favor YES)
            if time_progress < time_cutoff and is_depth_valid and favor_yes:
                if b_cnt_yes < max_buy and ws_ask <= buy_p:
                    if ap1 > 0.0 and ap1 <= buy_p and as1 > 0.0:
                        target_amount = bullet_usd / exec_ap1
                        max_affordable = cash / (exec_ap1 * (1.0 + fee_rate))
                        bought = min(target_amount, min(as1, max_affordable))
                        if bought >= 1.0:
                            inv_yes += bought
                            cash -= (bought * exec_ap1) * (1.0 + fee_rate)
                            b_cnt_yes += 1

            # Buy NO (only when momentum+accel both favor NO)
            if time_progress < time_cutoff and is_depth_valid and favor_no:
                if b_cnt_no < max_buy and no_ask_ws <= buy_p:
                    if (1.0 - bp1) > 0.0 and (1.0 - bp1) <= buy_p and bs1 > 0.0:
                        target_amount = bullet_usd / exec_no_ask_p1
                        max_affordable = cash / (exec_no_ask_p1 * (1.0 + fee_rate))
                        bought = min(target_amount, min(bs1, max_affordable))
                        if bought >= 1.0:
                            inv_no += bought
                            cash -= (bought * exec_no_ask_p1) * (1.0 + fee_rate)
                            b_cnt_no += 1

            continue

        # ---------------------------------------------------------
        # Strategy 9: ACCEL_MOMENTUM_L2
        # Same as accel_momentum but walks L2 order book for entries
        # to get better fills when L1 size is insufficient.
        # ---------------------------------------------------------
        if strat_idx == 9:
            no_ask_ws = 1.0 - ws_bid
            no_bid_ws = 1.0 - ws_ask
            exec_no_ask_p1 = min(0.9999, (1.0 - bp1) + slippage)
            exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - slippage)

            # L2/L3 data
            bp2 = data[i, 6]; bs2 = data[i, 7]
            ap2 = data[i, 8]; as2 = data[i, 9]
            bp3 = data[i, 10]; bs3 = data[i, 11]
            ap3 = data[i, 12]; as3 = data[i, 13]

            # Total available depth (for depth filter, include all levels)
            total_bid_depth = bs1 + bs2 + bs3
            total_ask_depth = as1 + as2 + as3
            l2_total_depth = total_bid_depth + total_ask_depth
            if l2_total_depth >= depth_threshold:
                depth_stable_ticks += 1
            else:
                depth_stable_ticks = 0
            is_depth_valid = (depth_stable_ticks >= 15)

            lookback_ticks = max(10, int(abs(stop_p) * total_ticks))
            half_lookback = max(5, lookback_ticks // 2)
            momentum = 0.0
            accel = 0.0
            if i >= lookback_ticks:
                momentum = ws_bid - data[i - lookback_ticks, 0]
                if i >= lookback_ticks + half_lookback:
                    early_vel = data[i - half_lookback, 0] - data[i - lookback_ticks, 0]
                    late_vel = ws_bid - data[i - half_lookback, 0]
                    accel = late_vel - early_vel

            favor_yes = (momentum > 0.02) and (accel > 0.01)
            favor_no = (momentum < -0.02) and (accel < -0.01)

            # Sell YES
            if inv_yes >= 10.0:
                if ws_bid >= sell_p and bp1 >= sell_p and bs1 > 0.0:
                    sold = min(inv_yes, bs1)
                    inv_yes -= sold
                    cash += (sold * exec_bp1) * (1.0 - fee_rate)
                elif time_progress > 0.85 and bp1 > 0.0 and bs1 > 0.0:
                    sold = min(inv_yes, bs1)
                    inv_yes -= sold
                    cash += (sold * exec_bp1) * (1.0 - fee_rate)

            # Sell NO
            if inv_no >= 10.0:
                if no_bid_ws >= sell_p and (1.0 - ap1) >= sell_p and as1 > 0.0:
                    sold = min(inv_no, as1)
                    inv_no -= sold
                    cash += (sold * exec_no_bid_p1) * (1.0 - fee_rate)
                elif time_progress > 0.85 and (1.0 - ap1) > 0.0 and as1 > 0.0:
                    sold = min(inv_no, as1)
                    inv_no -= sold
                    cash += (sold * exec_no_bid_p1) * (1.0 - fee_rate)

            # Buy YES — walk L2 book
            if time_progress < time_cutoff and is_depth_valid and favor_yes:
                if b_cnt_yes < max_buy and ws_ask <= buy_p:
                    remaining = bullet_usd
                    total_bought = 0.0
                    total_cost = 0.0
                    for lp, ls in [(ap1, as1), (ap2, as2), (ap3, as3)]:
                        if lp <= 0.0 or lp > buy_p or ls <= 0.0 or remaining <= 0.0:
                            continue
                        exec_p = min(0.9999, lp + slippage)
                        can_buy = remaining / (exec_p * (1.0 + fee_rate))
                        take = min(can_buy, ls)
                        if take >= 1.0:
                            total_bought += take
                            total_cost += take * exec_p * (1.0 + fee_rate)
                            remaining -= take * exec_p * (1.0 + fee_rate)
                    if total_bought >= 1.0 and cash >= total_cost:
                        inv_yes += total_bought
                        cash -= total_cost
                        b_cnt_yes += 1

            # Buy NO — walk L2 book
            if time_progress < time_cutoff and is_depth_valid and favor_no:
                if b_cnt_no < max_buy and no_ask_ws <= buy_p:
                    remaining = bullet_usd
                    total_bought = 0.0
                    total_cost = 0.0
                    # NO ask prices: 1-bp1, 1-bp2, 1-bp3
                    for lp_raw, ls in [(bp1, bs1), (bp2, bs2), (bp3, bs3)]:
                        no_price = 1.0 - lp_raw
                        if no_price <= 0.0 or no_price > buy_p or ls <= 0.0 or remaining <= 0.0:
                            continue
                        exec_p = min(0.9999, no_price + slippage)
                        can_buy = remaining / (exec_p * (1.0 + fee_rate))
                        take = min(can_buy, ls)
                        if take >= 1.0:
                            total_bought += take
                            total_cost += take * exec_p * (1.0 + fee_rate)
                            remaining -= take * exec_p * (1.0 + fee_rate)
                    if total_bought >= 1.0 and cash >= total_cost:
                        inv_no += total_bought
                        cash -= total_cost
                        b_cnt_no += 1

            continue

        # ---------------------------------------------------------
        # Strategy 10: FAV_CONVERGENCE  (the validated +EV edge)
        # Buy the FAVORITE side (price in [buy_p, sell_p]) in the late
        # window (time_progress >= time_cutoff) and HOLD to settlement.
        # Exploits the favorite-longshot bias: favorites are underpriced.
        #   buy_p   -> band low   (e.g. 0.70)
        #   sell_p  -> band high  (e.g. 0.93)
        #   time_cutoff -> earliest entry progress (e.g. 0.50)
        # ---------------------------------------------------------
        if strat_idx == 10:
            exec_no_bid_p1 = max(0.0001, (1.0 - ap1) - slippage)
            # Reversal stop-loss: exit if our held side reversed below stop_p (no longer the favorite)
            if stop_p > 0.0:
                if inv_yes >= 1.0 and ws_bid < stop_p and bs1 > 0.0:
                    sold = min(inv_yes, bs1); inv_yes -= sold
                    cash += (sold * exec_bp1) * (1.0 - fee_rate)
                if inv_no >= 1.0 and (1.0 - ws_ask) < stop_p and as1 > 0.0:
                    sold = min(inv_no, as1); inv_no -= sold
                    cash += (sold * exec_no_bid_p1) * (1.0 - fee_rate)
            if time_progress >= time_cutoff and is_depth_valid:
                # Buy YES if YES is the favorite and inside the band (total entries capped -> no flip)
                if (b_cnt_yes + b_cnt_no) < max_buy and ap1 > 0.0:
                    yes_ask = ap1
                    if buy_p <= yes_ask <= sell_p and as1 > 0.0:
                        exec_p = min(0.9999, yes_ask + slippage)
                        target_amount = bullet_usd / exec_p
                        max_affordable = cash / (exec_p * (1.0 + fee_rate))
                        bought = min(target_amount, min(as1, max_affordable))
                        if bought >= 1.0:
                            inv_yes += bought
                            cash -= bought * exec_p * (1.0 + fee_rate)
                            b_cnt_yes += 1
                # Buy NO if NO is the favorite and inside the band (NO ask = 1 - YES bid)
                if (b_cnt_yes + b_cnt_no) < max_buy and bp1 > 0.0:
                    no_ask = 1.0 - bp1
                    if buy_p <= no_ask <= sell_p and bs1 > 0.0:
                        exec_p = min(0.9999, no_ask + slippage)
                        target_amount = bullet_usd / exec_p
                        max_affordable = cash / (exec_p * (1.0 + fee_rate))
                        bought = min(target_amount, min(bs1, max_affordable))
                        if bought >= 1.0:
                            inv_no += bought
                            cash -= bought * exec_p * (1.0 + fee_rate)
                            b_cnt_no += 1
            continue

        # ---------------------------------------------------------
        # Strategy 0, 1, 2, 4 (Legacy)
        # ---------------------------------------------------------
        if ws_bid >= sell_p and inv_yes >= 10.0:
            if bp1 >= sell_p and bs1 > 0.0:
                sold = min(inv_yes, bs1); inv_yes -= sold; cash += (sold * exec_bp1) * (1.0 - fee_rate)
        
        # Protected by time_cutoff AND is_depth_valid
        if time_progress < time_cutoff and is_depth_valid and ws_ask <= buy_p and b_cnt < max_buy and not has_stopped:
            if ap1 > 0.0 and ap1 <= buy_p and as1 > 0.0:
                target_amount = bullet_usd / exec_ap1
                max_affordable = cash / (exec_ap1 * (1.0 + fee_rate))
                bought = min(target_amount, min(as1, max_affordable))
                if bought >= 1.0:
                    cost = bought * exec_ap1; inv_yes += bought; cash -= cost * (1.0 + fee_rate); b_cnt += 1
                    
        if strat_idx == 1 and ws_bid <= stop_p and inv_yes >= 10.0:
            if bp1 > 0.0 and bs1 > 0.0:
                sold = min(inv_yes, bs1); inv_yes -= sold; cash += (sold * exec_bp1) * (1.0 - fee_rate); has_stopped = True 

    # ===================== [SMART CONTRACT SETTLEMENT] =====================
    winner_yes = False
    if len(data) > 0:
        winner_yes = data[-1, 1] > 0.5
        
        if winner_yes:
            cash += inv_yes * 1.0  
        else:
            cash += inv_no * 1.0   
            
        inv_yes = 0.0
        inv_no = 0.0

    return cash - cap, cash, b_cnt + b_cnt_yes + b_cnt_no, 0, winner_yes

@njit(parallel=True, nogil=True)
def run_batch_simulation(all_data, s_idx, b_p, s_p, m_b, st_p, fee_rate, slippage, time_cutoff, depth_threshold):
    num_markets = len(all_data)
    pnls = np.zeros(num_markets); cashes = np.zeros(num_markets)
    trades = np.zeros(num_markets); invs = np.zeros(num_markets); wins = np.zeros(num_markets)
    for i in prange(num_markets):
        p, c, t, v, w = fast_simulate(all_data[i], s_idx, b_p, s_p, m_b, st_p, fee_rate, slippage, time_cutoff, depth_threshold)
        pnls[i], cashes[i], trades[i], invs[i], wins[i] = p, c, t, v, w
    return pnls, cashes, trades, invs, wins

# ===================== [MODE: OPTIMIZE & BACKTEST] =====================
STRAT_MAP = {"swing_hold": 0, "swing_stop": 1, "market_maker": 2, "mint_market_maker": 3, "swing_no": 4, "swing_dual": 5, "momentum_dual": 6, "trend_scalp": 7, "accel_momentum": 8, "accel_momentum_l2": 9, "fav_convergence": 10, "fav_convergence_late": 10}

def objective(trial, market_data, target_strat, fee_rate, slippage):
    strategy = target_strat if target_strat != 'all' else trial.suggest_categorical("strategy", list(STRAT_MAP.keys()))
    # fav_convergence buys favorites (high price band); all others buy cheap.
    if strategy == "fav_convergence":
        buy_p = trial.suggest_float("buy_p", 0.55, 0.85)
    else:
        buy_p = trial.suggest_float("buy_p", 0.10, 0.45)

    time_cutoff = trial.suggest_float("time_cutoff", 0.1, 0.9)
    depth_threshold = trial.suggest_float("depth_threshold", 0.0, 600.0)

    if strategy == "fav_convergence":
        sell_p = trial.suggest_float("sell_p", 0.88, 0.98)   # band high
        max_buy = trial.suggest_int("max_buy", 1, 10)
        stop_p = 0.0
    elif strategy in ["swing_no", "swing_dual"]:
        sell_p = trial.suggest_float("sell_p", 0.85, 0.98)
        max_buy = trial.suggest_int("max_buy", 1, 20)
        stop_p = trial.suggest_float("stop_p", -1.0, 0.20)
    elif strategy in ["momentum_dual", "trend_scalp", "accel_momentum", "accel_momentum_l2"]:
        sell_p = trial.suggest_float("sell_p", 0.40, 0.98)
        max_buy = trial.suggest_int("max_buy", 1, 30)
        stop_p = trial.suggest_float("stop_p", 0.02, 0.20)  # repurposed as lookback fraction
    elif "market_maker" in strategy:
        sell_p = buy_p + trial.suggest_float("mm_spread", 0.03, 0.15)
        max_buy, stop_p = trial.suggest_int("max_buy", 10, 50), 0.0
    else:
        sell_p = trial.suggest_float("sell_p", 0.85, 0.98)
        max_buy = trial.suggest_int("max_buy", 1, 20)
        stop_p = trial.suggest_float("stop_p", 0.01, 0.20) if strategy == "swing_stop" else 0.0

    pnls, _, _, _, _ = run_batch_simulation(market_data, STRAT_MAP[strategy], buy_p, sell_p, max_buy, stop_p, fee_rate, slippage, time_cutoff, depth_threshold)
    return np.mean(pnls) * len(pnls) - (np.std(pnls) * 2.0)

def run_optimization(market_data, target_strat='all', n_trials=1000, n_jobs=1, fee_rate=0.0, slippage=0.0):
    optuna.logging.set_verbosity(optuna.logging.WARNING) 
    study = optuna.create_study(direction="maximize")
    
    print(f"[*] Stress Testing [{target_strat.upper()}] (Trials={n_trials}, Jobs={n_jobs}, Fee={fee_rate*100}%, Slippage={slippage})...")
    study.optimize(lambda t: objective(t, market_data, target_strat, fee_rate, slippage), n_trials=n_trials, n_jobs=n_jobs, show_progress_bar=True)
    
    bp = study.best_params
    win_s = bp.get('strategy', target_strat) 
    
    final = {
        'strategy': win_s, 
        'buy_p': bp['buy_p'], 
        'max_buy': bp.get('max_buy', 1), 
        'stop_p': bp.get('stop_p', 0.0),
        'time_cutoff': bp.get('time_cutoff', 1.0),
        'depth_threshold': bp.get('depth_threshold', 0.0) 
    }
    if "market_maker" in win_s: final['sell_p'] = bp['buy_p'] + bp['mm_spread']
    else: final['sell_p'] = bp.get('sell_p', 0.95)
    
    with open(f"genome_{win_s}.json", 'w') as f: json.dump(final, f, indent=4)
    print(f"\nWINNER: {win_s.upper()} | Genome: {final}")
    return final

def load_all_genomes():
    return [json.load(open(f"genome_{s}.json")) for s in STRAT_MAP.keys() if os.path.exists(f"genome_{s}.json")]

def run_scenario_matrix(fee_rate=0.0, slippage=0.0, data_dir="market_data"):
    loaded_genomes = load_all_genomes()
    if not loaded_genomes: return print("[-] No genomes found. Run 'opt' first.")
    
    market_data, _ = load_data_to_numpy(data_dir)
    
    print(f"\n[*] Matrix Params -> Fee: {fee_rate*100}% | Slippage Penalty: {slippage}")
    col_w = 14
    header = f"\n{'Outcome':<25} | " + " | ".join([f"{g['strategy'].upper()[:col_w]:>{col_w}}" for g in loaded_genomes]) + " | Combined"
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    
    total_pnls = [0.0] * len(loaded_genomes)
    
    row = f"{'ALL MARKETS':<25} | "
    combined = 0
    for i, g in enumerate(loaded_genomes):
        tc = g.get('time_cutoff', 1.0)
        dt = g.get('depth_threshold', 0.0)
        p, _, _, _, _ = run_batch_simulation(market_data, STRAT_MAP[g['strategy']], g['buy_p'], g['sell_p'], g['max_buy'], g['stop_p'], fee_rate, slippage, tc, dt)
        val = np.sum(p)
        row += f"${val:>{col_w-1}.2f} | "
        total_pnls[i] += val
        combined += val
    print(row + f"${combined:>{col_w-1}.2f}")
    
    print("-" * len(header))
    print("=" * len(header))

PORTFOLIO_CONFIG = {
    "strategies": [
        {"name": "swing_dual",          "weight": 0.15},
        {"name": "momentum_dual",       "weight": 0.15},
        {"name": "trend_scalp",         "weight": 0.15},
        {"name": "accel_momentum_l2",   "weight": 0.55},
    ]
}

def run_portfolio_backtest(market_data, fee_rate=0.0, slippage=0.0):
    config = PORTFOLIO_CONFIG
    print(f"\n[*] PORTFOLIO BACKTEST (Fee={fee_rate*100}%, Slippage={slippage})")
    alloc_str = ', '.join([f"{s['name']}={s['weight']:.0%}" for s in config['strategies']])
    print(f"    Allocation: {alloc_str}")
    print("=" * 80)

    all_pnls = {}
    for strat in config["strategies"]:
        name = strat["name"]
        weight = strat["weight"]
        gf = f"genome_{name}.json"
        if not os.path.exists(gf):
            print(f"  [-] Missing {gf}, skipping")
            continue
        g = json.load(open(gf))
        tc = g.get('time_cutoff', 1.0)
        dt = g.get('depth_threshold', 0.0)
        p, c, t, v, w = run_batch_simulation(
            market_data, STRAT_MAP[g['strategy']],
            g['buy_p'], g['sell_p'], g['max_buy'], g['stop_p'],
            fee_rate, slippage, tc, dt
        )
        # Scale PnL by weight (equivalent to allocating weight% of capital)
        scaled_pnl = p * weight
        all_pnls[name] = scaled_pnl

        win_rate = np.sum(p > 0) / len(p) * 100
        sharpe = np.mean(p) / np.std(p) if np.std(p) > 0 else 0
        print(f"  {name:20s} (w={weight:.0%}) | Total: ${np.sum(scaled_pnl):>10.2f} | Sharpe: {sharpe:.3f} | WinRate: {win_rate:.1f}%")

    if not all_pnls:
        print("  [-] No strategies loaded.")
        return

    # Combine
    combined = np.zeros(len(market_data))
    for pnl in all_pnls.values():
        combined += pnl

    total = np.sum(combined)
    avg = np.mean(combined)
    std = np.std(combined)
    sharpe = avg / std if std > 0 else 0
    win_rate = np.sum(combined > 0) / len(combined) * 100
    max_loss = np.min(combined)

    print("-" * 80)
    print(f"  {'PORTFOLIO':20s}        | Total: ${total:>10.2f} | Sharpe: {sharpe:.3f} | WinRate: {win_rate:.1f}% | MaxLoss: ${max_loss:.2f}")
    print("=" * 80)

    # Save portfolio config
    with open("genome_portfolio.json", "w") as f:
        json.dump(config, f, indent=4)
    print(f"\n  [+] Portfolio config saved to genome_portfolio.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polymarket Quantitative Backtest Engine")
    parser.add_argument('--mode', choices=['opt', 'matrix', 'portfolio', 'bt'], required=True)
    parser.add_argument('--target_strat', default='all', help="Specific strategy to optimize or backtest.")
    parser.add_argument('--n_trials', type=int, default=1000, help="Number of trials for Optuna.")
    parser.add_argument('--n_jobs', type=int, default=1, help="Number of parallel jobs for Optuna.")
    parser.add_argument('--fee', type=float, default=0.0, help="Trading fee rate")
    parser.add_argument('--slippage', type=float, default=0.0, help="Slippage penalty per trade")
    parser.add_argument('--data_dir', type=str, default='market_data', help="Directory containing CSV market data files.")
    
    args = parser.parse_args()
    
    if args.mode == 'matrix': 
        run_scenario_matrix(fee_rate=args.fee, slippage=args.slippage, data_dir=args.data_dir)
    elif args.mode == 'portfolio':
        D, N = load_data_to_numpy(args.data_dir)
        run_portfolio_backtest(D, fee_rate=args.fee, slippage=args.slippage)
    else:
        D, N = load_data_to_numpy(args.data_dir)
        if args.mode == 'opt': 
            run_optimization(D, target_strat=args.target_strat, n_trials=args.n_trials, n_jobs=args.n_jobs, fee_rate=args.fee, slippage=args.slippage)
        elif args.mode == 'bt':
            target_file = f"genome_{args.target_strat}.json"
            if not os.path.exists(target_file):
                print(f"[-] Error: Could not find {target_file}")
                exit(1)
            with open(target_file, 'r') as f: g = json.load(f)
            tc = g.get('time_cutoff', 1.0)
            dt = g.get('depth_threshold', 0.0)
            p, c, t, v, w = run_batch_simulation(D, STRAT_MAP[g['strategy']], g['buy_p'], g['sell_p'], g['max_buy'], g['stop_p'], args.fee, args.slippage, tc, dt)
            print(f"\n[*] BACKTEST REPORT ({g['strategy'].upper()}) with {args.fee*100}% Fee, {args.slippage} Slippage")
            print(f"Final Backtest Total PNL: ${np.sum(p):.2f}")
