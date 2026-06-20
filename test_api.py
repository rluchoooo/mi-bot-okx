import os, json, asyncio
from dotenv import load_dotenv
load_dotenv()
from scanner import OKXClient

async def main():
    client = OKXClient(
        os.getenv('OKX_API_KEY', ''),
        os.getenv('OKX_API_SECRET', ''),
        os.getenv('OKX_API_PASSPHRASE', ''),
        True
    )
    res = await client.get_positions()
    print(json.dumps(res, indent=2))

asyncio.run(main())
