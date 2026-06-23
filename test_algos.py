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
    algos = await c._req('GET', '/api/v5/trade/orders-algo-pending?instType=SWAP&ordType=conditional', auth=True)
    print(json.dumps(algos[:2] if algos else []))
    
    # Let's also check oco
    oco = await c._req('GET', '/api/v5/trade/orders-algo-pending?instType=SWAP&ordType=oco', auth=True)
    print("OCO:")
    print(json.dumps(oco[:2] if oco else []))

asyncio.run(test())
