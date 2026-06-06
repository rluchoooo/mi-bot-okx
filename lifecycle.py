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
    BREAKEVEN_ACTIVATION_PCT, EARLY_EXIT_LOOKBACK_MINUTES,
    EARLY_EXIT_SL_PCT, EARLY_EXIT_VOL_MULT,
    TRAILING_ACTIVATION_PCT,
)
from risk import (
    breakeven_sl, new_trail_sl, pnl_pct_of_risk, pnl_usd, trail_distance,
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


def _volume_confirms_exit(df_5m: Optional[pd.DataFrame], side: str) -> bool:
    """
    Retorna True si el volumen adverso en las últimas velas es ≥ 1.8x el promedio.
    Volumen adverso para LONG = velas bajistas; para SHORT = velas alcistas.
    """
    if df_5m is None or df_5m.empty or len(df_5m) < 5:
        return False
    recent = df_5m.tail(5)
    avg_vol = df_5m["volume"].mean()
    if avg_vol <= 0:
        return False
    if side == "long":
        adverse_vols = recent.loc[recent["close"] < recent["open"], "volume"]
    else:
        adverse_vols = recent.loc[recent["close"] > recent["open"], "volume"]
    if adverse_vols.empty:
        return False
    return float(adverse_vols.mean()) >= avg_vol * EARLY_EXIT_VOL_MULT


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
    df_5m:         Optional[pd.DataFrame] = None,
    opened_at:     Optional[datetime]     = None,
) -> list[LifecycleDecision]:
    """
    Evalúa el estado de la posición y retorna decisiones ordenadas.
    """
    decisions: list[LifecycleDecision] = []

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
        decisions.append(LifecycleDecision(
            action=Action.CLOSE_MARKET,
            reason="STOP_LOSS_HIT",
            log_message=f"🛑 Stop Loss alcanzado: precio={price:.6f} sl={current_sl:.6f}",
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

    # ── MAX LOSS (Asfixia) ──────────────────────────────────────────
    from config import MAX_ABSOLUTE_LOSS
    if unrealized < MAX_ABSOLUTE_LOSS:
        decisions.append(LifecycleDecision(
            action=Action.CLOSE_MARKET,
            reason="MAX_LOSS_ASFIXIA",
            log_message=f"💀 Asfixia (Pérdida Crítica): PnL={unrealized:.2f} USDT < {MAX_ABSOLUTE_LOSS}. Cierre IOC inmediato.",
        ))
        return decisions

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
        updated = new_trail_sl(price, side, atr_5m, trail_sl)
        if updated != trail_sl:
            decisions.append(LifecycleDecision(
                action=Action.MOVE_SL,
                reason="TRAIL_MOVE",
                new_sl=updated,
                log_message=f"🎯 Trail SL movido: {trail_sl:.6f} → {updated:.6f}",
            ))

    # ── 3. Activar Trailing (75%) ───────────────────────────────────
    if not trail_activated and progress >= TRAILING_ACTIVATION_PCT:
        init_sl = new_trail_sl(price, side, atr_5m, current_sl)
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

    # ── 4. Activar Breakeven (50%) ──────────────────────────────────
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

    # ── 5. Early Exit (primeros 20 min, -40% riesgo + volumen adverso) ──
    if (
        not be_activated
        and not trail_activated
        and pnl_ratio <= -EARLY_EXIT_SL_PCT
    ):
        # Verificar ventana de tiempo
        in_window = True
        if opened_at is not None:
            opened_at_utc = opened_at.replace(tzinfo=timezone.utc) if opened_at.tzinfo is None else opened_at
            mins_open = (datetime.now(timezone.utc) - opened_at_utc).total_seconds() / 60
            in_window = mins_open <= EARLY_EXIT_LOOKBACK_MINUTES

        if in_window and _volume_confirms_exit(df_5m, side):
            decisions.append(LifecycleDecision(
                action=Action.CLOSE_MARKET,
                reason="EARLY_VOLUME_CUT",
                log_message=(
                    f"⚡ Early Exit: PnL {float(unrealized):.2f} USDT "
                    f"({float(pnl_ratio)*100:.1f}% riesgo) + volumen adverso ≥1.8x. "
                    f"Estructura fallida."
                ),
            ))

    return decisions
