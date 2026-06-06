"""
lifecycle.py – Ciclo de vida de una operación abierta.
Gestiona: Early Exit, Breakeven, Trailing Stop.
Retorna acciones a ejecutar (no ejecuta directamente para mantener testabilidad).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from typing import Optional

from risk import (
    BE_TRIGGER_PCT,
    EARLY_EXIT_PCT,
    TRAIL_TRIGGER_PCT,
    breakeven_sl,
    new_trail_sl,
    pnl_pct_of_risk,
    pnl_usd,
    trail_distance,
)


class Action(Enum):
    NONE        = auto()
    MOVE_SL     = auto()   # Move stop-loss (breakeven or trailing)
    CANCEL_TP   = auto()   # Cancel TP limit order when trailing activates
    CLOSE_MARKET = auto()  # Close at market (early exit or trailing hit)


@dataclass
class LifecycleDecision:
    action: Action
    reason: str
    new_sl: Optional[Decimal] = None
    log_message: str = ""


def evaluate(
    side: str,
    entry: Decimal,
    tp: Optional[Decimal],
    current_sl: Decimal,
    price: Decimal,
    qty: Decimal,
    ct_val: Decimal,
    atr_5m: Decimal,
    risk_usd: Decimal,
    be_activated: bool,
    trail_activated: bool,
    trail_sl: Optional[Decimal],
    peak_price: Optional[Decimal],
    tp_original: Decimal,         # always the original TP (even when tp=None after trailing)
) -> list[LifecycleDecision]:
    """
    Evalúa el estado de la posición y retorna una lista de decisiones ordenadas.
    Llamar una vez por tick (cada 3s en el fast loop).
    """
    decisions: list[LifecycleDecision] = []

    unrealized = pnl_usd(entry, price, qty, ct_val, side)
    pnl_ratio  = pnl_pct_of_risk(unrealized, risk_usd)

    # Distancia total entry→TP original
    tp_dist = abs(tp_original - entry)

    # Cuánto ha recorrido el precio hacia el TP (en fracción 0–1+)
    if side == "long":
        progress = (price - entry) / tp_dist if tp_dist > 0 else Decimal("0")
    else:
        progress = (entry - price) / tp_dist if tp_dist > 0 else Decimal("0")

    # ── 1. TRAILING STOP HIT (si trailing activo) ──────────────────────────
    if trail_activated and trail_sl is not None:
        hit = (price <= trail_sl) if side == "long" else (price >= trail_sl)
        if hit:
            decisions.append(LifecycleDecision(
                action=Action.CLOSE_MARKET,
                reason="TRAILING_HIT",
                log_message=f"Trailing Stop alcanzado: precio={price:.6f} trail_sl={trail_sl:.6f}",
            ))
            return decisions  # nothing else matters

    # ── 2. TRAILING STOP SEGUIMIENTO ───────────────────────────────────────
    if trail_activated and trail_sl is not None:
        updated_sl = new_trail_sl(price, side, tp_dist, trail_sl)
        if updated_sl != trail_sl:
            decisions.append(LifecycleDecision(
                action=Action.MOVE_SL,
                reason="TRAIL_MOVE",
                new_sl=updated_sl,
                log_message=f"Trailing SL movido: {trail_sl:.6f} → {updated_sl:.6f}",
            ))

    # ── 3. ACTIVAR TRAILING (75% del recorrido) ────────────────────────────
    if not trail_activated and progress >= TRAIL_TRIGGER_PCT:
        init_trail_sl = new_trail_sl(price, side, tp_dist, current_sl)
        decisions.append(LifecycleDecision(
            action=Action.MOVE_SL,
            reason="TRAIL_ACTIVATE",
            new_sl=init_trail_sl,
            log_message=(
                f"🎯 Trailing ACTIVADO al {float(progress)*100:.1f}% del recorrido. "
                f"Trail SL inicial: {init_trail_sl:.6f}. TP cancelado."
            ),
        ))
        decisions.append(LifecycleDecision(
            action=Action.CANCEL_TP,
            reason="TRAIL_ACTIVATE",
            log_message="Orden TP cancelada – el trailing persigue el precio.",
        ))
        return decisions

    # ── 4. ACTIVAR BREAKEVEN (50% del recorrido) ───────────────────────────
    if not be_activated and not trail_activated and progress >= BE_TRIGGER_PCT:
        be_sl = breakeven_sl(entry, side)
        decisions.append(LifecycleDecision(
            action=Action.MOVE_SL,
            reason="BREAKEVEN",
            new_sl=be_sl,
            log_message=(
                f"🛡️ Breakeven ACTIVADO al {float(progress)*100:.1f}% del recorrido. "
                f"SL movido a {be_sl:.6f} (+${float(be_sl - entry if side == 'long' else entry - be_sl):.2f} garantizados)."
            ),
        ))
        return decisions

    # ── 5. EARLY EXIT (-40% del riesgo) ────────────────────────────────────
    if not be_activated and not trail_activated and pnl_ratio <= EARLY_EXIT_PCT:
        decisions.append(LifecycleDecision(
            action=Action.CLOSE_MARKET,
            reason="EARLY_EXIT",
            log_message=(
                f"⚡ Early Exit activado: PnL {float(unrealized):.2f} USDT "
                f"({float(pnl_ratio)*100:.1f}% del riesgo). Estructura fallida."
            ),
        ))

    return decisions
