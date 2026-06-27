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
    for sym in ["XLM-USDT-SWAP", "BOME-USDT-SWAP"]:
        res = await c._req("GET", f"/api/v5/public/instruments?instType=SWAP&instId={sym}")
        if res:
            inst = res[0]
            print(f"{sym}: ctVal={inst['ctVal']}, lotSz={inst['lotSz']}, minSz={inst['minSz']}")

asyncio.run(test())
