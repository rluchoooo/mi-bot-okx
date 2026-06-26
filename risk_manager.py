from decimal import Decimal

class RiskManager:
    @staticmethod
    def calculate_levels(entry_price: float, atr: float, side: str, ct_val: float = 1.0, lot_sz: float = 1.0, strategy: str = "", signal_sl: float = None, signal_tp: float = None) -> dict:
        is_mtf = strategy in ("ST_EMA_REGIME_MTF_PRO", "AUTO_ADOPTED")
        is_ag = strategy == "ANTIGRAVITY_V13_PRO"
        
        # Base SL for both is 2.5 ATR
        sl_distance = atr * 2.5
        
        # Max 8.5% loss cap for SL
        max_sl_dist = entry_price * 0.085
        if sl_distance > max_sl_dist:
            sl_distance = max_sl_dist
            
        # 15% ROE offset is 1.5% spot move at 10x leverage
        roe_offset = entry_price * 0.015
        
        if side == "long":
            sl = entry_price - sl_distance
            tp_final = None # Both use Trailing Stop for final closure
            
            if is_ag:
                tp1_price = entry_price + (atr * 1.5)
                tp2_price = entry_price + (atr * 3.0)
                profit_lock_trigger = entry_price + (entry_price * 0.0333) # 33.3% ROE
            else:
                tp1_price = None
                tp2_price = None
                profit_lock_trigger = entry_price + (atr * 1.5)
                
            profit_lock_sl = entry_price + roe_offset
        else:
            sl = entry_price + sl_distance
            tp_final = None
            
            if is_ag:
                tp1_price = entry_price - (atr * 1.5)
                tp2_price = entry_price - (atr * 3.0)
                profit_lock_trigger = entry_price - (entry_price * 0.0333)
            else:
                tp1_price = None
                tp2_price = None
                profit_lock_trigger = entry_price - (atr * 1.5)
                
            profit_lock_sl = entry_price - roe_offset
            
        return {
            "entry_price": entry_price,
            "atr": atr,
            "sl_price": sl,
            "tp_final": tp_final,
            "tp1_price": tp1_price,
            "tp2_price": tp2_price,
            "profit_lock_trigger": profit_lock_trigger,
            "profit_lock_sl": profit_lock_sl
        }

risk_manager = RiskManager()
