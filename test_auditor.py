import asyncio
from scanner import QuantumBotRuntime, OKXClient

async def run():
    client = OKXClient("dummy", "dummy", "dummy", True)
    bot = QuantumBotRuntime("dummy", "dummy", "dummy")
    try:
        await bot._load_instruments(client)
        await bot._self_heal_auditor(client)
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(run())
