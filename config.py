"""
config.py – Parámetros centralizados del Quantum V10 Pro Bot.
Todos los módulos importan desde aquí. Cambiar un valor aquí lo aplica en todo el sistema.
"""
import os
from decimal import Decimal

# ── RIESGO ────────────────────────────────────────────────────────────
FIXED_RISK_USDT       = Decimal("8.0")
LEVERAGE              = 10
MAX_CONCURRENT_TRADES = 10
SAME_SYMBOL_ONLY      = False
DAILY_LOSS_LIMIT_USDT = Decimal("999999")  # Desactivado – cambiar para producción real
MAX_POSITION_VAL_USDT = Decimal("800.0")   # Salvaguarda: nominal máximo en USDT para evitar 'insufficient balance'

# ── SCANNER ───────────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 15
TOP_COINS_LIMIT       = 50
MIN_VOLUME_24H        = 100_000       # USDT mínimo de volumen 24h
LIMIT_ORDER_OFFSET_PCT = Decimal("0.0002")  # ±0.02% offset en precio límite

# ── SÍMBOLOS EXCLUIDOS ────────────────────────────────────────────────
DISALLOWED_BASES = {
    "XAU", "XAG", "WTI", "BRENT", "COPPER",
    "USDC", "BUSD", "DAI", "TUSD", "USDP",
    "EUR", "GBP",
}

# ── INDICADORES ───────────────────────────────────────────────────────
RSI_PERIOD  = 14
RSI_MIN     = 25
RSI_MAX     = 75
RSI_DIV_MIN_DIFF = 1.0      # Diferencia mínima entre pivotes RSI para confirmar divergencia

ATR_PERIOD  = 14
ADX_PERIOD  = 14
ADX_MIN     = 8

EMA_FAST    = 9
EMA_MID     = 21
EMA_SLOW    = 100
EMA_TREND   = 100             # Usado para el bias de tendencia macro en la Estrategia B

# ── SL / TP ───────────────────────────────────────────────────────────
ATR_MULTIPLIER_SL = Decimal("2.0")

BREAKEVEN_ACTIVATION_PCT = Decimal("0.40")   # Se activa al llegar al 40% del objetivo (TP2)
BREAKEVEN_PROFIT_PCT     = Decimal("0.15")   # Asegura un 15% de ganancia del objetivo

TP1_RR_MULT              = Decimal("1.2")    # TP1 at 1.2x Risk
TP1_QTY_PCT              = Decimal("0.30")   # Close 30% of position

TP2_RR_MULT              = Decimal("2.0")    # TP2 at 2.0x Risk
TP2_QTY_PCT              = Decimal("0.30")   # Close another 30% of position (leaves 40%)

TRAILING_FIXED_PCT       = Decimal("0.010")  # Trailing distance = 1.0% fijo desde el pico máximo


TRAIL_RETRY_SECONDS  = 10                    # Reintento si exchange rechaza orden

# ── SUPERTREND ────────────────────────────────────────────────────────
SUPERTREND_FACTOR = 3.0
SUPERTREND_PERIOD = 10

# ── FILTRO BTC (Escudo Macro) ─────────────────────────────────────────
BTC_MAX_VOLATILITY_PCT    = 0.02
BTC_BLOCK_SECONDS         = 7200   # 2 horas
BTC_REMINDER_INTERVAL_SEC = 60       # Log de recordatorio cada 60s mientras bloqueado

# ── COOLDOWN / ÓRDENES ────────────────────────────────────────────────
COOLDOWN_MINUTES      = 60
STALE_ORDER_MINUTES   = 15
RECONCILE_INTERVAL    = 1
RECONCILE_RETRY_SEC   = 10           # Espera entre reintentos en reconcile

# ── TELEGRAM ─────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
