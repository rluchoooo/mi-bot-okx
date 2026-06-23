from db import get_session
from models import Trade, TradeStatus

with get_session() as db:
    trades = db.query(Trade).filter(Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])).all()
    for t in trades:
        print(f"{t.symbol} {t.side}: TP1={t.tp1_price}, TP2={t.tp2_price}, SL={t.sl_price}")
