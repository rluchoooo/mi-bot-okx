import re

with open('strategy.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update SMCPDHSweepReversal
content = re.sub(
    r""                    # Filtro de SL estructural\s+if sh is not None:\s+simulated_sl = trigger\['close'\] \+ \(atr \* 2\.0\)\s+if simulated_sl <= sh:\s+continue"",
    r""                    # Filtro de SL estructural\n                    sl_calc = trigger['high'] + (atr * 0.1)\n                    if (sl_calc - trigger['close']) / trigger['close'] > 0.02:\n                        continue # Alto Riesgo"",
    content
)
content = re.sub(
    r""                    return Signal\(\s+symbol=symbol, side=\""short\"", strategy=self\.NAME, order_type=\""market\"",\s+entry_price=Decimal\(str\(trigger\['close'\]\)\), atr_5m=Decimal\(str\(atr\)\),\s+reason=(.*?)\s+\)"",
    r""                    tp_calc = df_15m.iloc[-22:-2]['low'].min()\n                    return Signal(\n                        symbol=symbol, side=\""short\"", strategy=self.NAME, order_type=\""market\"",\n                        entry_price=Decimal(str(trigger['close'])), atr_5m=Decimal(str(atr)),\n                        sl_price=Decimal(str(sl_calc)), tp_price=Decimal(str(tp_calc)),\n                        reason=\1\n                    )"",
    content
)

content = re.sub(
    r""                    # Filtro de SL estructural\s+if sl is not None:\s+simulated_sl = trigger\['close'\] - \(atr \* 2\.0\)\s+if simulated_sl >= sl:\s+continue"",
    r""                    # Filtro de SL estructural\n                    sl_calc = trigger['low'] - (atr * 0.1)\n                    if (trigger['close'] - sl_calc) / trigger['close'] > 0.02:\n                        continue # Alto Riesgo"",
    content
)
content = re.sub(
    r""                    return Signal\(\s+symbol=symbol, side=\""long\"", strategy=self\.NAME, order_type=\""market\"",\s+entry_price=Decimal\(str\(trigger\['close'\]\)\), atr_5m=Decimal\(str\(atr\)\),\s+reason=(.*?)\s+\)"",
    r""                    tp_calc = df_15m.iloc[-22:-2]['high'].max()\n                    return Signal(\n                        symbol=symbol, side=\""long\"", strategy=self.NAME, order_type=\""market\"",\n                        entry_price=Decimal(str(trigger['close'])), atr_5m=Decimal(str(atr)),\n                        sl_price=Decimal(str(sl_calc)), tp_price=Decimal(str(tp_calc)),\n                        reason=\1\n                    )"",
    content
)

with open('strategy.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Patch applied successfully')
