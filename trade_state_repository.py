from sqlalchemy.orm import Session
from models import get_session, Trade, TradeStatus

class TradeStateRepository:
    def get_open_trades(self):
        with get_session() as db:
            return db.query(Trade).filter(Trade.position_closed == 0).all()
            
    def get_trade_by_symbol(self, symbol: str) -> Trade | None:
        with get_session() as db:
            return db.query(Trade).filter(Trade.symbol == symbol, Trade.position_closed == 0).first()

    def update_trade(self, trade_id: int, **kwargs):
        with get_session() as db:
            t = db.query(Trade).filter(Trade.id == trade_id).first()
            if t:
                for k, v in kwargs.items():
                    setattr(t, k, v)
                db.commit()

    def save_new_trade(self, trade: Trade):
        with get_session() as db:
            db.add(trade)
            db.commit()
            db.refresh(trade)
            return trade

trade_state_repo = TradeStateRepository()
