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

# ── SCANNER ───────────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 15
TOP_COINS_LIMIT       = 50
MIN_VOLUME_24H        = 500_000       # USDT mínimo de volumen 24h
LIMIT_ORDER_OFFSET_PCT = Decimal("0.0002")  # ±0.02% offset en precio límite

# ── SÍMBOLOS EXCLUIDOS ────────────────────────────────────────────────
DISALLOWED_BASES = {
    "XAU", "XAG", "WTI", "BRENT", "COPPER",
    "USDC", "BUSD", "DAI", "TUSD", "USDP",
    "EUR", "GBP",
}

# ── INDICADORES ───────────────────────────────────────────────────────
RSI_PERIOD  = 14
RSI_MIN     = 35
RSI_MAX     = 65
RSI_DIV_MIN_DIFF = 2.0      # Diferencia mínima entre pivotes RSI para confirmar divergencia

ATR_PERIOD  = 14
ADX_PERIOD  = 14
ADX_MIN     = 20

EMA_FAST    = 9
EMA_MID     = 21
EMA_SLOW    = 100
EMA_TREND   = 50             # Usado para el bias 1H/15M en Strategy A

# ── SL / TP ───────────────────────────────────────────────────────────
ATR_MULTIPLIER_SL = Decimal("2.5")
ATR_MULTIPLIER_TP = Decimal("5.0")   # Ratio exacto 1:2

# ── BREAKEVEN ─────────────────────────────────────────────────────────
BREAKEVEN_ACTIVATION_PCT = Decimal("0.50")
BREAKEVEN_PROFIT_PCT     = Decimal("0.15")   # 15% del beneficio objetivo asegurado

# ── TRAILING STOP ─────────────────────────────────────────────────────
TRAILING_ACTIVATION_PCT = Decimal("0.75")
TRAILING_DISTANCE_PCT   = Decimal("0.15")    # correa = 15% de distancia al TP
TRAIL_RETRY_SECONDS     = 10                 # Reintento si exchange rechaza orden

# ── SALIDA TEMPRANA (Early Exit) ──────────────────────────────────────
EARLY_EXIT_SL_PCT           = Decimal("0.40")
EARLY_EXIT_VOL_MULT         = 1.8
EARLY_EXIT_LOOKBACK_MINUTES = 20

# ── FILTRO BTC (Escudo Macro) ─────────────────────────────────────────
BTC_MAX_VOLATILITY_PCT    = 0.015
BTC_BLOCK_SECONDS         = 10_800   # 3 horas
BTC_REMINDER_INTERVAL_SEC = 60       # Log de recordatorio cada 60s mientras bloqueado

# ── COOLDOWN / ÓRDENES ────────────────────────────────────────────────
COOLDOWN_MINUTES      = 30
STALE_ORDER_MINUTES   = 10
RECONCILE_INTERVAL    = 30
RECONCILE_RETRY_SEC   = 10           # Espera entre reintentos en reconcile

# ── TELEGRAM ─────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
