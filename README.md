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

Este sistema no garantiza rentabilidad. Una estrategia de Bollinger Bands + ATR puede fallar por rupturas falsas, slippage, spreads, latencia, cambios de régimen, baja liquidez y colas de distribución. El bot incluye filtros de liquidez, tendencia, volatilidad, volumen, límite de exposición y modo demo por defecto, pero aun así requiere backtesting, forward testing y auditoría antes de cualquier uso real.

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

