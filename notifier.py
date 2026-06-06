"""
notifier.py – Notificaciones Telegram para el Quantum V10 Pro Bot.
Envía alertas de apertura, cierre, errores críticos y bloqueos macro.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from config import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN

_log = logging.getLogger(__name__)


class TelegramNotifier:
    BASE = "https://api.telegram.org"

    def __init__(self) -> None:
        self._token   = TELEGRAM_TOKEN
        self._chat_id = TELEGRAM_CHAT_ID
        self._enabled = bool(self._token and self._chat_id)

    def is_enabled(self) -> bool:
        return self._enabled

    async def send(self, text: str) -> None:
        if not self._enabled:
            return
        url = f"{self.BASE}/bot{self._token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
        except Exception as e:
            _log.warning(f"[Telegram] Error enviando mensaje: {e}")

    # ── Plantillas ────────────────────────────────────────────────────

    async def notify_open(
        self,
        symbol: str, side: str, strategy: str,
        entry: float, sl: float, tp: float, qty: float,
    ) -> None:
        icon = "🟢" if side == "long" else "🔴"
        await self.send(
            f"{icon} <b>APERTURA {side.upper()}</b>\n"
            f"📌 <b>{symbol}</b> | {strategy}\n"
            f"💰 Entrada: <code>{entry:.6f}</code>\n"
            f"🛑 SL: <code>{sl:.6f}</code>\n"
            f"🎯 TP: <code>{tp:.6f}</code>\n"
            f"📦 Qty: <code>{qty}</code> contratos"
        )

    async def notify_close(
        self,
        symbol: str, side: str, reason: str,
        entry: float, close_price: float, pnl: float,
    ) -> None:
        icon = "✅" if pnl >= 0 else "❌"
        sign = "+" if pnl >= 0 else ""
        await self.send(
            f"{icon} <b>CIERRE {side.upper()}</b>\n"
            f"📌 <b>{symbol}</b>\n"
            f"📋 Razón: <code>{reason}</code>\n"
            f"💰 E: <code>{entry:.6f}</code> → S: <code>{close_price:.6f}</code>\n"
            f"💵 PnL: <b>{sign}{pnl:.2f} USDT</b>"
        )

    async def notify_breakeven(self, symbol: str, new_sl: float) -> None:
        await self.send(
            f"🛡️ <b>BREAKEVEN ACTIVADO</b>\n"
            f"📌 <b>{symbol}</b>\n"
            f"SL movido a: <code>{new_sl:.6f}</code>"
        )

    async def notify_trailing(self, symbol: str, trail_sl: float) -> None:
        await self.send(
            f"🎯 <b>TRAILING STOP ACTIVADO</b>\n"
            f"📌 <b>{symbol}</b>\n"
            f"Trail SL: <code>{trail_sl:.6f}</code> | TP cancelado"
        )

    async def notify_macro_block(self, reason: str, minutes_remaining: int) -> None:
        await self.send(
            f"🚨 <b>ALARMA MACRO – CORTAFUEGOS ACTIVADO</b>\n"
            f"📋 {reason}\n"
            f"⏱️ BLOQUEANDO OPERACIONES POR 3 HORAS\n"
            f"⏳ Tiempo restante: <b>{minutes_remaining} minutos</b>"
        )

    async def notify_error(self, context: str, error: str) -> None:
        await self.send(
            f"⚠️ <b>ERROR CRÍTICO</b>\n"
            f"📋 Contexto: {context}\n"
            f"❌ <code>{error[:300]}</code>"
        )

    async def notify_stale_cancel(self, symbol: str, order_id: str) -> None:
        await self.send(
            f"🗑️ <b>Orden STALE cancelada</b>\n"
            f"📌 {symbol} | ID: <code>{order_id}</code>\n"
            f"⏱️ Sin ejecución tras {10} minutos"
        )


# Singleton global
notifier = TelegramNotifier()
