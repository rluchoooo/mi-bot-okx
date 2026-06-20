import asyncio
from trade_state_repository import trade_state_repo
from trailing_manager import trailing_manager
from discord_notifier import discord_notifier
from order_execution_engine import OrderExecutionEngine

class PositionManager:
    def __init__(self, execution_engine: OrderExecutionEngine, okx_client):
        self.execution_engine = execution_engine
        self.client = okx_client

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

            # Update highest/lowest
            if t.side == "long":
                highest = max(t.highest_price or current_price, current_price)
                if highest != t.highest_price:
                    trade_state_repo.update_trade(t.id, highest_price=highest)
            else:
                lowest = min(t.lowest_price or current_price, current_price) if (t.lowest_price or 0) > 0 else current_price
                if lowest != t.lowest_price:
                    trade_state_repo.update_trade(t.id, lowest_price=lowest)

            # 1. Check Stop Loss Hit (OKX cierra nativamente, solo marcamos local o dejamos que el auditor sincronice)
            if self._is_sl_hit(t, current_price):
                # No enviamos orden, OKX se encarga. Podemos limpiar la DB si queremos,
                # pero el auditor de scanner.py limpiará los cerrados de todos modos.
                continue

            # 2. Check TP1 (Ejecutado por OKX, solo marcamos en DB para saber en qué etapa estamos)
            if not t.tp1_filled and self._is_tp_hit(t, current_price, t.tp1_price):
                qty_to_close = t.position_size * 0.30
                rem = t.remaining_size - qty_to_close
                trade_state_repo.update_trade(t.id, tp1_filled=1, remaining_size=rem)
                await discord_notifier.log_tp1(t.symbol, current_price)

            # 3. Check Profit Lock (Mover el SL nativo a la entrada)
            if not t.profit_lock_active and self._is_tp_hit(t, current_price, t.profit_lock_price):
                trade_state_repo.update_trade(t.id, profit_lock_active=1, sl_price=t.profit_lock_sl)
                await self.execution_engine.modify_native_sl(t.symbol, t.side, t.profit_lock_sl)
                await discord_notifier.log_profit_lock(t.symbol, t.profit_lock_sl)

            # 4. Check TP2 (Ejecutado por OKX nativamente)
            if t.tp1_filled and not t.tp2_filled and self._is_tp_hit(t, current_price, t.tp2_price):
                qty_to_close = t.position_size * 0.30
                rem = t.remaining_size - qty_to_close
                trade_state_repo.update_trade(t.id, tp2_filled=1, remaining_size=rem, trailing_active=1)
                await discord_notifier.log_tp2(t.symbol, current_price)

            # 5. Check Trailing
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
        if t.side == "long":
            return price <= t.sl_price
        return price >= t.sl_price

    def _is_tp_hit(self, t, price: float, target: float) -> bool:
        if not target: return False
        if t.side == "long":
            return price >= target
        return price <= target

    async def _close_position(self, t, price: float, reason: str):
        success = await self.execution_engine.execute_tp_closure(t.symbol, t.side, t.remaining_size)
        if success:
            trade_state_repo.update_trade(t.id, position_closed=1, remaining_size=0, close_price=price, close_reason=reason)
            await discord_notifier.log_close(t.symbol, price, reason)
