"""
strategy.py – Motor Dual de Señales: Quantum Trend V10 Pro + Quantum Divergence.
Usa EMA50 para el bias macro, ADX≥20 para filtro de tendencia,
FVG con entrada en el PUNTO MEDIO del gap, y divergencia con min 2pts RSI.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd

from config import (
    ADX_MIN, ADX_PERIOD, ATR_PERIOD,
    EMA_FAST, EMA_MID, EMA_SLOW, EMA_TREND,
    LIMIT_ORDER_OFFSET_PCT,
    RSI_DIV_MIN_DIFF, RSI_MAX, RSI_MIN, RSI_PERIOD,
)


# ──────────────────────────────────────────────
# Data class
# ──────────────────────────────────────────────

@dataclass
class Signal:
    symbol:      str
    side:        str          # "long" | "short"
    strategy:    str          # "QUANTUM_V10_PRO" | "QUANTUM_DIVERGENCE"
    entry_price: Decimal      # limit order price with offset
    atr_5m:      Decimal
    reason:      str
    score:       float = 0.0


# ──────────────────────────────────────────────
# Indicator helpers
# ──────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    rsi.loc[(loss == 0) & (gain > 0)] = 100
    rsi.loc[(gain == 0) & (loss > 0)] = 0
    return rsi.fillna(50)


def _atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _adx(df: pd.DataFrame, period: int = ADX_PERIOD) -> pd.Series:
    """Calcula ADX para filtrar tendencias débiles."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_s = tr.ewm(alpha=1 / period, adjust=False).mean()

    up_move   = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm   = pd.Series(np.where((up_move > down_move)   & (up_move > 0),   up_move,   0.0), index=df.index)
    minus_dm  = pd.Series(np.where((down_move > up_move)   & (down_move > 0), down_move, 0.0), index=df.index)

    atr_nz   = atr_s.replace(0, np.nan)
    plus_di  = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_nz
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_nz
    dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0)


def _find_fvg_midpoint(df_5m: pd.DataFrame, side: str, lookback: int = 5) -> Optional[Decimal]:
    """
    Busca un Fair Value Gap (FVG) en las últimas `lookback` velas de 5M.
    Retorna el PUNTO MEDIO del gap (no el borde) como precio de entrada.

    FVG BULLISH: low[i+2] > high[i]  → midpoint = (high[i] + low[i+2]) / 2
    FVG BEARISH: high[i+2] < low[i]  → midpoint = (low[i] + high[i+2]) / 2
    """
    df = df_5m.tail(lookback + 2).reset_index(drop=True)
    for i in range(len(df) - 2):
        left  = df.iloc[i]
        right = df.iloc[i + 2]
        if side == "long" and right["low"] > left["high"]:
            mid = (left["high"] + right["low"]) / 2
            return Decimal(str(round(mid, 8)))
        if side == "short" and right["high"] < left["low"]:
            mid = (left["low"] + right["high"]) / 2
            return Decimal(str(round(mid, 8)))
    return None


def _apply_offset(price: Decimal, side: str) -> Decimal:
    """
    Aplica un offset del 0.02% al precio límite para asegurar el fill.
    LONG: sube ligeramente para cruzar asks. SHORT: baja ligeramente.
    """
    offset = price * LIMIT_ORDER_OFFSET_PCT
    return price + offset if side == "long" else price - offset


# ──────────────────────────────────────────────
# Strategy A: QUANTUM_V10_PRO (Trend + FVG)
# ──────────────────────────────────────────────

