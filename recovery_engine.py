from exchange_synchronizer import ExchangeSynchronizer
from discord_notifier import discord_notifier

class RecoveryEngine:
    def __init__(self, synchronizer: ExchangeSynchronizer):
        self.synchronizer = synchronizer

    async def run_recovery(self):
        """
        Ejecutado al iniciar el bot. 
        Revisa posiciones huérfanas y reconstruye estados.
        """
        await discord_notifier.log_reconnect("Iniciando Recovery Engine...")
        await self.synchronizer.sync_orphaned_trades()
        await discord_notifier.log_reconnect("Recovery Engine completado. Posiciones sincronizadas.")
