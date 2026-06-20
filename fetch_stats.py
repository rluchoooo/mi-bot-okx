import os
import re
from gradio_client import Client
import sys
sys.stdout.reconfigure(encoding='utf-8')

try:
    client = Client("angelik723/okex")
    res = client.predict(api_name="/build_dashboard")
    
    # Extract important fields
    today_pnl = re.search(r'PNL DIARIO.*?<strong.*?>([-+]?\d+\.\d+)</strong>', res, re.DOTALL)
    win_rate = re.search(r'WIN RATE.*?<strong.*?>([\d\.]+)%</strong>', res, re.DOTALL)
    total_pnl = re.search(r'>TOTAL.*?<strong.*?>([-+]?\d+\.\d+)</strong>', res, re.DOTALL)
    
    # We can just dump everything we can find about "TOTAL HOY" or "RENDIMIENTO GLOBAL"
    print("Today PNL:", today_pnl.group(1) if today_pnl else "Not found")
    print("Win Rate:", win_rate.group(1) + "%" if win_rate else "Not found")
    print("Total PNL:", total_pnl.group(1) if total_pnl else "Not found")
    
    # Let's extract closed trades count
    closed_trades = re.search(r'TRADES CERRADOS</small>.*?<strong>(\d+)</strong>', res, re.DOTALL)
    print("Closed trades:", closed_trades.group(1) if closed_trades else "Not found")
    
    # Let's extract open trades
    open_trades = re.search(r'MONITOR DE POSICIONES ACTIVAS.*?<b.*?>(\d+) ACTIVAS</b>', res, re.DOTALL)
    print("Open trades:", open_trades.group(1) if open_trades else "Not found")

except Exception as e:
    print("Error:", e)
