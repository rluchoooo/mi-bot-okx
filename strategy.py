"""
strategy.py – Motor de Señales
A: Quantum SMC V10 PRO (Limit Orders)
B: Supertrend Pullback V3 (Market Orders)
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd

from config import (
    ADX_MIN, ADX_PERIOD, ATR_PERIOD,
    EMA_FAST, EMA_MID, EMA_TREND,
    RSI_PERIOD,
    SUPERTREND_FACTOR, SUPERTREND_PERIOD
)


# ──────────────────────────────────────────────
# Data class
# ──────────────────────────────────────────────

@dataclass
class Signal:
    symbol:      str
    side:        str          # "long" | "short"
    strategy:    str          # "QUANTUM_SMC_V10_PRO" | "SUPERTREND_PULLBACK_V3"
    order_type:  str          # "limit" | "market"
    entry_price: Decimal      # price to enter at
    atr_5m:      Decimal
    reason:      str
    sl_price:    Optional[Decimal] = None # Exact SL for strategy B
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


def _supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    atr = _atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2
    basic_ub = hl2 + (multiplier * atr)
    basic_lb = hl2 - (multiplier * atr)

    supertrend = np.zeros(len(df))
    direction = np.ones(len(df))
    final_ub = np.zeros(len(df))
    final_lb = np.zeros(len(df))
    
    close = df["close"].values
    
    final_ub[0] = basic_ub.iloc[0]
    final_lb[0] = basic_lb.iloc[0]
    
    for i in range(1, len(df)):
        if basic_ub.iloc[i] < final_ub[i-1] or close[i-1] > final_ub[i-1]:
            final_ub[i] = basic_ub.iloc[i]
        else:
            final_ub[i] = final_ub[i-1]
            
        if basic_lb.iloc[i] > final_lb[i-1] or close[i-1] < final_lb[i-1]:
            final_lb[i] = basic_lb.iloc[i]
        else:
            final_lb[i] = final_lb[i-1]
            
        if supertrend[i-1] == final_ub[i-1] and close[i] <= final_ub[i]:
            direction[i] = -1
        elif supertrend[i-1] == final_ub[i-1] and close[i] > final_ub[i]:
            direction[i] = 1
        elif supertrend[i-1] == final_lb[i-1] and close[i] >= final_lb[i]:
            direction[i] = 1
        elif supertrend[i-1] == final_lb[i-1] and close[i] < final_lb[i]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]
            
        if direction[i] == 1:
            supertrend[i] = final_lb[i]
        else:
            supertrend[i] = final_ub[i]
            
    return pd.DataFrame({
        "supertrend": supertrend,
        "direction": direction
    }, index=df.index)


# ──────────────────────────────────────────────
# Strategy A: QUANTUM_SMC_V10_PRO (FVG + Sweep)
# ──────────────────────────────────────────────

class QuantumSMCStrategy:
    NAME = "QUANTUM_SMC_V10_PRO"

    def signal(
        self,
        symbol:  str,
        df_1h:   pd.DataFrame,
        df_15m:  pd.DataFrame,
        df_5m:   pd.DataFrame,
    ) -> Optional[Signal]:
        if len(df_5m) < 35:
            return None

        # Calculate volume SMA 15
        vol_sma = df_5m["volume"].rolling(15).mean()
        atr = _atr(df_5m, ATR_PERIOD)
        
        # Check the last pattern (v1, v2, v3) -> indexes -3, -2, -1
        # V1: -3, V2: -2, V3: -1
        v1 = df_5m.iloc[-3]
        v2 = df_5m.iloc[-2]
        v3 = df_5m.iloc[-1]
        
        vol_3_sma = vol_sma.iloc[-1]
        if v3["volume"] <= 1.25 * vol_3_sma:
            return None
            
        atr_val = Decimal(str(atr.iloc[-1]))
            
        # Swing Low of last 15 candles before the pattern (indexes -18 to -4)
        history_window = df_5m.iloc[-18:-3]
        swing_low = history_window["low"].min()
        swing_high = history_window["high"].max()
        
        # LONG check
        # V3 must be green (Close > Open)
        if v3["close"] > v3["open"]:
            # FVG Check: V2 Low > V1 High
            if v2["low"] > v1["high"]:
                # Sweep check: V1 or V3 low broke the swing_low
                if v1["low"] < swing_low or v3["low"] < swing_low:
                    entry = Decimal(str((v2["low"] + v1["high"]) / 2))
                    return Signal(
                        symbol=symbol, side="long", strategy=self.NAME, order_type="limit",
                        entry_price=entry, atr_5m=atr_val,
                        reason=f"SMC FVG Long | Vol={v3['volume']:.0f} > SMA({vol_3_sma:.0f})",
                        score=1.0
                    )
                    
        # SHORT check
        # V3 must be red (Close < Open)
        if v3["close"] < v3["open"]:
            # FVG Check: V2 High < V1 Low
            if v2["high"] < v1["low"]:
                # Sweep check: V1 or V3 high broke the swing_high
                if v1["high"] > swing_high or v3["high"] > swing_high:
                    entry = Decimal(str((v2["high"] + v1["low"]) / 2))
                    return Signal(
                        symbol=symbol, side="short", strategy=self.NAME, order_type="limit",
                        entry_price=entry, atr_5m=atr_val,
                        reason=f"SMC FVG Short | Vol={v3['volume']:.0f} > SMA({vol_3_sma:.0f})",
                        score=1.0
                    )
        
        return None


# ──────────────────────────────────────────────
# Strategy B: SUPERTREND_PULLBACK_V3
# ──────────────────────────────────────────────

class SupertrendPullbackStrategy:
    NAME = "SUPERTREND_PULLBACK_V3"

    def signal(
        self,
        symbol:  str,
        df_1h:   pd.DataFrame,
        df_15m:  pd.DataFrame,
        df_5m:   pd.DataFrame,
    ) -> Optional[Signal]:
        if len(df_5m) < 55:
            return None

        ema9 = _ema(df_5m["close"], EMA_FAST)
        ema21 = _ema(df_5m["close"], EMA_MID)
        ema50 = _ema(df_5m["close"], EMA_TREND)
        
        adx = _adx(df_5m, ADX_PERIOD)
        rsi = _rsi(df_5m, RSI_PERIOD)
        st_df = _supertrend(df_5m, SUPERTREND_PERIOD, SUPERTREND_FACTOR)
        
        current = df_5m.iloc[-1]
        c_ema9 = ema9.iloc[-1]
        c_ema21 = ema21.iloc[-1]
        c_ema50 = ema50.iloc[-1]
        c_adx = adx.iloc[-1]
        c_rsi = rsi.iloc[-1]
        c_st = st_df["supertrend"].iloc[-1]
        c_st_dir = st_df["direction"].iloc[-1]
        
        atr_val = Decimal(str(_atr(df_5m, ATR_PERIOD).iloc[-1]))
        
        if c_adx <= 20:
            return None
            
        # LONG Logic
        if c_ema9 > c_ema21 and current["close"] > c_ema50 and c_st_dir == 1:
            if 50 < c_rsi < 70:
                if current["low"] <= c_ema21 and current["close"] > c_ema21:
                    entry = Decimal(str(current["close"]))
                    sl_val = Decimal(str(c_st)) * Decimal("0.999")
                    return Signal(
                        symbol=symbol, side="long", strategy=self.NAME, order_type="market",
                        entry_price=entry, sl_price=sl_val, atr_5m=atr_val,
                        reason=f"ST Pullback Long | ADX={c_adx:.1f} | RSI={c_rsi:.1f}",
                        score=1.0
                    )
                    
        # SHORT Logic
        if c_ema9 < c_ema21 and current["close"] < c_ema50 and c_st_dir == -1:
            if 30 < c_rsi < 50:
                if current["high"] >= c_ema21 and current["close"] < c_ema21:
                    entry = Decimal(str(current["close"]))
                    sl_val = Decimal(str(c_st)) * Decimal("1.001")
                    return Signal(
                        symbol=symbol, side="short", strategy=self.NAME, order_type="market",
                        entry_price=entry, sl_price=sl_val, atr_5m=atr_val,
                        reason=f"ST Pullback Short | ADX={c_adx:.1f} | RSI={c_rsi:.1f}",
                        score=1.0
                    )
                    
        return None
