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
        self.shock_direction: str = ""

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

    def evaluate(self, open_px: float, high: float, low: float, close: float) -> bool:
        """
        Evalúa si la vela de BTC supera el umbral de volatilidad.
        Si lo supera, bloquea el bot por 3 horas y calcula la dirección del shock.
        """
        if low <= 0:
            return False
        range_pct = (high - low) / low
        if range_pct >= float(BTC_MAX_VOLATILITY_PCT):
            self._blocked_until = time.time() + BTC_BLOCK_SECONDS
            self._last_reminder_at = 0.0
            self.shock_direction = "bullish" if close > open_px else "bearish"
            trend_str = "ALCISTA (Pump)" if self.shock_direction == "bullish" else "BAJISTA (Dump)"
            self._last_trigger_reason = f"Shock de BTC {trend_str} > {float(BTC_MAX_VOLATILITY_PCT)*100:.2f}% ({range_pct*100:.2f}%)"
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
