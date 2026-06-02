---
title: OKX Demo Quant Bot
emoji: 📈
colorFrom: blue
colorTo: gray
sdk: gradio
sdk_version: 5.36.2
app_file: app.py
pinned: true
license: mit
---

# OKX Demo Quant Bot

Bot educativo para OKX demo trading en futuros perpetuos USDT-SWAP.

## Advertencia profesional

Este sistema no garantiza rentabilidad. Una estrategia técnica puede fallar por rupturas falsas, slippage, spreads, latencia, cambios de régimen, baja liquidez, funding adverso y colas de distribución. El bot incluye filtros de liquidez, tendencia, volatilidad, volumen, funding, open interest, límite de exposición y modo demo por defecto, pero aun así requiere backtesting, forward testing y auditoría antes de cualquier uso real.

## Estrategia actual: Adaptive Donchian Momentum

- Universo dinámico: top 70 futuros perpetuos USDT-SWAP por volumen.
- Filtro de exclusión: metales, tokens de acciones y productos no cripto puros.
- Entrada: ruptura Donchian de 20 velas o continuación tras pullback a EMA20.
- Confirmaciones: ADX/DI, RSI de momentum no exhausto, VWAP, volumen relativo y tendencia 15m/EMA100.
- Filtros de microestructura: spread máximo y volatilidad ATR mínima/máxima.
- Filtros de derivados: evita funding extremo o demasiado unilateral.
- Gestión: stop 2 ATR, objetivo inicial 2R, break-even parcial y trailing dinámico.

## Secretos requeridos

Configura estos secretos en Hugging Face Space Settings:

- `OKX_API_KEY`
- `OKX_API_SECRET`
- `OKX_API_PASSPHRASE`
- `OKX_SIMULATED=1`

Opcionales:

- `BOT_AUTOSTART=true`
- `ORDER_MARGIN_USDT=15`
- `LEVERAGE=10`
- `MAX_CONCURRENT_POSITIONS=10`
- `TIMEFRAME=5m`
- `CONFIRM_TIMEFRAME=15m`
