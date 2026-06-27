import asyncio
import os
from dotenv import load_dotenv
from scanner import OKXClient

load_dotenv()

async def test():
    c = OKXClient(
        os.getenv('OKX_API_KEY'), 
        os.getenv('OKX_API_SECRET'), 
        os.getenv('OKX_API_PASSPHRASE'), 
        simulated=True
    )
    
    # Try fetching history for some symbols
    for sym in ["ZIL-USDT-SWAP", "CRO-USDT-SWAP", "CHZ-USDT-SWAP"]:
        try:
            res = await c.get_positions_history(sym, limit=5)
            print(f"History for {sym}:", res)
        except Exception as e:
            print(f"Error for {sym}: {e}")

asyncio.run(test())
