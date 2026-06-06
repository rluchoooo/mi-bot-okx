"""
risk.py – Calculadora de tamaño de posición y niveles de SL/TP.
Riesgo fijo: $8 USDT por operación con apalancamiento 10x.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal


RISK_USD: Decimal = Decimal("8.0")
LEVERAGE: int = 10
ATR_SL_MULT: Decimal = Decimal("2.5")
ATR_TP_MULT: Decimal = Decimal("5.0")

# Lifecycle thresholds
BE_TRIGGER_PCT: Decimal = Decimal("0.50")    # 50% toward TP activates breakeven
TRAIL_TRIGGER_PCT: Decimal = Decimal("0.75") # 75% toward TP activates trailing
TRAIL_DISTANCE_PCT: Decimal = Decimal("0.15") # trailing correa = 15% of TP distance
BE_LOCK_USD: Decimal = Decimal("1.6")        # $1.60 guaranteed after breakeven
EARLY_EXIT_PCT: Decimal = Decimal("-0.40")   # -40% of risk = -$3.20 triggers early exit
MIN_GUARANTEED_PCT: Decimal = Decimal("0.60") # 60% of TP on trailing = $9.60 min


def compute_sl(entry: Decimal, side: str, atr: Decimal) -> Decimal:
    """Stop Loss = entry ± 2.5 × ATR."""
    distance = ATR_SL_MULT * atr
    return entry - distance if side == "long" else entry + distance


def compute_tp(entry: Decimal, side: str, atr: Decimal) -> Decimal:
    """Take Profit = entry ± 5.0 × ATR (1:2 R/R)."""
    distance = ATR_TP_MULT * atr
    return entry + distance if side == "long" else entry - distance


def compute_qty(
    entry: Decimal,
    sl: Decimal,
    ct_val: Decimal,
    lot_sz: Decimal,
    risk_usd: Decimal = RISK_USD,
) -> Decimal:
    """
    Calcula el tamaño exacto en contratos.
    qty = risk_usd / |entry - sl| / ct_val
    Redondea hacia abajo al lot_sz del instrumento.
    """
    sl_distance = abs(entry - sl)
    if sl_distance == 0:
        return Decimal("0")
    qty_raw = risk_usd / (sl_distance * ct_val)
    return qty_raw.quantize(lot_sz, rounding=ROUND_DOWN)


def breakeven_sl(entry: Decimal, side: str) -> Decimal:
    """SL de breakeven = entrada + $1.60 / entrada - $1.60."""
    return entry + BE_LOCK_USD if side == "long" else entry - BE_LOCK_USD


def trail_distance(tp_distance: Decimal) -> Decimal:
    """Correa del trailing stop = 15% de la distancia total al TP."""
    return TRAIL_DISTANCE_PCT * tp_distance


def new_trail_sl(
    price: Decimal,
    side: str,
    tp_dist: Decimal,
    current_sl: Decimal,
) -> Decimal:
    """
    Calcula el nuevo trailing stop.
    Para LONG: sube si el precio sube.
    Para SHORT: baja si el precio baja.
    Nunca retrocede (monotone).
    """
    dist = trail_distance(tp_dist)
    candidate = price - dist if side == "long" else price + dist
    if side == "long":
        return max(candidate, current_sl)
    return min(candidate, current_sl)


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


def pnl_pct_of_risk(unrealized_pnl: Decimal, risk_usd: Decimal = RISK_USD) -> Decimal:
    """Retorna el PnL como fracción del riesgo máximo."""
    if risk_usd == 0:
        return Decimal("0")
    return unrealized_pnl / risk_usd
