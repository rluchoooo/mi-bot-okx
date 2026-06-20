import os
import httpx
import json
from datetime import datetime, timezone

class DiscordNotifier:
    def __init__(self):
        self.webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")

    async def _send(self, title: str, description: str, color: int = 3447003):
        if not self.webhook_url:
            return
            
        payload = {
            "embeds": [{
                "title": title,
                "description": description,
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }]
        }
        
        try:
            async with httpx.AsyncClient() as client:
                await client.post(self.webhook_url, json=payload, timeout=5.0)
        except Exception:
            pass # Fails silently for notifier

    async def log_entry(self, symbol: str, side: str, price: float, atr: float):
        await self._send(
            f"🟢 ENTRADA {side.upper()} | {symbol}", 
            f"Precio: {price:.6f}\nATR: {atr:.6f}",
            color=3066993 if side == "long" else 15158332
        )

    async def log_tp1(self, symbol: str, price: float):
        await self._send(f"✅ TP1 EJECUTADO | {symbol}", f"Precio: {price:.6f}\nCerrado: 30%", color=3447003)

    async def log_tp2(self, symbol: str, price: float):
        await self._send(f"🚀 TP2 EJECUTADO | {symbol}", f"Precio: {price:.6f}\nCerrado: 30%", color=1752220)

    async def log_profit_lock(self, symbol: str, new_sl: float):
        await self._send(f"🔒 PROFIT LOCK ACTIVADO | {symbol}", f"Nuevo Stop Loss: {new_sl:.6f}", color=10181046)

    async def log_trailing(self, symbol: str, new_sl: float):
        await self._send(f"🏃 TRAILING STOP ACTUALIZADO | {symbol}", f"Nuevo Stop Loss: {new_sl:.6f}", color=15844367)

    async def log_close(self, symbol: str, price: float, reason: str):
        await self._send(f"🛑 CIERRE FINAL | {symbol}", f"Precio: {price:.6f}\nRazón: {reason}", color=10038562)

    async def log_error(self, location: str, error: str):
        await self._send(f"❌ ERROR | {location}", f"Mensaje: {error}", color=15158332)
        
    async def log_reconnect(self, message: str):
        await self._send(f"🔄 RECONEXIÓN", message, color=15105570)

discord_notifier = DiscordNotifier()
