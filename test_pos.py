import asyncio, os, json
from dotenv import load_dotenv
load_dotenv()
from scanner import OKXClient

async def test():
    c = OKXClient(
        os.getenv('OKX_API_KEY'), 
        os.getenv('OKX_API_SECRET'), 
        os.getenv('OKX_API_PASSPHRASE'), 
        simulated=True
    )
    pos = await c.get_positions()
    print(json.dumps(pos[:2] if pos else []))

asyncio.run(test())
