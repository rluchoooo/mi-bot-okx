from trade_state_repository import trade_state_repo
from models import Trade, TradeSide
from discord_notifier import discord_notifier

class ExchangeSynchronizer:
    def __init__(self, okx_client):
        self.client = okx_client

    async def sync_orphaned_trades(self):
        """
        Sincroniza posiciones de OKX que no existen en la BD.
        """
        try:
            okx_pos = await self.client.get_positions()
            for p in okx_pos:
                sym = p.get("instId")
                existing = trade_state_repo.get_trade_by_symbol(sym)
                if not existing:
                    # Adopt it
                    pos_side = p.get("posSide", "").lower()
                    side = TradeSide.LONG if pos_side == "long" else TradeSide.SHORT
                    entry = float(p.get("avgPx", 0))
                    qty = float(p.get("pos", 0))
                    
                    t = Trade(
                        symbol=sym,
                        side=side,
                        strategy="AUTO_ADOPTED",
                        entry_price=entry,
                        position_size=qty,
                        remaining_size=qty,
                        sl_price=entry * (0.95 if side == TradeSide.LONG else 1.05),
                        tp_price=entry * (1.10 if side == TradeSide.LONG else 0.90),
                        atr=entry * 0.01,
                        leverage=int(p.get("lever", 10))
                    )
                    trade_state_repo.save_new_trade(t)
                    await discord_notifier.log_reconnect(f"[{sym}] Posición huérfana adoptada.")
        except Exception as e:
            await discord_notifier.log_error("ExchangeSynchronizer", str(e))
