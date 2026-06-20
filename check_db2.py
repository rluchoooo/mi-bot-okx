import sqlite3
c = sqlite3.connect('quantum_bot.db')
res = c.execute("SELECT id, symbol, side, status, tp1_price, tp2_price, sl_price FROM trades WHERE symbol='HBAR-USDT-SWAP' ORDER BY id DESC LIMIT 1").fetchone()
print(res)
