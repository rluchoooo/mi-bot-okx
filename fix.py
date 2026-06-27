import sqlite3

conn = sqlite3.connect('quantum_bot.db')
c = conn.cursor()

c.execute("SELECT id, symbol, status FROM trades WHERE status NOT IN ('CLOSED', 'EARLY_EXIT')")
rows = c.fetchall()
print('Open trades in DB:', rows)

c.execute("UPDATE trades SET status = 'CLOSED', close_reason = 'MANUAL_CLEANUP' WHERE status NOT IN ('CLOSED', 'EARLY_EXIT')")
conn.commit()

c.execute("SELECT id, symbol, status FROM trades WHERE status NOT IN ('CLOSED', 'EARLY_EXIT')")
rows2 = c.fetchall()
print('Open trades after update:', rows2)

conn.close()
