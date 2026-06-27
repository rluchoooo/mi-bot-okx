import sqlite3
conn = sqlite3.connect('quantum_bot.db')
c = conn.cursor()
c.execute('SELECT symbol, side, entry_price, position_size, realized_pnl, status, close_reason FROM trades ORDER BY id DESC LIMIT 15')
for r in c.fetchall():
    print(r)
conn.close()
