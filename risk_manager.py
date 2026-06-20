from decimal import Decimal

class RiskManager:
    MAX_SL_MULTIPLIER = 2.0
    MIN_SL_MULTIPLIER = 1.0
    TP_MULTIPLIER = 5.0
    
    @staticmethod
    def calculate_levels(entry_price: float, atr: float, side: str, ct_val: float = 1.0, lot_sz: float = 1.0, strategy: str = "") -> dict:
        sl_distance = atr * 2.0
        
        if side == "long":
            sl = entry_price - sl_distance
            tp1_price = entry_price + (atr * 1.2)
            tp2_price = entry_price + (atr * 2.4)
            profit_lock_trigger = entry_price + (atr * 1.33)
            tp_final = entry_price + (atr * 4.0)
        else:
            sl = entry_price + sl_distance
            tp1_price = entry_price - (atr * 1.2)
            tp2_price = entry_price - (atr * 2.4)
            profit_lock_trigger = entry_price - (atr * 1.33)
            tp_final = entry_price - (atr * 4.0)
            
        return {
            "entry_price": entry_price,
            "atr": atr,
            "sl_price": sl,
            "tp_final": tp_final,
            "tp1_price": tp1_price,
            "tp2_price": tp2_price,
            "profit_lock_trigger": profit_lock_trigger,
            "profit_lock_sl": entry_price # Se mueve al entry
        }

risk_manager = RiskManager()
