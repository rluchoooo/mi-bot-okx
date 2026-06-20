import asyncio
import os
from scanner import OKXClient
from models import engine, Base

async def reset():
    print("Connecting to OKX...")
    c = OKXClient(
        api_key=os.getenv("OKX_API_KEY", ""),
        api_secret=os.getenv("OKX_API_SECRET", ""),
        passphrase=os.getenv("OKX_PASSPHRASE", ""),
        simulated=True
    )
    pos = await c.get_positions()
    print("Positions:", len(pos))
    for p in pos:
        sym = p['instId']
        side = 'long' if p.get('posSide', 'net').lower() == 'long' else 'short'
        print(f"Closing {sym} {side}")
        await c.close_position(sym, side)
        await c.cancel_algo_orders(sym)
    await c.close()
    
    print("Recreating database schema...")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    print("Done")

asyncio.run(reset())
