import asyncio
import os
import httpx
from dotenv import load_dotenv
import base64
import hashlib
import hmac
from datetime import datetime, timezone
import json

load_dotenv()
API_KEY = os.getenv("OKX_API_KEY", "")
API_SECRET = os.getenv("OKX_API_SECRET", "")
PASSPHRASE = os.getenv("OKX_API_PASSPHRASE", "")
SIMULATED = os.getenv("OKX_SIMULATED", "1") == "1"

def _sign(method, path, body=""):
    ts  = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    pre = f"{ts}{method.upper()}{path}{body}"
    sig = base64.b64encode(hmac.new(API_SECRET.encode(), pre.encode(), hashlib.sha256).digest()).decode()
    h = {
        "Content-Type": "application/json",
        "OK-ACCESS-KEY": API_KEY,
        "OK-ACCESS-SIGN": sig,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
    }
    if SIMULATED:
        h["x-simulated-trading"] = "1"
    return h

async def main():
    async with httpx.AsyncClient() as client:
        headers = _sign("GET", "/api/v5/account/positions?instType=SWAP")
        r = await client.get("https://www.okx.com/api/v5/account/positions?instType=SWAP", headers=headers)
        data = r.json()
        if data["code"] != "0":
            print(f"Error: {data['msg']}")
            return
        positions = [p for p in data.get("data", []) if float(p.get("pos", 0)) != 0]
        print(f"Posiciones abiertas: {len(positions)}")
        for p in positions:
            print(f"- {p['instId']} | {p['posSide']} | Entry: {p['avgPx']} | PnL: {p['upl']}")

asyncio.run(main())
