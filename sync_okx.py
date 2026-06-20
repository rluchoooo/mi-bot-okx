import asyncio
from sqlalchemy.orm import Session
from models import get_session, Trade, TradeStatus, Strategy, TradeSide
from scanner import OKXClient

import os
from dotenv import load_dotenv
load_dotenv()

async def sync():
    client = OKXClient(
        api_key=os.getenv("OKX_API_KEY", ""),
        api_secret=os.getenv("OKX_API_SECRET", ""),
        passphrase=os.getenv("OKX_API_PASSPHRASE", ""),
        simulated=True
    )
    try:
        positions = await client.get_positions()
    except Exception as e:
        print(f"Error fetching: {e}")
        return
        
    print(f"Found {len(positions)} positions on OKX.")
    
    with get_session() as db:
        for p in positions:
            sym = p.get("instId")
            pos_side = p.get("posSide", "").lower()
            side = TradeSide.LONG if pos_side == "long" else TradeSide.SHORT
            entry = float(p.get("avgPx", 0))
            qty = float(p.get("pos", 0))
            
            # Check if exists
            existing = db.query(Trade).filter_by(symbol=sym, status=TradeStatus.OPEN).first()
            if existing:
                print(f"Trade {sym} already in DB.")
                continue
                
            print(f"Importing {sym} into DB...")
            t = Trade(
                symbol=sym,
                side=side,
                strategy="SUPERTREND_PULLBACK_V3", # default guess
                status=TradeStatus.OPEN,
                entry_price=entry,
                qty=qty,
                sl_price=entry * (0.95 if side == TradeSide.LONG else 1.05),
                tp_price=entry * (1.10 if side == TradeSide.LONG else 0.90),
                atr_5m=entry * 0.01,
                leverage=int(p.get("lever", 10))
            )
            db.add(t)
        db.commit()
    print("Done syncing.")
    
if __name__ == "__main__":
    asyncio.run(sync())
