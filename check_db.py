import sqlite3
c = sqlite3.connect('quantum_bot.db')
res = c.execute("SELECT id, symbol, side, status, entry_price, sl_price, atr, be_activated, trail_activated, peak_price, tp1_done, strategy FROM trades WHERE symbol='MASK-USDT-SWAP' ORDER BY id DESC LIMIT 1").fetchone()
print(res)
