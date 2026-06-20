class TrailingManager:
    @staticmethod
    def calculate_trailing_stop(side: str, current_sl: float, current_price: float, highest_price: float, lowest_price: float, atr: float) -> float:
        """
        Calculates the new Trailing Stop.
        Trailing distance is exactly 1.2 ATR.
        The trailing stop NEVER moves backwards.
        """
        atr_distance = atr * 1.2
        
        if side == "long":
            best_price = max(highest_price, current_price)
            theoretical_sl = best_price - atr_distance
            # Solo avanza
            new_sl = max(current_sl, theoretical_sl)
            return new_sl
        else:
            best_price = min(lowest_price, current_price) if lowest_price > 0 else current_price
            theoretical_sl = best_price + atr_distance
            # Solo avanza
            new_sl = min(current_sl, theoretical_sl)
            return new_sl

trailing_manager = TrailingManager()
