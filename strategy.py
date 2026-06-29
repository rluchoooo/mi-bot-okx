import pandas as pd
import numpy as np
from decimal import Decimal
from typing import Optional, List, Tuple
from dataclasses import dataclass

@dataclass
class Signal:
    symbol: str
    side: str
    strategy: str
    order_type: str
    entry_price: Decimal
    atr_5m: Decimal
    sl_price: Optional[Decimal] = None
    tp_price: Optional[Decimal] = None
    reason: str = ""
    score: float = 1.0
from config import EMA_TREND, ADX_PERIOD, ATR_PERIOD, ADX_MIN

# ── FUNCIONES DE INDICADORES BÁSICOS ───────────────────────────────────

def _adx(df: pd.DataFrame, p: int) -> pd.Series:
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - df['close'].shift()).abs()
    tr3 = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    up = df['high'] - df['high'].shift()
    down = df['low'].shift() - df['low']
    
    pos_dm = np.where((up > down) & (up > 0), up, 0.0)
    neg_dm = np.where((down > up) & (down > 0), down, 0.0)
    
    tr_ema = tr.ewm(span=p, adjust=False).mean()
    pos_ema = pd.Series(pos_dm, index=df.index).ewm(span=p, adjust=False).mean()
    neg_ema = pd.Series(neg_dm, index=df.index).ewm(span=p, adjust=False).mean()
    
    pos_di = 100 * pos_ema / tr_ema
    neg_di = 100 * neg_ema / tr_ema
    
    dx = 100 * (pos_di - neg_di).abs() / (pos_di + neg_di).abs()
    return dx.ewm(span=p, adjust=False).mean()

def _ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=p, adjust=False).mean()

def _atr(df: pd.DataFrame, p: int) -> pd.Series:
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - df['close'].shift()).abs()
    tr3 = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=p, adjust=False).mean()

def _supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    atr = _atr(df, period).values
    high = df['high'].values
    low = df['low'].values
    close = df['close'].values
    
    n = len(df)
    final_ub = np.zeros(n)
    final_lb = np.zeros(n)
    supertrend = np.zeros(n)
    direction = np.ones(n)
    
    hl2 = (high + low) / 2
    basic_ub = hl2 + multiplier * atr
    basic_lb = hl2 - multiplier * atr
    
    for i in range(1, n):
        if basic_ub[i] < final_ub[i-1] or close[i-1] > final_ub[i-1]:
            final_ub[i] = basic_ub[i]
        else:
            final_ub[i] = final_ub[i-1]
            
        if basic_lb[i] > final_lb[i-1] or close[i-1] < final_lb[i-1]:
            final_lb[i] = basic_lb[i]
        else:
            final_lb[i] = final_lb[i-1]
            
        if supertrend[i-1] == final_ub[i-1] and close[i] <= final_ub[i]:
            supertrend[i] = final_ub[i]
            direction[i] = -1
        elif supertrend[i-1] == final_ub[i-1] and close[i] > final_ub[i]:
            supertrend[i] = final_lb[i]
            direction[i] = 1
        elif supertrend[i-1] == final_lb[i-1] and close[i] >= final_lb[i]:
            supertrend[i] = final_lb[i]
            direction[i] = 1
        elif supertrend[i-1] == final_lb[i-1] and close[i] < final_lb[i]:
            supertrend[i] = final_ub[i]
            direction[i] = -1
            
    return pd.DataFrame({
        'supertrend': supertrend,
        'direction': direction
    }, index=df.index)

