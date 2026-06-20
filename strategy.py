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


# ── TRUE SMC ANALYZER (LÓGICA INSTITUCIONAL) ───────────────────────────

class TrueSMCAnalyzer:
    @staticmethod
    def calc_pdh_pdl(df_1h: pd.DataFrame):
        if not pd.api.types.is_datetime64_any_dtype(df_1h.index):
            return None, None
        daily = df_1h.resample('D').agg({'high': 'max', 'low': 'min'})
        if len(daily) < 2: return None, None
        pdh = daily.iloc[-2]['high']
        pdl = daily.iloc[-2]['low']
        return pdh, pdl

    @staticmethod
    def get_swing_pivots(df: pd.DataFrame, window: int = 3, lookback: int = 50):
        highs = df['high'].values
        lows = df['low'].values
        
        last_sh = None
        last_sl = None
        
        start_idx = max(window, len(df) - lookback)
        end_idx = len(df) - window
        
        for i in range(start_idx, end_idx):
            is_sh = True
            is_sl = True
            for j in range(1, window + 1):
                if highs[i] <= highs[i-j] or highs[i] <= highs[i+j]: is_sh = False
                if lows[i] >= lows[i-j] or lows[i] >= lows[i+j]: is_sl = False
            
            if is_sh: last_sh = highs[i]
            if is_sl: last_sl = lows[i]
            
        return last_sh, last_sl

    @staticmethod
    def find_unmitigated_fvg(df: pd.DataFrame, lookback: int = 30):
        if len(df) < lookback + 3: return None, None
        
        start_idx = len(df) - lookback
        end_idx = len(df) - 2 
        
        bullish_fvg = None
        bearish_fvg = None
        
        for i in range(end_idx, start_idx, -1):
            v1 = df.iloc[i-2]
            v2 = df.iloc[i-1]
            v3 = df.iloc[i]
            
            if v1['high'] < v3['low'] and v2['close'] > v2['open']:
                mitigated = False
                for j in range(i+1, len(df)-1):
                    if df.iloc[j]['low'] <= v1['high']:
                        mitigated = True
                        break
                if not mitigated:
                    bullish_fvg = (v1['high'], v3['low']) 
                    break 
                    
            if v1['low'] > v3['high'] and v2['close'] < v2['open']:
                mitigated = False
                for j in range(i+1, len(df)-1):
                    if df.iloc[j]['high'] >= v1['low']:
                        mitigated = True
                        break
                if not mitigated:
                    bearish_fvg = (v3['high'], v1['low'])
                    break
                    
        return bullish_fvg, bearish_fvg

    @staticmethod
    def find_orderblock(df: pd.DataFrame, lookback: int = 40):
        if len(df) < lookback: return None, None
        
        bullish_ob = None
        bearish_ob = None
        
        vols = df['volume'].rolling(10).mean()
        
        start_idx = len(df) - lookback
        end_idx = len(df) - 3 
        
        for i in range(end_idx, start_idx, -1):
            expansion_candle = df.iloc[i]
            if expansion_candle['volume'] > vols.iloc[i] * 1.2:
                body = abs(expansion_candle['close'] - expansion_candle['open'])
                atr_val = _atr(df.iloc[:i+1], 14).iloc[-1]
                
                if expansion_candle['close'] > expansion_candle['open'] and body > atr_val * 1.0:
                    ob_candle = df.iloc[i-1]
                    if ob_candle['close'] < ob_candle['open']:
                        ob_high = ob_candle['high']
                        mitigated = False
                        for j in range(i+1, len(df)-1):
                            if df.iloc[j]['low'] <= ob_high:
                                mitigated = True
                                break
                        if not mitigated:
                            bullish_ob = ob_candle
                            break
                            
                if expansion_candle['close'] < expansion_candle['open'] and body > atr_val * 1.0:
                    ob_candle = df.iloc[i-1]
                    if ob_candle['close'] > ob_candle['open']: 
                        ob_low = ob_candle['low']
                        mitigated = False
                        for j in range(i+1, len(df)-1):
                            if df.iloc[j]['high'] >= ob_low:
                                mitigated = True
                                break
                        if not mitigated:
                            bearish_ob = ob_candle
                            break
                            
        return bullish_ob, bearish_ob

