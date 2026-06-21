import pandas as pd
import numpy as np
import glob
import os
import optuna
import matplotlib.pyplot as plt
import seaborn as sns
from joblib import parallel_backend

# 1. Global Data Loading & Pre-processing
FILE_PATH = "market_data/Token_*.csv"
FILES = sorted(glob.glob(FILE_PATH))
if not FILES:
    print("[-] Error: No data found in 'market_data/'")
    exit()

# ¥u¸ü¤J¨ã³Æ L2 ²`«×ªº¦³®ÄÀÉ®×
MARKET_DATA = []
print(f"[*] Loading L2 Data from {len(FILES)} files...")
for f in FILES:
    df = pd.read_csv(f).fillna(0.0) # ¶ñ¸ÉªÅ¯Ê­È¨¾§b
    if 'Ask_S1' in df.columns:
        MARKET_DATA.append(df)

if not MARKET_DATA:
    print("[-] Error: No L2 order book columns found in CSVs.")
    exit()

print(f"[+] Successfully loaded {len(MARKET_DATA)} L2 market files.")

def objective(trial):
    # Search Space
    buy_p = trial.suggest_float("buy_p", 0.005, 0.05)
    sell_p = trial.suggest_float("sell_p", 0.10, 0.95)
    max_buy = trial.suggest_int("max_buy", 1, 20)
    
    cap = 1000.0
    total_pnl = 0.0

    for df in MARKET_DATA:
        cash, inv_yes, inv_no, b_cnt = 0.0, cap, cap, 0
        
        # Unpack L2 Arrays for C-level execution speed
        ws_bids = df['WS_Bid'].values
        ws_asks = df['WS_Ask'].values
        
        bp1, bs1 = df['Bid_P1'].values, df['Bid_S1'].values
        bp2, bs2 = df['Bid_P2'].values, df['Bid_S2'].values
        bp3, bs3 = df['Bid_P3'].values, df['Bid_S3'].values
        
        ap1, as1 = df['Ask_P1'].values, df['Ask_S1'].values
        ap2, as2 = df['Ask_P2'].values, df['Ask_S2'].values
        ap3, as3 = df['Ask_P3'].values, df['Ask_S3'].values

        for i in range(len(ws_bids)):
            # Ä²µo±ø¥ó¨ÌµM¬Ý WS §Y®É³ø»ù
            can_exit = ws_bids[i] >= sell_p

            # ==========================================
            # 1. L2 Scavenge (Buy YES) - ¦Y½æ³æÀð (Asks)
            # ==========================================
            if can_exit and ws_asks[i] <= buy_p and b_cnt < max_buy:
                needed = 500.0
                bought = 0.0
                cost = 0.0
                
                # ³vÀÉ¦Y³æ (Walk the book)
                for p, s in [(ap1[i], as1[i]), (ap2[i], as2[i]), (ap3[i], as3[i])]:
                    if p == 0 or p > buy_p: continue # ¶W¹L§Ú­Ìªº­­»ù´N¤£¦Y
                    take = min(needed, s)
                    bought += take
                    cost += take * p
                    needed -= take
                    if needed <= 0: break
                
                if bought > 0:
                    inv_yes += bought
                    cash -= cost
                    b_cnt += 1

            # ==========================================
            # 2. L2 Escalator (Sell NO) - ¯{¶R³æÀð (Bids)
            # ==========================================
            if can_exit and inv_no >= 10: # ¦Ü¤Ö¦³ 10 ªÑ¤~½æ¡AÁ×§K·¥ºÝ¸HªÑ
                to_sell = min(500.0, inv_no)
                sold = 0.0
                revenue = 0.0
                
                for p, s in [(bp1[i], bs1[i]), (bp2[i], bs2[i]), (bp3[i], bs3[i])]:
                    if p == 0 or p < sell_p: continue # §C©ó§Ú­Ìªº©³»ù´N¤£¯{
                    take = min(to_sell, s)
                    sold += take
                    revenue += take * p
                    to_sell -= take
                    if to_sell <= 0: break

                if sold > 0:
                    inv_no -= sold
                    cash += revenue

            # 3. Atomic Merge
            pairs = min(inv_yes, inv_no)
            if pairs >= 100:
                inv_yes -= pairs
                inv_no -= pairs
                cash += pairs

        # Settlement (¥H³Ì«á¤@µ§ WS_Ask §@¬°µ²ºâ¨Ì¾Ú)
        last_ask = df['WS_Ask'].values[-1]
        winner_yes = last_ask > 0.5
        payout = inv_yes if winner_yes else inv_no
        total_pnl += (cash + payout - cap)

    return total_pnl

def run_l2_opt():
    sampler = optuna.samplers.TPESampler(multivariate=True)
    study = optuna.create_study(direction="maximize", sampler=sampler)

    print(f"[*] Starting 1000 Trials L2 Parallel Optimization...")
    with parallel_backend("multiprocessing", n_jobs=-1):
        study.optimize(objective, n_trials=1000, n_jobs=-1)

    print("\n" + "="*50)
    print(f"REALISTIC L2 PNL FOUND: ${study.best_value:.2f}")
    print("BEST PARAMS:", study.best_params)
    print("="*50)

    print("[*] Generating Static Heatmap and Importance Plot...")
    df_res = study.trials_dataframe()
    
    plt.figure(figsize=(12, 10))
    df_res['p_sell'] = df_res['params_sell_p'].round(3)
    df_res['p_buy'] = df_res['params_buy_p'].round(3)
    pivot = df_res.pivot_table(index='p_sell', columns='p_buy', values='value', aggfunc='max')
    sns.heatmap(pivot, cmap='magma')
    plt.title('L2 Optimization Heatmap: Sell vs Buy Price PNL')
    plt.savefig('opt_heatmap_l2.png')
    plt.close()

    importances = optuna.importance.get_param_importances(study)
    plt.figure(figsize=(8, 6))
    plt.bar(importances.keys(), importances.values(), color='coral')
    plt.title('L2 Hyperparameter Importance')
    plt.savefig('opt_importance_l2.png')
    plt.close()

    html = f"""
    <html><body style="background:#222; color:white; font-family:sans-serif; text-align:center;">
        <h1>Level 2 Strategy Optimization Report</h1>
        <div style="background:#333; display:inline-block; padding:20px; border-radius:10px;">
            <h2>Realistic Best PNL: ${study.best_value:.2f}</h2>
            <p>Params: {study.best_params}</p>
        </div><br><br>
        <img src="opt_heatmap_l2.png" style="width:45%; margin:10px;">
        <img src="opt_importance_l2.png" style="width:45%; margin:10px;">
    </body></html>
    """
    with open("report_l2.html", "w") as f:
        f.write(html)
    
    print("[+] L2 Optimization complete. Open 'report_l2.html' to see results.")

if __name__ == "__main__":
    run_l2_opt()
