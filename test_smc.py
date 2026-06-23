import asyncio
import os
import httpx
from dotenv import load_dotenv
import pandas as pd
from decimal import Decimal
from strategy import SMCPDHSweepReversal, SMCFVGMitigation, SMCOrderblockBounce, SMCAMDBreakout

load_dotenv()
API_KEY = os.getenv("OKX_API_KEY", "")
API_SECRET = os.getenv("OKX_API_SECRET", "")
PASSPHRASE = os.getenv("OKX_API_PASSPHRASE", "")
SIMULATED = os.getenv("OKX_SIMULATED", "1") == "1"

class DummyClient:
    async def candles(self, inst_id, bar, limit):
        async with httpx.AsyncClient() as client:
            res = await client.get(f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}")
            data = res.json()["data"]
            df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"])
            for c in ["open", "high", "low", "close", "vol"]:
                df[c] = df[c].astype(float)
            df['ts'] = pd.to_datetime(df['ts'].astype(float), unit='ms')
            return df.sort_values('ts').reset_index(drop=True)

    async def tickers(self):
        async with httpx.AsyncClient() as client:
            res = await client.get("https://www.okx.com/api/v5/market/tickers?instType=SWAP")
            return res.json()["data"]

async def test_smc():
    client = DummyClient()
    tickers = await client.tickers()
    universe = [t for t in tickers if t["instId"].endswith("-USDT-SWAP") and float(t.get("vol24h", 0)) > 100000][:10]
    
    strats = [SMCPDHSweepReversal(), SMCFVGMitigation(), SMCOrderblockBounce(), SMCAMDBreakout()]
    
    for tick in universe:
        iid = tick["instId"]
        print(f"Probando {iid}...")
        try:
            df_1h, df_15m, df_5m = await asyncio.gather(
                client.candles(iid, "1H", 300),
                client.candles(iid, "15m", 300),
                client.candles(iid, "5m", 150),
            )
            for st in strats:
                sig = st.signal(iid, df_1h, df_15m, df_5m)
                if sig:
                    print(f"✅ SEÑAL {st.NAME} EN {iid}: {sig.side}")
        except Exception as e:
            print(f"ERROR: {iid} - {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_smc())