# ── ESTRATEGIAS INDIVIDUALES ───────────────────────────────────────────

class SMCPDHSweepReversal:
    NAME = "SMC_LIQ_SWEEP"

    def signal(self, symbol: str, df_1h: pd.DataFrame, df_15m: pd.DataFrame, df_5m: pd.DataFrame) -> Optional[Signal]:
        if len(df_15m) < 55: return None
        
        df_1h_time = df_1h.copy()
        try:
            df_1h_time.index = pd.to_datetime(df_1h_time['timestamp'], unit='ms')
            pdh, pdl = TrueSMCAnalyzer.calc_pdh_pdl(df_1h_time)
        except Exception:
            pdh, pdl = None, None

        sh, sl = TrueSMCAnalyzer.get_swing_pivots(df_15m, window=5, lookback=40)
        
        trigger = df_15m.iloc[-2]
        atr = _atr(df_15m, ATR_PERIOD).iloc[-2]
        
        upper_targets = [t for t in [pdh, sh] if t is not None]
        lower_targets = [t for t in [pdl, sl] if t is not None]
        
        for target in upper_targets:
            if trigger['high'] > target and trigger['close'] < target:
                body = abs(trigger['close'] - trigger['open'])
                wick = trigger['high'] - max(trigger['close'], trigger['open'])
                if wick > body * 0.8 and trigger['close'] < trigger['open']:
                    return Signal(
                        symbol=symbol, side="short", strategy=self.NAME, order_type="market",
                        entry_price=Decimal(str(trigger['close'])), atr_5m=Decimal(str(atr)),
                        reason=f"Liquidity Sweep Top | Tgt={target:.4f}", score=1.0
                    )

        for target in lower_targets:
            if trigger['low'] < target and trigger['close'] > target:
                body = abs(trigger['close'] - trigger['open'])
                wick = min(trigger['close'], trigger['open']) - trigger['low']
                if wick > body * 0.8 and trigger['close'] > trigger['open']:
                    return Signal(
                        symbol=symbol, side="long", strategy=self.NAME, order_type="market",
                        entry_price=Decimal(str(trigger['close'])), atr_5m=Decimal(str(atr)),
                        reason=f"Liquidity Sweep Bottom | Tgt={target:.4f}", score=1.0
                    )
        return None

class SMCFVGMitigation:
    NAME = "SMC_FVG_MITIG"

    def signal(self, symbol: str, df_1h: pd.DataFrame, df_15m: pd.DataFrame, df_5m: pd.DataFrame) -> Optional[Signal]:
        if len(df_15m) < 50: return None
        
        bull_fvg, bear_fvg = TrueSMCAnalyzer.find_unmitigated_fvg(df_15m, lookback=30)
        trigger = df_15m.iloc[-2]
        atr = _atr(df_15m, ATR_PERIOD).iloc[-2]
        ema100 = _ema(df_15m['close'], EMA_TREND).iloc[-2]
        
        if bull_fvg and trigger['close'] > ema100:
            fvg_bottom, fvg_top = bull_fvg
            if trigger['low'] < fvg_top and trigger['close'] > trigger['open']:
                return Signal(
                    symbol=symbol, side="long", strategy=self.NAME, order_type="market",
                    entry_price=Decimal(str(trigger['close'])), atr_5m=Decimal(str(atr)),
                    reason=f"FVG Mitigated Long | Zone {fvg_bottom:.4f}-{fvg_top:.4f}", score=1.0
                )
                
        if bear_fvg and trigger['close'] < ema100:
            fvg_bottom, fvg_top = bear_fvg
            if trigger['high'] > fvg_bottom and trigger['close'] < trigger['open']:
                return Signal(
                    symbol=symbol, side="short", strategy=self.NAME, order_type="market",
                    entry_price=Decimal(str(trigger['close'])), atr_5m=Decimal(str(atr)),
                    reason=f"FVG Mitigated Short | Zone {fvg_bottom:.4f}-{fvg_top:.4f}", score=1.0
                )
        return None

