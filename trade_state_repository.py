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
                db.refresh(t)
                
                # Check if it was closed to log it
                if "position_closed" in kwargs and kwargs["position_closed"] == 1:
                    try:
                        from csv_logger import log_trade_to_csv
                        log_trade_to_csv(t)
                    except Exception:
                        pass

    def save_new_trade(self, trade: Trade):
        with get_session() as db:
            db.add(trade)
            db.commit()
            db.refresh(trade)
            
            # CEREBRO: Save the strategy to a persistent JSON file so it survives SQLite wipes
            import json, os
            cerebro_path = "cerebro.json"
            cerebro_data = {}
            if os.path.exists(cerebro_path):
                try:
                    with open(cerebro_path, "r", encoding="utf-8") as f:
                        cerebro_data = json.load(f)
                except Exception:
                    pass
            cerebro_data[trade.symbol] = trade.strategy
            try:
                with open(cerebro_path, "w", encoding="utf-8") as f:
                    json.dump(cerebro_data, f)
            except Exception:
                pass
                
            try:
                from csv_logger import log_trade_to_csv
                log_trade_to_csv(trade)
            except Exception:
                pass
                
            return trade

trade_state_repo = TradeStateRepository()
