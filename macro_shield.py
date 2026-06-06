"""
macro_shield.py – Escudo Macroeconómico Anti-Shock basado en volatilidad de BTC 5M.
Si la vela de 5 minutos de BTC supera 1.5% de rango, bloquea nuevas entradas 3 horas.
"""
from __future__ import annotations

import time
from decimal import Decimal

VOLATILITY_THRESHOLD: float = 0.015   # 1.5%
BLOCK_SECONDS: int = 10_800           # 3 horas


class MacroShield:
    def __init__(self) -> None:
        self._blocked_until: float = 0.0
        self._last_trigger_reason: str = ""

    @property
    def is_blocked(self) -> bool:
        return time.time() < self._blocked_until

    @property
    def remaining_seconds(self) -> int:
        remaining = self._blocked_until - time.time()
        return max(0, int(remaining))

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
        Retorna True si el escudo se activó (nuevo bloqueo), False si está libre.
        """
        if btc_close <= 0:
            return False
        volatility = (btc_high - btc_low) / btc_close
        if volatility > VOLATILITY_THRESHOLD:
            self._blocked_until = time.time() + BLOCK_SECONDS
            pct = volatility * 100
            self._last_trigger_reason = f"BTC volatilidad {pct:.2f}% > 1.5%"
            return True
        return False

    def force_clear(self) -> None:
        """Permite forzar el desbloqueo manual desde el dashboard."""
        self._blocked_until = 0.0
        self._last_trigger_reason = ""