class QuantumTrendStrategy:
    """
    15M → EMA 50 → Bias LONG/SHORT + RSI + ADX → Filtro de momentum y fuerza.
    5M  → FVG midpoint → Entrada con offset 0.02%.
    """
    NAME = "QUANTUM_V10_PRO"

    def signal(
        self,
        symbol:  str,
        df_15m:  pd.DataFrame,
        df_5m:   pd.DataFrame,
    ) -> Optional[Signal]:
        if df_15m.empty or df_5m.empty:
            return None
        if len(df_15m) < 55 or len(df_5m) < 16:
            return None

        # ── 15M macro bias (EMA 50) ──────────────────────────────────
        ema50_15m = _ema(df_15m["close"], EMA_TREND).iloc[-1]
        close_15m = df_15m["close"].iloc[-1]
        bias = "long" if close_15m > ema50_15m else "short"

        # ── 15M momentum + ADX filter ───────────────────────────────
        rsi_15m   = _rsi(df_15m["close"]).iloc[-1]
        adx_15m   = _adx(df_15m).iloc[-1]

        if adx_15m < ADX_MIN:
            return None  # tendencia demasiado débil

        if bias == "long":
            if rsi_15m >= RSI_MAX:
                return None
        else:
            if rsi_15m <= RSI_MIN:
                return None

        # ── 5M FVG sniper (punto medio) ─────────────────────────────
        atr_5m = _atr(df_5m).iloc[-1]
        if atr_5m <= 0:
            return None

        mid = _find_fvg_midpoint(df_5m, bias, lookback=15)
        if mid is None:
            return None

        entry = _apply_offset(mid, bias)
        score = (abs(rsi_15m - 50) / 50) + (min(adx_15m, 50) / 100)

        return Signal(
            symbol=symbol, side=bias, strategy=self.NAME,
            entry_price=entry, atr_5m=Decimal(str(atr_5m)),
            reason=f"EMA50 bias={bias} | RSI15m={rsi_15m:.1f} | ADX={adx_15m:.1f} | FVG mid={float(mid):.6f}",
            score=score,
        )


# ──────────────────────────────────────────────
# Strategy B: QUANTUM_DIVERGENCE (RSI Divergence + FVG)
# ──────────────────────────────────────────────

class QuantumDivergenceStrategy:
    """
    15M → Divergencia RSI/Precio en ventana de 30 velas (mínimo 2pts RSI).
    5M  → FVG confirmador (punto medio + offset 0.02%).
    """
    NAME   = "QUANTUM_DIVERGENCE"
    WINDOW = 30

    def signal(
        self,
        symbol: str,
        df_15m: pd.DataFrame,
        df_5m:  pd.DataFrame,
    ) -> Optional[Signal]:
        if df_15m.empty or df_5m.empty:
            return None
        if len(df_15m) < self.WINDOW + 5 or len(df_5m) < 16:
            return None

        df = df_15m.tail(self.WINDOW).reset_index(drop=True)
        df["rsi"] = _rsi(df["close"])

        rsi_now  = float(df["rsi"].iloc[-1])
        price_now_low  = float(df["low"].iloc[-1])
        price_now_high = float(df["high"].iloc[-1])

        bullish_div = False
        bearish_div = False

        # ── Bullish: precio hace LL, RSI hace HL, RSI < 40 ──────────
        if rsi_now < 40:
            prev_low_idx = df["low"].iloc[:-3].idxmin()
            prev_rsi_at_low = float(df["rsi"].iloc[prev_low_idx])
            prev_price_low  = float(df["low"].iloc[prev_low_idx])
            rsi_diff = rsi_now - prev_rsi_at_low
            if (price_now_low < prev_price_low        # precio hace LL
                    and rsi_diff >= RSI_DIV_MIN_DIFF):  # RSI hace HL con ≥2pts
                bullish_div = True

        # ── Bearish: precio hace HH, RSI hace LH, RSI > 60 ─────────
        if rsi_now > 60:
            prev_high_idx = df["high"].iloc[:-3].idxmax()
            prev_rsi_at_high = float(df["rsi"].iloc[prev_high_idx])
            prev_price_high  = float(df["high"].iloc[prev_high_idx])
            rsi_diff = prev_rsi_at_high - rsi_now
            if (price_now_high > prev_price_high      # precio hace HH
                    and rsi_diff >= RSI_DIV_MIN_DIFF):  # RSI hace LH con ≥2pts
                bearish_div = True

        if not bullish_div and not bearish_div:
            return None

        side = "long" if bullish_div else "short"

        # ── 5M FVG confirmador ───────────────────────────────────────
        atr_5m = _atr(df_5m).iloc[-1]
        if atr_5m <= 0:
            return None

        mid = _find_fvg_midpoint(df_5m, side, lookback=15)
        if mid is None:
            return None

        entry = _apply_offset(mid, side)
        div_label = "Bullish Div" if bullish_div else "Bearish Div"
        score = abs(rsi_now - 50) / 50 + 0.15   # bonus por ser señal de reversión

        return Signal(
            symbol=symbol, side=side, strategy=self.NAME,
            entry_price=entry, atr_5m=Decimal(str(atr_5m)),
            reason=f"{div_label} | RSI15m={rsi_now:.1f} | FVG mid={float(mid):.6f}",
            score=score,
        )
