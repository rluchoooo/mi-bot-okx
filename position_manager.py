import asyncio
from trade_state_repository import trade_state_repo
from trailing_manager import trailing_manager
from discord_notifier import discord_notifier
from order_execution_engine import OrderExecutionEngine

class PositionManager:
    def __init__(self, execution_engine: OrderExecutionEngine, okx_client):
        self.execution_engine = execution_engine
        self.client = okx_client
        self._cooldown_map: dict[str, float] = {}  # symbol -> cooldown_until_ts

    async def run_supervisor(self):
        """
        Bucle infinito del Supervisor de Seguridad (ejecuta cada 5 segundos).
        """
        while True:
            try:
                await self.check_positions()
            except Exception as e:
                await discord_notifier.log_error("PositionManager.run_supervisor", str(e))
            await asyncio.sleep(5)

    def is_in_cooldown(self, symbol: str) -> bool:
        import time
        until = self._cooldown_map.get(symbol, 0)
        return time.time() < until

    def _apply_cooldown(self, symbol: str):
        import time
        self._cooldown_map[symbol] = time.time() + 3600  # 1 hora

    async def check_positions(self):
        trades = trade_state_repo.get_open_trades()
        if not trades:
            return
            
        tickers = await self.client.tickers()
        price_map = {t.get("instId"): float(t.get("last", 0)) for t in tickers}

        for t in trades:
            current_price = price_map.get(t.symbol)
            if not current_price:
                continue

            side_str = "long" if (t.side.value if hasattr(t.side, "value") else str(t.side)).lower() in ("long", "tradeside.long") else "short"

            # Update highest/lowest
            if side_str == "long":
                highest = max(t.highest_price or current_price, current_price)
                if highest != t.highest_price:
                    trade_state_repo.update_trade(t.id, highest_price=highest)
            else:
                lowest = min(t.lowest_price or current_price, current_price) if (t.lowest_price or 0) > 0 else current_price
                if lowest != t.lowest_price:
                    trade_state_repo.update_trade(t.id, lowest_price=lowest)

            # 1. Check Stop Loss Hit (OKX cierra nativamente)
            if self._is_sl_hit(t, current_price):
                # Si el SL se toca SIN haber llegado a Breakeven ni TPs -> Cooldown 1 hora
                if not t.profit_lock_active and not t.tp1_filled:
                    self._apply_cooldown(t.symbol)
                continue

            strat_str = t.strategy.value if hasattr(t.strategy, "value") else str(t.strategy)
            strat_str = strat_str.replace("Strategy.", "")
            is_mtf = strat_str in ("ST_EMA_REGIME_MTF_PRO", "AUTO_ADOPTED")
            is_ag = strat_str == "ANTIGRAVITY_V13_PRO"

            # 2. Check TP1 (Solo para Antigravity: cierra 30%)
            if is_ag and not t.tp1_filled and self._is_tp_hit(t, current_price, t.tp1_price):
                qty_to_close = t.position_size * 0.30
                rem = t.remaining_size - qty_to_close
                trade_state_repo.update_trade(t.id, tp1_filled=1, remaining_size=rem)
                await discord_notifier.log_tp1(t.symbol, current_price)

            # Calculate profit lock levels dynamically (12% ROE at 10x leverage = 1.2% spot move)
            roe_offset = t.entry_price * 0.012
            profit_lock_sl = t.entry_price + roe_offset if side_str == "long" else t.entry_price - roe_offset

            # 3. Check Profit Lock (Mover el SL nativo a entry + 12% ROE)
            if not t.profit_lock_active and self._is_tp_hit(t, current_price, t.profit_lock_price):
                trade_state_repo.update_trade(t.id, profit_lock_active=1, sl_price=profit_lock_sl)
                await self.execution_engine.modify_native_sl(t.symbol, t.side, profit_lock_sl)
                await discord_notifier.log_profit_lock(t.symbol, profit_lock_sl)

            # 4. Check TP2 & Trailing Activation
            if not t.trailing_active:
                if is_ag:
                    # Antigravity: TP2 cierra 30%, activa trailing en el 40% runner
                    if t.tp1_filled and not t.tp2_filled and self._is_tp_hit(t, current_price, t.tp2_price):
                        qty_to_close = t.position_size * 0.30
                        rem = t.remaining_size - qty_to_close
                        trade_state_repo.update_trade(t.id, tp2_filled=1, remaining_size=rem, trailing_active=1)
                        await discord_notifier.log_tp2(t.symbol, current_price)
                else:
                    # SuperTrend/AUTO: activate trailing at 2.5 ATR without partials
                    trail_trigger = t.entry_price + (t.atr * 2.5) if side_str == "long" else t.entry_price - (t.atr * 2.5)
                    if self._is_tp_hit(t, current_price, trail_trigger):
                        trade_state_repo.update_trade(t.id, trailing_active=1)

            # 5. Check Trailing (EMA21 dynamic trailing for both strategies)
            if t.trailing_active:
                new_sl = trailing_manager.calculate_trailing_stop(
                    t.side, t.sl_price, current_price, 
                    t.highest_price or current_price, 
                    t.lowest_price or current_price, 
                    t.atr
                )
                if new_sl != t.sl_price:
                    trade_state_repo.update_trade(t.id, sl_price=new_sl)
                    await self.execution_engine.modify_native_sl(t.symbol, t.side, new_sl)
                    await discord_notifier.log_trailing(t.symbol, new_sl)

    def _is_sl_hit(self, t, price: float) -> bool:
        side_str = "long" if (t.side.value if hasattr(t.side, "value") else str(t.side)).lower() in ("long", "tradeside.long") else "short"
        if side_str == "long":
            return price <= t.sl_price
        return price >= t.sl_price

    def _is_tp_hit(self, t, price: float, target: float) -> bool:
        if not target: return False
        side_str = "long" if (t.side.value if hasattr(t.side, "value") else str(t.side)).lower() in ("long", "tradeside.long") else "short"
        if side_str == "long":
            return price >= target
        return price <= target

    async def _close_position(self, t, price: float, reason: str):
        success = await self.execution_engine.execute_tp_closure(t.symbol, t.side, t.remaining_size)
        if success:
            trade_state_repo.update_trade(t.id, position_closed=1, remaining_size=0, close_price=price, close_reason=reason)
            await discord_notifier.log_close(t.symbol, price, reason)
