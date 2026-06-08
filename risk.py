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
    TRAILING_ACTIVATION_PCT, TRAILING_RETAIN_PCT,
)

# Re-export for backward compatibility
RISK_USD          = FIXED_RISK_USDT
BE_TRIGGER_PCT    = BREAKEVEN_ACTIVATION_PCT
TRAIL_TRIGGER_PCT = TRAILING_ACTIVATION_PCT
BE_LOCK_PCT       = BREAKEVEN_PROFIT_PCT


def compute_sl(entry: Decimal, side: str, atr: Decimal) -> Decimal:
    """Stop Loss = entry ± 2.5 × ATR (Para SMC V10 Pro)."""
    distance = ATR_MULTIPLIER_SL * atr
    return entry - distance if side == "long" else entry + distance


def compute_tp(entry: Decimal, sl: Decimal, side: str) -> Decimal:
    """Take Profit = Ratio 1:2 (2x SL distance)."""
    distance = abs(entry - sl) * Decimal("2.0")
    return entry + distance if side == "long" else entry - distance


def compute_qty(
    entry: Decimal,
    sl: Decimal,
    ct_val: Decimal,
    lot_sz: Decimal,
    risk_usd: Decimal = FIXED_RISK_USDT,
) -> Decimal:
    """
    qty = risk_usd / (|entry - sl| × ct_val)
    Redondeado hacia abajo al lot_sz del instrumento.
    """
    sl_distance = abs(entry - sl)
    if sl_distance == 0:
        return Decimal("0")
    qty_raw = risk_usd / (sl_distance * ct_val)
    return qty_raw.quantize(lot_sz, rounding=ROUND_DOWN)


def breakeven_sl(entry: Decimal, side: str, tp_dist: Decimal) -> Decimal:
    """
    SL de breakeven = entrada + (BREAKEVEN_PROFIT_PCT × tp_dist).
    Asegura un 15% de la distancia total al TP.
    """
    lock = BREAKEVEN_PROFIT_PCT * tp_dist
    return entry + lock if side == "long" else entry - lock


def new_trail_sl(
    entry: Decimal,
    peak_price: Decimal,
    side: str,
    current_sl: Decimal,
) -> Decimal:
    """
    Nuevo trailing stop: retiene el 65% de la ganancia máxima (nunca retrocede).
    """
    max_gain = abs(peak_price - entry)
    retained = max_gain * TRAILING_RETAIN_PCT
    candidate = entry + retained if side == "long" else entry - retained
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
