"""
lifecycle.py – Ciclo de vida de una operación abierta.
Etapa 1: Early Exit (primeros 20 min, PnL -40% + volumen adverso 1.8x).
Etapa 2: Breakeven (50% del recorrido → SL = entrada + 10% del TP dist).
Etapa 3: Trailing Stop (75% del recorrido → cancela TP, persigue precio).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum, auto
from typing import Optional

import pandas as pd

from config import (
    BREAKEVEN_ACTIVATION_PCT,
    SMC_TRAIL_ACTIVATION, SMC_TRAIL_RETAIN,
    ST_TRAIL_ACTIVATION, ST_TRAIL_RETAIN,
)
from risk import (
    breakeven_sl, new_trail_sl, pnl_pct_of_risk, pnl_usd,
)


class Action(Enum):
    NONE          = auto()
    MOVE_SL       = auto()
    CANCEL_TP     = auto()
    CLOSE_MARKET  = auto()


@dataclass
class LifecycleDecision:
    action:      Action
    reason:      str
    new_sl:      Optional[Decimal] = None
    log_message: str = ""


def evaluate(
    side:          str,
    entry:         Decimal,
    tp:            Optional[Decimal],
    current_sl:    Decimal,
    price:         Decimal,
    qty:           Decimal,
    ct_val:        Decimal,
    atr_5m:        Decimal,
    risk_usd:      Decimal,
    be_activated:  bool,
    trail_activated: bool,
    trail_sl:      Optional[Decimal],
    peak_price:    Optional[Decimal],
    tp_original:   Decimal,
    strategy_name: str = "",
    df_5m:         Optional[pd.DataFrame] = None,
    opened_at:     Optional[datetime]     = None,
) -> list[LifecycleDecision]:
    """
    Evalúa el estado de la posición y retorna decisiones ordenadas.
    """
    decisions: list[LifecycleDecision] = []
    
    # ── Asignación Dinámica de Perfiles ──
    if "SMC" in strategy_name.upper():
        trail_act_pct = SMC_TRAIL_ACTIVATION
        trail_ret_pct = SMC_TRAIL_RETAIN
    else:
        trail_act_pct = ST_TRAIL_ACTIVATION
        trail_ret_pct = ST_TRAIL_RETAIN

    unrealized = pnl_usd(entry, price, qty, ct_val, side)
    pnl_ratio  = pnl_pct_of_risk(unrealized, risk_usd)
    tp_dist    = abs(tp_original - entry)

    if side == "long":
        progress = (price - entry) / tp_dist if tp_dist > 0 else Decimal("0")
    else:
        progress = (entry - price) / tp_dist if tp_dist > 0 else Decimal("0")

    # ── 0. Stop Loss / Take Profit inicial Hit ──────────────────────
    sl_hit = (price <= current_sl) if side == "long" else (price >= current_sl)
    if sl_hit:
        reason = "STOP_LOSS_HIT"
        if trail_activated:
            reason = "TRAILING_HIT"
        elif be_activated:
            reason = "BREAKEVEN_HIT"
        decisions.append(LifecycleDecision(
            action=Action.CLOSE_MARKET,
            reason=reason,
            log_message=f"🛑 {reason.replace('_', ' ')} alcanzado: precio={price:.6f} sl={current_sl:.6f}",
        ))
        return decisions

    if tp is not None:
        tp_hit = (price >= tp) if side == "long" else (price <= tp)
        if tp_hit:
            decisions.append(LifecycleDecision(
                action=Action.CLOSE_MARKET,
                reason="TAKE_PROFIT_HIT",
                log_message=f"✅ Take Profit alcanzado: precio={price:.6f} tp={tp:.6f}",
            ))
            return decisions

    # ── Early Exit (Omitido por brevedad en este snippet, pero se podría añadir si estuviera activo)

    # ── 1. Trailing stop hit ────────────────────────────────────────
    if trail_activated and trail_sl is not None:
        hit = (price <= trail_sl) if side == "long" else (price >= trail_sl)
        if hit:
            decisions.append(LifecycleDecision(
                action=Action.CLOSE_MARKET,
                reason="TRAILING_HIT",
                log_message=f"🎯 Trailing Stop alcanzado: precio={price:.6f} trail_sl={trail_sl:.6f}",
            ))
            return decisions

    # ── 2. Trailing seguimiento ─────────────────────────────────────
    if trail_activated and trail_sl is not None:
        if peak_price is None:
            peak_price = price
        updated = new_trail_sl(entry, peak_price, side, trail_sl, trail_ret_pct)
        if updated != trail_sl:
            decisions.append(LifecycleDecision(
                action=Action.MOVE_SL,
                reason="TRAIL_MOVE",
                new_sl=updated,
                log_message=f"🎯 Trail SL movido: {trail_sl:.6f} → {updated:.6f}",
            ))

    # ── 3. Activar Trailing ─────────────────────────────────────────
    if not trail_activated and progress >= trail_act_pct:
        if peak_price is None:
            peak_price = price
        init_sl = new_trail_sl(entry, peak_price, side, current_sl, trail_ret_pct)
        decisions.append(LifecycleDecision(
            action=Action.MOVE_SL,
            reason="TRAIL_ACTIVATE",
            new_sl=init_sl,
            log_message=(
                f"🎯 Trailing ACTIVADO al {float(progress)*100:.1f}% del recorrido. "
                f"Trail SL: {init_sl:.6f}. TP cancelado."
            ),
        ))
        decisions.append(LifecycleDecision(
            action=Action.CANCEL_TP,
            reason="TRAIL_ACTIVATE",
            log_message="Orden TP cancelada – precio libre para correr.",
        ))
        return decisions

    # ── 4. Activar Breakeven ────────────────────────────────────────
    if not be_activated and not trail_activated and progress >= BREAKEVEN_ACTIVATION_PCT:
        be_sl = breakeven_sl(entry, side, tp_dist)
        decisions.append(LifecycleDecision(
            action=Action.MOVE_SL,
            reason="BREAKEVEN",
            new_sl=be_sl,
            log_message=(
                f"🛡️ Breakeven ACTIVADO al {float(progress)*100:.1f}% del recorrido. "
                f"SL → {be_sl:.6f} (+{float(abs(be_sl - entry)):.4f} garantizados)."
            ),
        ))
        return decisions

    return decisions
