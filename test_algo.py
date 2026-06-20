import asyncio
from scanner import OKXClient
from strategy import SupertrendPullbackStrategy, QuantumSMCStrategy

async def test():
    client = OKXClient(api_key="", api_secret="", passphrase="", simulated=True)
    iid = "SOL-USDT-SWAP"
    df_1h = await client.candles(iid, "1H", 150)
    df_15m = await client.candles(iid, "15m", 150)
    df_5m = await client.candles(iid, "5m", 150)
    
    print(f"Data fetched: 5m={len(df_5m)} candles")
    
    st_strat = SupertrendPullbackStrategy()
    sig = st_strat.signal(iid, df_1h, df_15m, df_5m)
    print(f"Supertrend Pullback Signal: {sig}")
    
    smc_strat = QuantumSMCStrategy()
    sig2 = smc_strat.signal(iid, df_1h, df_15m, df_5m)
    print(f"Quantum SMC Signal: {sig2}")
    
    await client.close()

if __name__ == "__main__":
    asyncio.run(test())
