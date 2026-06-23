"""
app.py – FastAPI Backend para el Quantum V10 Pro Bot.
"""
from __future__ import annotations

import os
from decimal import Decimal
import logging
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import uvicorn

from dotenv import load_dotenv
load_dotenv()

from models import TradeStatus, TradeSide, create_all
from risk import pnl_usd
from scanner import QuantumBotRuntime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Init
# ──────────────────────────────────────────────────────────────────────

create_all()

runtime = QuantumBotRuntime(
    api_key    = os.getenv("OKX_API_KEY", ""),
    api_secret = os.getenv("OKX_API_SECRET", ""),
    passphrase = os.getenv("OKX_API_PASSPHRASE", ""),
    simulated  = os.getenv("OKX_SIMULATED", "1") == "1",
)

if os.getenv("BOT_AUTOSTART", "true").lower() == "true":
    runtime.start()

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

STRATEGY_SHORT = {
    "QUANTUM_SMC_V10_PRO":    "QUANTUM SMC V10 (FVG)",
    "SUPERTREND_PULLBACK_V3": "SUPERTREND PULLBACK V3",
    "AUTO_ADOPTED":           "AUTO ADOPTED",
    "SMC_LIQ_SWEEP":          "SMC LIQ SWEEP",
    "SMC_FVG_MITIG":          "SMC FVG MITIG",
    "SMC_OB_RETEST":          "SMC OB RETEST",
    "SMC_AMD_PO3":            "SMC AMD PO3",
    "ST_EMA_REGIME_MTF":      "SuperTrend EMA Regime MTF Pro",
}

# ──────────────────────────────────────────────────────────────────────
# FastAPI App
# ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="OKX Quantum Elite API")

@app.get("/api/dashboard")
async def get_dashboard_data():
    open_trades   = runtime.get_open_trades()
    closed_trades = runtime.get_closed_trades(n=8)
    stats         = runtime.get_stats()
    logs          = runtime.get_logs(n=20)

    # Stats
    total_pnl     = stats.get("total_pnl", 0)
    win_rate      = stats.get("win_rate", 0)
    pf            = stats.get("profit_factor", 0)
    pnl_today     = stats.get("pnl_today", 0)

    # OKX Live Positions (Fix for Live PNL)
    live_upl_total = 0.0
    okx_positions = {}
    
    # We fetch live positions if the bot is running and has a client loaded.
    try:
        from scanner import OKXClient
        temp_client = OKXClient(
            api_key=runtime.api_key,
            api_secret=runtime.api_secret,
            passphrase=runtime.passphrase,
            simulated=runtime.simulated
        )
        live_positions = await temp_client.get_positions()
        for p in live_positions:
            okx_positions[p.get("instId", "")] = p
        await temp_client.close()
    except Exception as e:
        logger.warning(f"Failed to fetch live positions for PNL sync: {e}")

    pos_data = []
    
    for t in open_trades:
        raw_strat = t.strategy.value if hasattr(t.strategy, "value") else str(t.strategy)
        raw_strat = raw_strat.replace("Strategy.", "")
        strat_lbl = STRATEGY_SHORT.get(raw_strat, raw_strat)
        
        side_lbl  = (t.side.value if hasattr(t.side, "value") else str(t.side)).upper()
        if "." in side_lbl: side_lbl = side_lbl.split(".")[-1]
        sym       = t.symbol.replace("-USDT-SWAP", "USDT")
        
        status_val = (t.status.value if hasattr(t.status, "value") else str(t.status)).upper()
        if "." in status_val: status_val = status_val.split(".")[-1]

        # LIVE PNL SYNC FIX
        live_upl = 0.0
        # t.symbol is typically "FIL-USDT-SWAP"
        if t.symbol in okx_positions:
            upl_raw = okx_positions[t.symbol].get("upl", "0")
            try:
                live_upl = float(upl_raw)
            except:
                pass
        
        live_upl_total += live_upl
        upl_val = f"+{live_upl:.4f}" if live_upl >= 0 else f"{live_upl:.4f}"
        
        pos_data.append([
            sym,
            strat_lbl,
            side_lbl,
            f"{t.entry_price:.6f}" if t.entry_price else "0.00",
            f"SL: {t.sl_price or 'N/A'} | TP: {getattr(t, 'tp1_price', None) or 'Trailing Only'}",
            upl_val,
            status_val
        ])
    
    if not pos_data:
        pos_data = [["-", "-", "-", "-", "-", "-", "-"]]

    # Closed Trades
    closed_data = []
    for t in closed_trades:
        pnl   = t.realized_pnl or 0
        sym   = t.symbol.replace("-USDT-SWAP", "USDT")
        side_lbl = (t.side.value if hasattr(t.side, "value") else str(t.side)).upper()
        if "." in side_lbl: side_lbl = side_lbl.split(".")[-1]
        
        reason = (t.close_reason or "").upper()
        
        raw_strat = t.strategy.value if hasattr(t.strategy, "value") else str(t.strategy)
        raw_strat = raw_strat.replace("Strategy.", "")
        strat  = STRATEGY_SHORT.get(raw_strat, raw_strat)
        
        upl_val = f"+{pnl:.4f}" if pnl >= 0 else f"{pnl:.4f}"
        
        closed_data.append([
            sym,
            side_lbl,
            strat,
            f"{t.entry_price:.6f}" if t.entry_price else "0.00",
            f"{t.close_price:.6f}" if t.close_price else "0.00",
            reason,
            upl_val
        ])
    
    if not closed_data:
        closed_data = [["-", "-", "-", "-", "-", "-", "-"]]

    logs_text = "\\n".join(f"[QUANTUM] {l}" for l in reversed(logs)) if logs else "[SYSTEM] Terminal ready. Awaiting signals..."
    if runtime.running:
        status_str = "🟢 BOT RUNNING" if getattr(runtime, 'opening_allowed', False) else "🟡 SAFE STOP (Protecting)"
    else:
        status_str = "🔴 OFF"
    
    # We fetch balance from runtime if it exists, otherwise 0
    bal = getattr(runtime, "current_exchange_balance", 0.0)

    return JSONResponse(content={
        "status": status_str,
        "balance": f"{bal:,.2f} USDT",
        "live_pnl": f"{live_upl_total:+.2f} USDT",
        "daily_pnl": f"{pnl_today:+.2f} USDT",
        "win_rate": f"{win_rate:.1f}%",
        "profit_factor": f"{pf:.2f}",
        "active_positions": pos_data,
        "closed_trades": closed_data,
        "logs": logs_text
    })

@app.post("/api/start")
async def start_bot():
    runtime.start()
    return {"status": "started"}

@app.post("/api/stop")
async def stop_bot():
    runtime.stop()
    return {"status": "stopped"}

@app.post("/api/reset")
async def reset_bot():
    runtime.reset_database()
    return {"status": "reset"}

# Serve frontend static files
# We wait for the subagent to build `frontend/dist`
if os.path.exists("frontend/dist"):
    app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
