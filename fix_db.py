import asyncio
from models import get_session
from models import Trade, TradeStatus

def fix_db():
    with get_session() as db:
        trades = db.query(Trade).filter(Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])).all()
        for t in trades:
            if t.tp1_price is None or t.tp2_price is None:
                print(f"Fixing {t.symbol} {t.side}...")
                atr_est = float(t.atr) if t.atr else (t.entry_price * 0.005 / 2.5)
                # lifecycle TP1 is 1.2, TP2 is 2.4
                side_str = "long" if t.side.value == "long" else "short"
                if side_str == "long":
                    t.tp1_price = t.entry_price + (1.2 * atr_est)
                    t.tp2_price = t.entry_price + (2.4 * atr_est)
                    t.profit_lock_sl = t.entry_price + (0.4 * atr_est)
                else:
                    t.tp1_price = t.entry_price - (1.2 * atr_est)
                    t.tp2_price = t.entry_price - (2.4 * atr_est)
                    t.profit_lock_sl = t.entry_price - (0.4 * atr_est)
                print(f"  -> TP1: {t.tp1_price}, TP2: {t.tp2_price}, BE: {t.profit_lock_sl}")
        db.commit()

if __name__ == "__main__":
    fix_db()
