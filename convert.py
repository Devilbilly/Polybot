import os
import pandas as pd
from datetime import datetime

def convert_log_to_csv(log_file="paper_trader_detail.log"):
    today_str = datetime.now().strftime("%Y%m%d")
    output_dir = f"market_data_{today_str}"
    
    print(f"[SYSTEM] Starting conversion. Output directory: {output_dir}")
    
    if not os.path.exists(log_file):
        print(f"[ERROR] Cannot find {log_file}")
        return None
        
    os.makedirs(output_dir, exist_ok=True)
    
    market_data = {}
    total_ticks = 0
    missing_size_warning_triggered = False
    
    with open(log_file, 'r', encoding='utf-8') as f:
        for line in f:
            if "[TICK]" in line:
                try:
                    parts = line.split("|")
                    if len(parts) < 5:
                        continue
                        
                    token_part = parts[2].strip()
                    token_id = token_part.split(":")[1].strip()
                    
                    bid = float(parts[3].split(":")[1].strip())
                    ask = float(parts[4].split(":")[1].strip())
                    
                    # Default conservative liquidity for old logs
                    bid_size = 50.0 
                    ask_size = 50.0
                    
                    # If the log is the new upgraded format containing sizes
                    if len(parts) >= 7:
                        bid_size = float(parts[5].split(":")[1].strip())
                        ask_size = float(parts[6].split(":")[1].strip())
                    elif not missing_size_warning_triggered:
                        print("[WARNING] Old log format detected. Using conservative 50.0 mock liquidity.")
                        missing_size_warning_triggered = True
                        
                    if token_id not in market_data:
                        market_data[token_id] = []
                        
                    market_data[token_id].append({
                        "WS_Bid": bid,
                        "WS_Ask": ask,
                        "Bid_P1": bid,
                        "Bid_S1": bid_size, 
                        "Ask_P1": ask,
                        "Ask_S1": ask_size  
                    })
                    
                    total_ticks += 1
                except Exception as e:
                    continue

    print(f"[SYSTEM] Parsed {total_ticks} ticks across {len(market_data)} tokens.")
    
    generated_files = 0
    for token_id, rows in market_data.items():
        if len(rows) < 10:
            continue
            
        df = pd.DataFrame(rows)
        columns_order = ['WS_Bid', 'WS_Ask', 'Bid_P1', 'Bid_S1', 'Ask_P1', 'Ask_S1']
        df = df[columns_order]
        
        output_file = os.path.join(output_dir, f"Token_{token_id}.csv")
        df.to_csv(output_file, index=False)
        generated_files += 1
        
    print(f"[SYSTEM] Successfully generated {generated_files} CSV files in '{output_dir}/'")
    return output_dir

if __name__ == "__main__":
    generated_dir = convert_log_to_csv()
    if generated_dir:
        print("\n[SYSTEM] Run your backtest using:")
        print(f"python3 bt.py --mode bt --target_strat swing_dual --fee 0.001 --slippage 0.002 --data_dir {generated_dir}")