def _hma(s: pd.Series, p: int) -> pd.Series:
    """Hull Moving Average."""
    half_wma = s.rolling(window=p // 2).mean()
    full_wma = s.rolling(window=p).mean()
    diff = 2 * half_wma - full_wma
    return diff.rolling(window=int(np.sqrt(p))).mean()

def _rsi(s: pd.Series, p: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.where(delta > 0, 0.0).ewm(span=p, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(span=p, adjust=False).mean()
    rs = gain / (loss + 1e-10)
    return 100 - (100 / (1 + rs))

def _macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line

def _bollinger(s: pd.Series, p: int = 20, std_dev: float = 2.0):
    middle = s.rolling(window=p).mean()
    std = s.rolling(window=p).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return middle, upper, lower

def _dmi(df: pd.DataFrame, p: int = 14):
    """Returns +DI, -DI series."""
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - df['close'].shift()).abs()
    tr3 = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    up = df['high'] - df['high'].shift()
    down = df['low'].shift() - df['low']
    pos_dm = np.where((up > down) & (up > 0), up, 0.0)
    neg_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr_ema = tr.ewm(span=p, adjust=False).mean()
    pos_ema = pd.Series(pos_dm, index=df.index).ewm(span=p, adjust=False).mean()
    neg_ema = pd.Series(neg_dm, index=df.index).ewm(span=p, adjust=False).mean()
    plus_di = 100 * pos_ema / (tr_ema + 1e-10)
    minus_di = 100 * neg_ema / (tr_ema + 1e-10)
    return plus_di, minus_di


# ── ESTRATEGIAS ────────────────────────────────────────────────────────

class AntigravityQuantumV13Pro:
    NAME = "ANTIGRAVITY_V13_PRO"

    def signal(self, symbol: str, df_1h: pd.DataFrame, df_15m: pd.DataFrame, df_5m: pd.DataFrame) -> Optional[Signal]:
        """Trend Following on 15m candles with strict multi-filter confirmation."""
        if len(df_15m) < 250:
            return None

        df = df_15m.copy()
        df['ema9'] = _ema(df['close'], 9)
        df['ema20'] = _ema(df['close'], 20)
        df['ema50'] = _ema(df['close'], 50)
        df['ema100'] = _ema(df['close'], 100)
        df['ema200'] = _ema(df['close'], 200)
        df['hma20'] = _hma(df['close'], 20)
        df['hma50'] = _hma(df['close'], 50)
        df['atr'] = _atr(df, 14)
        df['adx'] = _adx(df, 14)
        df['rsi'] = _rsi(df['close'], 14)
        plus_di, minus_di = _dmi(df, 14)
        df['plus_di'] = plus_di
        df['minus_di'] = minus_di
        macd_line, signal_line = _macd(df['close'])
        df['macd'] = macd_line
        df['macd_signal'] = signal_line
        bb_mid, bb_upper, bb_lower = _bollinger(df['close'], 20, 2.0)
        df['bb_mid'] = bb_mid
        df['bb_upper'] = bb_upper
        df['bb_lower'] = bb_lower
        df['vol_sma20'] = df['volume'].rolling(20).mean()
        df['vol_sma50'] = df['volume'].rolling(50).mean()

        trigger = df.iloc[-2]  # Last CLOSED candle
        prev = df.iloc[-3]

        if trigger['atr'] <= 0 or pd.isna(trigger['ema200']):
            return None

        atr_pct = (trigger['atr'] / trigger['close']) * 100
        if atr_pct < 0.25 or atr_pct > 4.5:
            return None

        # --- LONG ---
        long_trend = (
            trigger['close'] > trigger['ema100'] and
            trigger['ema20'] > trigger['ema50'] and
            df['ema20'].iloc[-2] > df['ema20'].iloc[-3] > df['ema20'].iloc[-4] > df['ema20'].iloc[-5] and
            df['ema9'].iloc[-2] > df['ema9'].iloc[-3]
        )
        long_volume = (
            trigger['volume'] > (trigger['vol_sma20'] * 1.05) and
            trigger['volume'] > (trigger['vol_sma50'] * 0.85)
        )
        long_adx = trigger['adx'] > 14 and trigger['plus_di'] > trigger['minus_di']
        long_rsi = 30 < trigger['rsi'] < 80
        long_macd = trigger['macd'] > trigger['macd_signal']
        long_cross = prev['hma20'] <= prev['hma50'] and trigger['hma20'] > trigger['hma50']

        if all([long_trend, long_volume, long_adx, long_rsi, long_macd, long_cross]):
            return Signal(
                symbol=symbol, side="long", strategy=self.NAME, order_type="limit",
                entry_price=Decimal(str(trigger['close'])),
                atr_5m=Decimal(str(trigger['atr'])),
                reason=f"AG Long | ADX={trigger['adx']:.1f} RSI={trigger['rsi']:.0f} HMA Cross",
                score=1.0
            )

        # --- SHORT ---
        short_trend = (
            trigger['close'] < trigger['ema100'] and
            trigger['ema20'] < trigger['ema50'] and
            df['ema20'].iloc[-2] < df['ema20'].iloc[-3] < df['ema20'].iloc[-4] < df['ema20'].iloc[-5] and
            df['ema9'].iloc[-2] < df['ema9'].iloc[-3]
        )
        short_volume = (
            trigger['volume'] > (trigger['vol_sma20'] * 1.05) and
            trigger['volume'] > (trigger['vol_sma50'] * 0.85)
        )
        short_adx = trigger['adx'] > 14 and trigger['minus_di'] > trigger['plus_di']
        short_rsi = 20 < trigger['rsi'] < 70
        short_macd = trigger['macd'] < trigger['macd_signal']
        short_cross = prev['hma20'] >= prev['hma50'] and trigger['hma20'] < trigger['hma50']

        if all([short_trend, short_volume, short_adx, short_rsi, short_macd, short_cross]):
            return Signal(
                symbol=symbol, side="short", strategy=self.NAME, order_type="limit",
                entry_price=Decimal(str(trigger['close'])),
                atr_5m=Decimal(str(trigger['atr'])),
                reason=f"AG Short | ADX={trigger['adx']:.1f} RSI={trigger['rsi']:.0f} HMA Cross",
                score=1.0
            )

        return None


class SuperTrendEMARegimeMTFPro:
    NAME = "ST_EMA_REGIME_MTF_PRO"

    def signal(self, symbol: str, df_1h: pd.DataFrame, df_15m: pd.DataFrame, df_5m: pd.DataFrame) -> Optional[Signal]:
        if len(df_15m) < 200 or len(df_1h) < 200: 
            return None
            
        # 1. Calcular indicadores en 15m
        df_15m = df_15m.copy()
        df_15m['ema200'] = _ema(df_15m['close'], 200)
        df_15m['ema21'] = _ema(df_15m['close'], 21)
        df_15m['ema9'] = _ema(df_15m['close'], 9)
        df_15m['adx'] = _adx(df_15m, 14)
        df_15m['atr'] = _atr(df_15m, 10)
        
        st_df = _supertrend(df_15m, 10, 3.0)
        df_15m['st'] = st_df['supertrend']
        df_15m['st_dir'] = st_df['direction']
        
        # 2. Calcular indicadores en 1H
        df_1h = df_1h.copy()
        df_1h['ema200'] = _ema(df_1h['close'], 200)
        df_1h['ema21'] = _ema(df_1h['close'], 21)
        df_1h['ema9'] = _ema(df_1h['close'], 9)
        
        trigger = df_15m.iloc[-2]
        trigger_1h = df_1h.iloc[-2]
        
        # 3. Detectar armado de setup (Regímenes) en ventana de 25 velas
        window_start = max(0, len(df_15m) - 25 - 2)
        window = df_15m.iloc[window_start:-2]
        
        long_armed = False
        short_armed = False
        
        # Un régimen bajista "arma" un LONG si vemos ST rojo, precio debajo de EMA200 y EMA9 debajo EMA21
        bearish_regime_mask = (window['st_dir'] == -1) & (window['close'] < window['ema200']) & (window['ema9'] < window['ema21'])
        if bearish_regime_mask.any():
            long_armed = True
            
        # Un régimen alcista "arma" un SHORT si vemos ST verde, precio sobre EMA200 y EMA9 sobre EMA21
        bullish_regime_mask = (window['st_dir'] == 1) & (window['close'] > window['ema200']) & (window['ema9'] > window['ema21'])
        if bullish_regime_mask.any():
            short_armed = True
            
        # 4. Gatillos de entrada
        # LONG
        if long_armed:
            cond_st = trigger['st_dir'] == 1
            cond_px = trigger['close'] > trigger['ema200']
            cond_st_ema = trigger['st'] > trigger['ema200']
            cond_ema_stack = (trigger['ema9'] > trigger['ema200']) and (trigger['ema21'] > trigger['ema200']) and (trigger['ema9'] > trigger['ema21'])
            cond_adx = trigger['adx'] >= 18
            cond_slope = trigger['ema200'] > df_15m.iloc[-12]['ema200'] # diff con 10 velas atras
            cond_dist = (trigger['close'] - trigger['ema200']) >= (0.3 * trigger['atr'])
            
            # Filtro 1H
            cond_1h = (trigger_1h['close'] > trigger_1h['ema200']) and (trigger_1h['ema9'] > trigger_1h['ema21'])
            
            if all([cond_st, cond_px, cond_st_ema, cond_ema_stack, cond_adx, cond_slope, cond_dist, cond_1h]):
                return Signal(
                    symbol=symbol, side="long", strategy=self.NAME, order_type="limit",
                    entry_price=Decimal(str(trigger['ema21'])), atr_5m=Decimal(str(trigger['atr'])),
                    reason=f"ST+EMA Long | ADX={trigger['adx']:.1f}", score=1.0
                )
                
        # SHORT
        if short_armed:
            cond_st = trigger['st_dir'] == -1
            cond_px = trigger['close'] < trigger['ema200']
            cond_st_ema = trigger['st'] < trigger['ema200']
            cond_ema_stack = (trigger['ema9'] < trigger['ema200']) and (trigger['ema21'] < trigger['ema200']) and (trigger['ema9'] < trigger['ema21'])
            cond_adx = trigger['adx'] >= 18
            cond_slope = trigger['ema200'] < df_15m.iloc[-12]['ema200'] # diff con 10 velas atras
            cond_dist = (trigger['ema200'] - trigger['close']) >= (0.3 * trigger['atr'])
            
            # Filtro 1H
            cond_1h = (trigger_1h['close'] < trigger_1h['ema200']) and (trigger_1h['ema9'] < trigger_1h['ema21'])

            if all([cond_st, cond_px, cond_st_ema, cond_ema_stack, cond_adx, cond_slope, cond_dist, cond_1h]):
                return Signal(
                    symbol=symbol, side="short", strategy=self.NAME, order_type="limit",
                    entry_price=Decimal(str(trigger['ema21'])), atr_5m=Decimal(str(trigger['atr'])),
                    reason=f"ST+EMA Short | ADX={trigger['adx']:.1f}", score=1.0
                )
                
        return None

    def exit_signal(self, side: str, df_15m: pd.DataFrame) -> bool:
        if len(df_15m) < 200: return False
        
        df_15m = df_15m.copy()
        df_15m['ema200'] = _ema(df_15m['close'], 200)
        df_15m['ema21'] = _ema(df_15m['close'], 21)
        df_15m['ema9'] = _ema(df_15m['close'], 9)
        
        st_df = _supertrend(df_15m, 10, 3.0)
        df_15m['st'] = st_df['supertrend']
        df_15m['st_dir'] = st_df['direction']
        
        trigger = df_15m.iloc[-2]
        
        window_start = max(0, len(df_15m) - 25 - 2)
        window = df_15m.iloc[window_start:-2]
        
        if side == "long":
            bullish_regime_mask = (window['st_dir'] == 1) & (window['close'] > window['ema200']) & (window['ema9'] > window['ema21'])
            if bullish_regime_mask.any():
                cond_st = trigger['st_dir'] == -1
                cond_px = trigger['close'] < trigger['ema200']
                cond_st_ema = trigger['st'] < trigger['ema200']
                cond_ema_stack = (trigger['ema9'] < trigger['ema200']) and (trigger['ema21'] < trigger['ema200']) and (trigger['ema9'] < trigger['ema21'])
                if cond_st and cond_px and cond_st_ema and cond_ema_stack:
                    return True
        elif side == "short":
            bearish_regime_mask = (window['st_dir'] == -1) & (window['close'] < window['ema200']) & (window['ema9'] < window['ema21'])
            if bearish_regime_mask.any():
                cond_st = trigger['st_dir'] == 1
                cond_px = trigger['close'] > trigger['ema200']
                cond_st_ema = trigger['st'] > trigger['ema200']
                cond_ema_stack = (trigger['ema9'] > trigger['ema200']) and (trigger['ema21'] > trigger['ema200']) and (trigger['ema9'] > trigger['ema21'])
                if cond_st and cond_px and cond_st_ema and cond_ema_stack:
                    return True
        
        return False