class SMCOrderblockBounce:
    NAME = "SMC_OB_RETEST"

    def signal(self, symbol: str, df_1h: pd.DataFrame, df_15m: pd.DataFrame, df_5m: pd.DataFrame) -> Optional[Signal]:
        if len(df_15m) < 50: return None
        
        bull_ob, bear_ob = TrueSMCAnalyzer.find_orderblock(df_15m, lookback=40)
        trigger = df_15m.iloc[-2]
        atr = _atr(df_15m, ATR_PERIOD).iloc[-2]
        ema100 = _ema(df_15m['close'], EMA_TREND).iloc[-2]
        
        if bull_ob is not None and trigger['close'] > ema100:
            ob_high = bull_ob['high']
            if trigger['low'] <= ob_high and trigger['close'] > trigger['open']:
                return Signal(
                    symbol=symbol, side="long", strategy=self.NAME, order_type="market",
                    entry_price=Decimal(str(trigger['close'])), atr_5m=Decimal(str(atr)),
                    reason=f"OB Retest Long | OB High {ob_high:.4f}", score=1.0
                )
                
        if bear_ob is not None and trigger['close'] < ema100:
            ob_low = bear_ob['low']
            if trigger['high'] >= ob_low and trigger['close'] < trigger['open']:
                return Signal(
                    symbol=symbol, side="short", strategy=self.NAME, order_type="market",
                    entry_price=Decimal(str(trigger['close'])), atr_5m=Decimal(str(atr)),
                    reason=f"OB Retest Short | OB Low {ob_low:.4f}", score=1.0
                )
        return None

class SMCAMDBreakout:
    NAME = "SMC_AMD_PO3"

    def signal(self, symbol: str, df_1h: pd.DataFrame, df_15m: pd.DataFrame, df_5m: pd.DataFrame) -> Optional[Signal]:
        if len(df_15m) < 30: return None
        
        atr = _atr(df_15m, ATR_PERIOD).iloc[-2]
        recent_window = df_15m.iloc[-15:-2] 
        trigger = df_15m.iloc[-2]
        
        range_high = recent_window['high'].max()
        range_low = recent_window['low'].min()
        rango_size = range_high - range_low
        
        if rango_size < atr * 2:
            if trigger['high'] > range_high and trigger['close'] < range_high:
                if trigger['close'] < trigger['open']:
                    return Signal(
                        symbol=symbol, side="short", strategy=self.NAME, order_type="market",
                        entry_price=Decimal(str(trigger['close'])), atr_5m=Decimal(str(atr)),
                        reason=f"AMD Sweep High -> Distribute Short", score=1.0
                    )
            if trigger['low'] < range_low and trigger['close'] > range_low:
                if trigger['close'] > trigger['open']:
                    return Signal(
                        symbol=symbol, side="long", strategy=self.NAME, order_type="market",
                        entry_price=Decimal(str(trigger['close'])), atr_5m=Decimal(str(atr)),
                        reason=f"AMD Sweep Low -> Distribute Long", score=1.0
                    )
        return None

class SuperTrendEMARegimeMTFPro:
    NAME = "ST_EMA_REGIME_MTF"

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
        
        # 3. Detectar armado de setup (Regímenes) en ventana de 160 velas
        window_start = max(0, len(df_15m) - 160 - 2)
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
                    entry_price=Decimal(str(trigger['close'])), atr_5m=Decimal(str(trigger['atr'])),
                    reason=f"Regime Shift Long | ADX={trigger['adx']:.1f}", score=1.0
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
                    entry_price=Decimal(str(trigger['close'])), atr_5m=Decimal(str(trigger['atr'])),
                    reason=f"Regime Shift Short | ADX={trigger['adx']:.1f}", score=1.0
                )
                
        return None
