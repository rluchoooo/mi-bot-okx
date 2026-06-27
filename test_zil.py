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
    res = await c._req("GET", "/api/v5/public/instruments?instType=SWAP&instId=ZIL-USDT-SWAP")
    print(res)

asyncio.run(test())
