"""
strategy.py – Motor Dual de Señales: Quantum Trend V10 Pro + Quantum Divergence.
Ambas estrategias buscan un FVG (Fair Value Gap) de 5M como gatillo sniper.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd


# ──────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────

@dataclass
class Signal:
    symbol: str
    side: str                # "long" | "short"
    strategy: str            # "QUANTUM_V10_PRO" | "QUANTUM_DIVERGENCE"
    entry_price: Decimal
    atr_5m: Decimal
    reason: str
    score: float = 0.0       # Higher = higher priority if multiple signals


# ──────────────────────────────────────────────
# Indicator helpers
# ──────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi.loc[(loss == 0) & (gain > 0)] = 100
    rsi.loc[(gain == 0) & (loss > 0)] = 0
    return rsi.fillna(50)


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _find_fvg(df_5m: pd.DataFrame, side: str, lookback: int = 5) -> Optional[Decimal]:
    """
    Busca un Fair Value Gap (FVG) en las últimas `lookback` velas de 5M.
    FVG BULLISH: low[i+2] > high[i] → precio de entrada = high[i] (borde inferior del FVG)
    FVG BEARISH: high[i+2] < low[i] → precio de entrada = low[i] (borde superior del FVG)
    Retorna el precio de entrada si se detecta, o None.
    """
    df = df_5m.tail(lookback + 2).reset_index(drop=True)
    for i in range(len(df) - 2):
        candle_left  = df.iloc[i]
        candle_right = df.iloc[i + 2]
        if side == "long":
            # Bullish FVG: hay un hueco entre high de vela izquierda y low de vela derecha
            if candle_right["low"] > candle_left["high"]:
                return Decimal(str(candle_left["high"]))  # entrada en el borde
        else:
            # Bearish FVG: hueco entre low de izquierda y high de derecha
            if candle_right["high"] < candle_left["low"]:
                return Decimal(str(candle_left["low"]))
    return None


# ──────────────────────────────────────────────
# Strategy A: QUANTUM_V10_PRO (Trend + FVG)
# ──────────────────────────────────────────────

class QuantumTrendStrategy:
    """
    Filtro Macro  1H: EMA 50 → Bias LONG/SHORT.
    Filtro Momentum 15M: EMA 50 + RSI.
    Gatillo Sniper 5M: FVG alineado con el bias.
    """
    NAME = "QUANTUM_V10_PRO"

    def signal(
        self,
        symbol: str,
        df_1h: pd.DataFrame,
        df_15m: pd.DataFrame,
        df_5m: pd.DataFrame,
    ) -> Optional[Signal]:
        if df_1h.empty or df_15m.empty or df_5m.empty:
            return None
        if len(df_1h) < 52 or len(df_15m) < 52 or len(df_5m) < 14:
            return None

        # ── 1H macro bias ──
        ema50_1h = _ema(df_1h["close"], 50).iloc[-1]
        close_1h = df_1h["close"].iloc[-1]
        bias = "long" if close_1h > ema50_1h else "short"

        # ── 15M momentum filter ──
        ema50_15m = _ema(df_15m["close"], 50).iloc[-1]
        close_15m = df_15m["close"].iloc[-1]
        rsi_15m   = _rsi(df_15m["close"]).iloc[-1]

        if bias == "long":
            if not (close_15m > ema50_15m and rsi_15m < 65):
                return None
        else:
            if not (close_15m < ema50_15m and rsi_15m > 35):
                return None

        # ── 5M FVG sniper ──
        atr_5m = _atr(df_5m).iloc[-1]
        if atr_5m <= 0:
            return None

        entry = _find_fvg(df_5m, bias, lookback=5)
        if entry is None:
            return None

        score = abs(rsi_15m - 50) / 50  # farther from 50 = stronger momentum
        return Signal(
            symbol=symbol,
            side=bias,
            strategy=self.NAME,
            entry_price=entry,
            atr_5m=Decimal(str(atr_5m)),
            reason=f"EMA50 bias={bias} | RSI15m={rsi_15m:.1f} | FVG sniper",
            score=score,
        )


# ──────────────────────────────────────────────
# Strategy B: QUANTUM_DIVERGENCE (RSI Divergence + FVG)
# ──────────────────────────────────────────────

class QuantumDivergenceStrategy:
    """
    Radar de Divergencia 15M: analiza 30 velas buscando divergencias RSI/Precio.
    Gatillo Sniper 5M: FVG confirmador.
    """
    NAME = "QUANTUM_DIVERGENCE"
    WINDOW = 30

    def signal(
        self,
        symbol: str,
        df_15m: pd.DataFrame,
        df_5m: pd.DataFrame,
    ) -> Optional[Signal]:
        if df_15m.empty or df_5m.empty:
            return None
        if len(df_15m) < self.WINDOW + 5 or len(df_5m) < 14:
            return None

        df = df_15m.tail(self.WINDOW).reset_index(drop=True)
        df["rsi"] = _rsi(df["close"])

        # ── Bullish Divergence (LONG): price LL, RSI HL, RSI < 40 ──
        price_min_idx = df["low"].idxmin()
        rsi_min_idx   = df["rsi"].idxmin()
        bullish_div = (
            price_min_idx > rsi_min_idx           # price made new low AFTER rsi low
            and df["low"].iloc[-1] < df["low"].iloc[price_min_idx - 1 if price_min_idx > 0 else 0]
            and df["rsi"].iloc[-1] > df["rsi"].iloc[rsi_min_idx]
            and df["rsi"].iloc[-1] < 40
        )

        # ── Bearish Divergence (SHORT): price HH, RSI LH, RSI > 60 ──
        price_max_idx = df["high"].idxmax()
        rsi_max_idx   = df["rsi"].idxmax()
        bearish_div = (
            price_max_idx > rsi_max_idx
            and df["high"].iloc[-1] > df["high"].iloc[price_max_idx - 1 if price_max_idx > 0 else 0]
            and df["rsi"].iloc[-1] < df["rsi"].iloc[rsi_max_idx]
            and df["rsi"].iloc[-1] > 60
        )

        if not bullish_div and not bearish_div:
            return None

        side = "long" if bullish_div else "short"
        rsi_now = df["rsi"].iloc[-1]

        # ── 5M FVG confirmation ──
        atr_5m = _atr(df_5m).iloc[-1]
        if atr_5m <= 0:
            return None

        entry = _find_fvg(df_5m, side, lookback=5)
        if entry is None:
            return None

        score = abs(rsi_now - 50) / 50 + 0.1  # slight bonus for divergence signals
        div_label = "Bullish Divergence" if bullish_div else "Bearish Divergence"
        return Signal(
            symbol=symbol,
            side=side,
            strategy=self.NAME,
            entry_price=entry,
            atr_5m=Decimal(str(atr_5m)),
            reason=f"{div_label} | RSI15m={rsi_now:.1f} | FVG sniper",
            score=score,
        )
