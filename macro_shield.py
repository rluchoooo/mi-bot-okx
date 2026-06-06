"""
macro_shield.py – Escudo Macroeconómico Anti-Shock basado en volatilidad de BTC 5M.
Si la vela de 5 minutos de BTC supera 1.5% de rango, bloquea nuevas entradas 3 horas.
Emite recordatorio en logs cada 60 segundos mientras está activo.
"""
from __future__ import annotations

import time

from config import BTC_BLOCK_SECONDS, BTC_MAX_VOLATILITY_PCT, BTC_REMINDER_INTERVAL_SEC


class MacroShield:
    def __init__(self) -> None:
        self._blocked_until:     float = 0.0
        self._last_reminder_at:  float = 0.0
        self._last_trigger_reason: str = ""

    @property
    def is_blocked(self) -> bool:
        return time.time() < self._blocked_until

    @property
    def remaining_seconds(self) -> int:
        return max(0, int(self._blocked_until - time.time()))

    @property
    def remaining_minutes(self) -> int:
        return self.remaining_seconds // 60

    @property
    def status_label(self) -> str:
        if self.is_blocked:
            mins = self.remaining_seconds // 60
            secs = self.remaining_seconds % 60
            return f"🔴 BLOQUEADO {mins}m {secs}s – {self._last_trigger_reason}"
        return "🟢 LIBRE – Sin shocks detectados"

    def evaluate(self, btc_high: float, btc_low: float, btc_close: float) -> bool:
        """
        Evalúa la vela de 5M de BTC.
        Retorna True si el escudo se activó (nuevo bloqueo).
        """
        if btc_close <= 0:
            return False
        volatility = (btc_high - btc_low) / btc_close
        if volatility > BTC_MAX_VOLATILITY_PCT:
            self._blocked_until       = time.time() + BTC_BLOCK_SECONDS
            self._last_reminder_at    = 0.0   # fuerza aviso inmediato
            pct = volatility * 100
            self._last_trigger_reason = f"BTC volatilidad {pct:.2f}% > 1.5%"
            return True
        return False

    def should_send_reminder(self) -> bool:
        """
        Retorna True si es momento de enviar el recordatorio periódico (cada 60s).
        Actualiza el timestamp interno cuando retorna True.
        """
        if not self.is_blocked:
            return False
        now = time.time()
        if now - self._last_reminder_at >= BTC_REMINDER_INTERVAL_SEC:
            self._last_reminder_at = now
            return True
        return False

    def reminder_message(self) -> str:
        mins = self.remaining_minutes
        return (
            f"🚨 CORTAFUEGOS ACTIVO: Faltan {mins} minutos para desbloquear. "
            f"Scanner de entradas suspendido. ({self._last_trigger_reason})"
        )

    def force_clear(self) -> None:
        """Desbloqueo manual desde el dashboard."""
        self._blocked_until       = 0.0
        self._last_trigger_reason = ""
        self._last_reminder_at    = 0.0
