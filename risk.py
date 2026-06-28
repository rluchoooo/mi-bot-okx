"""
risk.py – Calculadora de tamaño de posición y niveles de SL/TP.
Riesgo fijo: $8 USDT por operación con apalancamiento 10x (config.py).
"""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from config import (
    ATR_MULTIPLIER_SL,
    BREAKEVEN_ACTIVATION_PCT, BREAKEVEN_PROFIT_PCT,
    FIXED_RISK_USDT,
    LEVERAGE,
    MAX_POSITION_VAL_USDT,
)

# Re-export for backward compatibility
RISK_USD          = FIXED_RISK_USDT
BE_TRIGGER_PCT    = BREAKEVEN_ACTIVATION_PCT
BE_LOCK_PCT       = BREAKEVEN_PROFIT_PCT


def compute_sl(entry: Decimal, side: str, atr: Decimal) -> Decimal:
    """Stop Loss = entry ± 2.0 × ATR."""
    distance = Decimal("2.0") * atr
    return entry - distance if side == "long" else entry + distance


def compute_tp1(entry: Decimal, side: str, atr: Decimal) -> Decimal:
    """Take Profit 1 = 2.5 ATR."""
    distance = Decimal("2.5") * atr
    return entry + distance if side == "long" else entry - distance


def compute_tp2(entry: Decimal, side: str, atr: Decimal) -> Decimal:
    """Take Profit 2 = 5.0 ATR."""
    distance = Decimal("5.0") * atr
    return entry + distance if side == "long" else entry - distance


def compute_qty(
    entry: Decimal,
    sl: Decimal,
    ct_val: Decimal,
    lot_sz: Decimal,
    risk_usd: Decimal = FIXED_RISK_USDT,
) -> Decimal:
    """
    Calcula el número de contratos garantizando un tamaño nominal fijo:
    Margen de $15 USD * Apalancamiento 10x = $150 USDT Nominales.
    """
    if ct_val == 0 or entry == 0:
        return Decimal("0")

    from config import LEVERAGE
    nominal_size = risk_usd * Decimal(str(LEVERAGE))
    
    # qty × ctVal × entry = nominal_size
    qty_raw = nominal_size / (entry * ct_val)
    qty_final = qty_raw.quantize(lot_sz, rounding=ROUND_DOWN)
    
    # Si el redondeo lo dejó en 0 pero se puede comprar al menos 1 lote
    if qty_final == 0 and qty_raw > 0:
        qty_final = lot_sz

    return qty_final




def breakeven_sl(entry: Decimal, side: str, atr: Decimal = Decimal("0"), tp_dist: Decimal = Decimal("0")) -> Decimal:
    """
    SL de breakeven: asegura un 12% de ROE de ganancia al activarse.
    """
    from config import LEVERAGE
    # 12% ROE = 12 / LEVERAGE % de movimiento de precio
    price_movement_pct = Decimal("12.0") / Decimal(str(LEVERAGE)) / Decimal("100")
    lock = entry * price_movement_pct
    return entry + lock if side == "long" else entry - lock


def new_trail_sl(
    entry: Decimal,
    peak_price: Decimal,
    side: str,
    current_sl: Decimal,
    retain_pct: Decimal,
) -> Decimal:
    """
    Nuevo trailing stop: retiene el % de la ganancia máxima especificado.
    """
    max_gain = abs(peak_price - entry)
    retained = max_gain * retain_pct
    candidate = entry + retained if side == "long" else entry - retained
    return max(candidate, current_sl) if side == "long" else min(candidate, current_sl)


def new_trail_sl_fixed(
    peak_price: Decimal,
    side: str,
    current_sl: Decimal,
    atr: Decimal = Decimal("0"),
) -> Decimal:
    """
    Trailing stop a 1.2 ATR de distancia desde el pico máximo.
    Si ATR = 0 (fallback), usa 1% del precio del pico.
    El SL nunca retrocede (solo avanza con el precio).
    """
    TRAIL_ATR_MULT = Decimal("1.2")
    if atr > 0:
        distance = TRAIL_ATR_MULT * atr
    else:
        from config import TRAILING_FIXED_PCT
        distance = peak_price * TRAILING_FIXED_PCT
    candidate = peak_price - distance if side == "long" else peak_price + distance
    return max(candidate, current_sl) if side == "long" else min(candidate, current_sl)


def pnl_usd(
    entry: Decimal,
    price: Decimal,
    qty: Decimal,
    ct_val: Decimal,
    side: str,
) -> Decimal:
    """PnL no realizado en USDT (sin comisiones)."""
    raw = (price - entry) * qty * ct_val
    return raw if side == "long" else -raw


def pnl_pct_of_risk(unrealized_pnl: Decimal, risk_usd: Decimal = FIXED_RISK_USDT) -> Decimal:
    """PnL como fracción del riesgo máximo."""
    if risk_usd == 0:
        return Decimal("0")
    return unrealized_pnl / risk_usd
