"""
lifecycle.py – Ciclo de vida de una operación con matemática 30/30/40.

Regla matemática exacta (100% = 8.33 ATR):
  SL         = entry - 2.0 ATR      (riesgo máximo)
  TP1        = entry + 1.2 ATR  →  30% del recorrido → cierra 30% del volumen
  Breakeven  = entry + 1.33 ATR →  33.3% del recorrido → mueve SL a entrada protegida (+0.6 ATR)
  TP2        = entry + 2.4 ATR  →  60% del recorrido → cierra 30% + activa Trailing
  Objetivo   = Infinito → Trailing persigue el precio sin límite hasta que se devuelva y toque el SL.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum, auto
from typing import Optional

import pandas as pd
from risk import pnl_pct_of_risk, pnl_usd, new_trail_sl_fixed, breakeven_sl


class Action(Enum):
    NONE          = auto()
    MOVE_SL       = auto()
    CANCEL_TP     = auto()
    CLOSE_MARKET  = auto()
    CLOSE_PARTIAL = auto()


@dataclass
class LifecycleDecision:
    action:      Action
    reason:      str
    new_sl:      Optional[Decimal] = None
    log_message: str = ""


# ── Constantes de la regla 30/30/40 ───────────────────────────────────────
ATR_SL        = Decimal("2.0")    # SL a 2.0 ATR
ATR_TP1       = Decimal("1.2")    # TP1 a 1.2 ATR  → 30% del objetivo total (4.0)
ATR_BREAKEVEN = Decimal("1.32")   # Breakeven a 1.32 ATR → 33% del objetivo total
ATR_TP2       = Decimal("2.4")    # TP2 a 2.4 ATR  → 60% del objetivo total
ATR_TARGET    = Decimal("4.0")    # Referencia teórica (Trailing infinito en la práctica)


def _compute_levels(entry: Decimal, atr: Decimal, side: str) -> dict:
    """Calcula todos los niveles desde la entrada con la regla 30/30/40."""
    if side == "long":
        return {
            "sl":        entry - ATR_SL        * atr,
            "tp1":       entry + ATR_TP1       * atr,
            "be":        entry + ATR_BREAKEVEN * atr,
            "tp2":       entry + ATR_TP2       * atr,
            "target":    entry + ATR_TARGET    * atr,
        }
    else:
        return {
            "sl":        entry + ATR_SL        * atr,
            "tp1":       entry - ATR_TP1       * atr,
            "be":        entry - ATR_BREAKEVEN * atr,
            "tp2":       entry - ATR_TP2       * atr,
            "target":    entry - ATR_TARGET    * atr,
        }

def _ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=p, adjust=False).mean()


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
    strategy_name: str = "",
    df_5m:         Optional[pd.DataFrame] = None,
    opened_at:     Optional[datetime]     = None,
    tp1_done:      bool = False,
    tp2_done:      bool = False,
) -> list[LifecycleDecision]:
    """
    Evalúa el estado de la posición con la regla 30/30/40:
      30% del recorrido → TP1 → Cierra 30%
      33.3% del recorrido → Breakeven → Mueve SL a +15% de la distancia total (0.6 ATR)
      60% del recorrido → TP2 → Cierra 30% + Activa Trailing
     Infinito → Trailing persigue sin límite hasta ser tocado
    """
    decisions: list[LifecycleDecision] = []
    lvl = _compute_levels(entry, atr_5m, side)
    tp1_price = lvl["tp1"]
    tp2_price = lvl["tp2"]
    be_price  = lvl["be"]

    in_profit = (price > entry) if side == "long" else (price < entry)

    # ── 0. Stop Loss Hit ──────────────────────────────────────────────────
    sl_hit = (price <= current_sl) if side == "long" else (price >= current_sl)
    if sl_hit:
        reason = "TRAILING_HIT" if trail_activated else "STOP_LOSS_HIT"
        decisions.append(LifecycleDecision(
            action=Action.CLOSE_MARKET,
            reason=reason,
            log_message=f"🛑 {reason.replace('_', ' ')}: precio={price:.6f} sl={current_sl:.6f}",
        ))
        return decisions

    # ── 0.5 Trailing SL hit (precio cruza trailing) ────────────────────────
    if trail_activated and trail_sl is not None:
        trail_hit = (price <= trail_sl) if side == "long" else (price >= trail_sl)
        if trail_hit:
            decisions.append(LifecycleDecision(
                action=Action.CLOSE_MARKET,
                reason="TRAILING_HIT",
                log_message=f"🎯 Trailing alcanzado: precio={price:.6f} trail_sl={trail_sl:.6f}",
            ))
            return decisions

    # ── 1. Trailing seguimiento (mueve SL con la EMA21 o el pico) ─────────────────────
    if trail_activated and trail_sl is not None:
        updated = trail_sl
        if df_5m is not None and not df_5m.empty:
            ema21 = _ema(df_5m['close'], 21).iloc[-1]
            candidate = Decimal(str(ema21))
            updated = max(candidate, trail_sl) if side == "long" else min(candidate, trail_sl)
        else:
            if peak_price is None:
                peak_price = price
            updated = new_trail_sl_fixed(peak_price, side, trail_sl, atr=atr_5m)

        if updated != trail_sl:
            decisions.append(LifecycleDecision(
                action=Action.MOVE_SL,
                reason="TRAIL_MOVE",
                new_sl=updated,
                log_message=f"🎯 Trail SL: {trail_sl:.6f} → {updated:.6f}",
            ))

    # ── 2. Breakeven (40% del recorrido = 3.33 ATR) ────────────────────────
    if not be_activated and not trail_activated and in_profit:
        be_reached = (price >= be_price) if side == "long" else (price <= be_price)
        if be_reached:
            # SL se mueve a entrada + 0.33 ATR (blindaje mínimo)
            new_sl = breakeven_sl(entry, side, atr=atr_5m)
            is_better = (new_sl > current_sl) if side == "long" else (new_sl < current_sl)
            if is_better:
                decisions.append(LifecycleDecision(
                    action=Action.MOVE_SL,
                    reason="BREAKEVEN_ACTIVATE",
                    new_sl=new_sl,
                    log_message=(
                        f"🛡️ BREAKEVEN activado (1.32 ATR = 33%). "
                        f"SL blindado: {current_sl:.6f} → {new_sl:.6f} (entrada + 0.4 ATR)"
                    ),
                ))

    # ── 3. TP1 Hit (1.2 ATR → cierra 30%) ────────────────────────────────
    if not tp1_done and not trail_activated:
        hit_tp1 = (price >= tp1_price) if side == "long" else (price <= tp1_price)
        if hit_tp1:
            decisions.append(LifecycleDecision(
                action=Action.CLOSE_PARTIAL,
                reason="TP1_HIT",
                log_message=f"🎯 TP1 (1.2 ATR) alcanzado: {price:.6f} ≥ {tp1_price:.6f} → cierra 30%",
            ))

    # ── 4. TP2 Hit (2.4 ATR → cierra 30% + activa Trailing) ──────────────
    if tp1_done and not tp2_done and not trail_activated:
        hit_tp2 = (price >= tp2_price) if side == "long" else (price <= tp2_price)
        if hit_tp2:
            decisions.append(LifecycleDecision(
                action=Action.CLOSE_PARTIAL,
                reason="TP2_HIT",
                log_message=f"🚀 TP2 (2.4 ATR) alcanzado: {price:.6f} → cierra 30% + activa Runner",
            ))

            # Iniciar Trailing desde la EMA21 o el pico actual
            init_sl = current_sl
            if df_5m is not None and not df_5m.empty:
                ema21 = _ema(df_5m['close'], 21).iloc[-1]
                init_sl = Decimal(str(ema21))
            else:
                if peak_price is None:
                    peak_price = price
                init_sl = new_trail_sl_fixed(peak_price, side, current_sl, atr=atr_5m)

            # Salvaguarda: trailing nunca baja del breakeven (entrada + 0.33 ATR)
            min_secure = breakeven_sl(entry, side, atr=atr_5m)
            init_sl = max(init_sl, min_secure) if side == "long" else min(init_sl, min_secure)

            decisions.append(LifecycleDecision(
                action=Action.MOVE_SL,
                reason="TRAIL_ACTIVATE",
                new_sl=init_sl,
                log_message=(
                    f"🏃 Trailing ACTIVADO. SL inicial: {init_sl:.6f} "
                    f"(persiguiendo ganancias de forma infinita hasta que el precio retroceda y toque el trailing)"
                ),
            ))

    return decisions
