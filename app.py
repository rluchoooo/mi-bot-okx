"""
app.py – Dashboard Gradio para el Quantum V10 Pro Bot.
"""
from __future__ import annotations

import os
from decimal import Decimal

from dotenv import load_dotenv
load_dotenv()

import gradio as gr

from models import TradeStatus, TradeSide, create_all
from risk import pnl_usd
import logging

logging.basicConfig(level=logging.INFO)
from scanner import QuantumBotRuntime

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

def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _fmt(v: float, decimals: int = None) -> str:
    if v is None:
        return "0.00"
    if decimals is not None:
        return f"{v:.{decimals}f}"
    val = abs(v)
    if val == 0:
        return "0.00"
    if val >= 100:
        d = 2
    elif val >= 1:
        d = 4
    elif val >= 0.01:
        d = 6
    elif val >= 0.0001:
        d = 8
    else:
        d = 10
    return f"{v:.{d}f}"

def _pnl_color(v: float) -> str:
    return "#00ff88" if v >= 0 else "#ff2a55"

def _pnl_cls(v: float) -> str:
    return "pos" if v >= 0 else "neg"


STATUS_ICON = {
    "OPEN":       "🔵",
    "BREAKEVEN":  "🛡️",
    "TRAILING":   "🎯",
    "EARLY_EXIT": "⚡",
    "CLOSED":     "✅",
}

STRATEGY_SHORT = {
    "QUANTUM_SMC_V10_PRO":    "QUANTUM SMC V10 (FVG)",
    "SUPERTREND_PULLBACK_V3": "SUPERTREND PULLBACK V3",
    "AUTO_ADOPTED":           "AUTO ADOPTED",
    "SMC_LIQ_SWEEP":          "SMC LIQ SWEEP",
    "SMC_FVG_MITIG":          "SMC FVG MITIG",
    "SMC_OB_RETEST":          "SMC OB RETEST",
    "SMC_AMD_PO3":            "SMC AMD PO3",
    "ST_EMA_REGIME_MTF":      "SUPERTREND EMA PRO",
}


# ──────────────────────────────────────────────────────────────────────
# Dashboard HTML
# ──────────────────────────────────────────────────────────────────────



def fetch_dashboard_data():
    open_trades   = runtime.get_open_trades()
    closed_trades = runtime.get_closed_trades(n=8)
    stats         = runtime.get_stats()
    logs          = runtime.get_logs(n=20)

    # Stats
    total_pnl     = stats.get("total_pnl", 0)
    win_rate      = stats.get("win_rate", 0)
    pf            = stats.get("profit_factor", 0)
    total_trades  = stats.get("total_trades", 0)
    best          = stats.get("best_trade", 0)
    worst         = stats.get("worst_trade", 0)
    pnl_today     = stats.get("pnl_today", 0)
    avg_win       = stats.get("avg_win", 0)
    avg_loss      = stats.get("avg_loss", 0)
    edge          = (win_rate / 100 * avg_win) - ((100 - win_rate) / 100 * avg_loss)

    # Active Positions
    pos_data = []
    live_upl_total = 0.0
    
    lp_values = getattr(runtime, "last_positions", {}).values()
    
    for pos in lp_values:
        try:
            upl_raw = pos.get("upl", "") or "0"
            live_upl_total += float(upl_raw) if upl_raw else 0.0
        except:
            pass

    for t in open_trades:
        raw_strat = t.strategy.value if hasattr(t.strategy, "value") else str(t.strategy)
        raw_strat = raw_strat.replace("Strategy.", "")
        strat_lbl = STRATEGY_SHORT.get(raw_strat, raw_strat)
        side_lbl  = (t.side.value if hasattr(t.side, "value") else str(t.side)).upper()
        if "." in side_lbl: side_lbl = side_lbl.split(".")[-1]
        sym       = t.symbol.replace("-USDT-SWAP", "USDT")
        
        status_val = (t.status.value if hasattr(t.status, "value") else str(t.status)).upper()
        if "." in status_val: status_val = status_val.split(".")[-1]

        # Fix PNL Sync
        live_upl = 0.0
        if hasattr(runtime, "last_positions") and runtime.last_positions:
            for lp_pos in runtime.last_positions.values():
                lp_inst = lp_pos.get("instId", "")
                
                # Normalize both symbols to just the base asset (e.g. THETA-USDT-SWAP -> THETA)
                lp_base = lp_inst.replace("-", "").replace("SWAP", "").replace("USDT", "")
                t_base = t.symbol.replace("-", "").replace("SWAP", "").replace("USDT", "")
                
                if lp_base == t_base:
                    try:
                        upl_raw = lp_pos.get("upl", "") or "0"
                        live_upl = float(upl_raw) if upl_raw else 0.0
                        break
                    except:
                        pass
        
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
    
    status_str = "🟢 QUANTUM BOT IS RUNNING" if runtime.running else "🔴 STOPPED"
    
    bal = getattr(runtime, "current_exchange_balance", 0.0)

    return (
        f"**STATUS:** {status_str}",
        f"{bal:,.2f} USDT",
        f"{live_upl_total:+.2f} USDT",
        f"{pnl_today:+.2f} USDT",
        f"{win_rate:.1f}%",
        f"{pf:.2f}",
        pos_data,
        closed_data,
        logs_text
    )

custom_css = '''
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=JetBrains+Mono:wght@400;700&display=swap');

body, .gradio-container {
    background-color: #0b0f19 !important;
    font-family: 'Inter', sans-serif !important;
    color: #f3f4f6 !important;
}

/* Tablas Premium */
table {
    border-collapse: separate !important;
    border-spacing: 0 !important;
    border-radius: 8px !important;
    overflow: hidden !important;
    border: 1px solid #1f2937 !important;
}
thead th {
    background-color: #111827 !important;
    color: #06b6d4 !important;
    font-weight: 700 !important;
    font-size: 0.9rem !important;
    text-transform: uppercase;
    padding: 12px 15px !important;
}
tbody td {
    background-color: #1f2937 !important;
    color: #f9fafb !important;
    font-weight: 600 !important;
    padding: 10px 15px !important;
    border-bottom: 1px solid #374151 !important;
}
tbody tr:hover td {
    background-color: #374151 !important;
}

/* Botones Glowing */
button.primary {
    background: linear-gradient(135deg, #06b6d4, #3b82f6) !important;
    border: none !important;
    color: white !important;
    box-shadow: 0 0 10px rgba(6, 182, 212, 0.5) !important;
    transition: all 0.3s ease !important;
}
button.primary:hover {
    box-shadow: 0 0 20px rgba(6, 182, 212, 0.8) !important;
    transform: scale(1.02);
}
button.stop {
    background: linear-gradient(135deg, #ef4444, #b91c1c) !important;
    border: none !important;
    color: white !important;
    box-shadow: 0 0 10px rgba(239, 68, 68, 0.5) !important;
    transition: all 0.3s ease !important;
}
button.stop:hover {
    box-shadow: 0 0 20px rgba(239, 68, 68, 0.8) !important;
    transform: scale(1.02);
}

/* Cajas de texto e inputs */
.gr-box, .gr-form, .gr-panel, .gr-block {
    background-color: #111827 !important;
    border: 1px solid #374151 !important;
    border-radius: 8px !important;
}
.gr-text-input, textarea, input {
    background-color: #1f2937 !important;
    color: #ffffff !important;
    border: 1px solid #4b5563 !important;
    font-weight: bold !important;
    font-size: 1.1rem !important;
}

/* Textos importantes */
h1, h2, h3 {
    color: #f3f4f6 !important;
}
.markdown-text strong {
    color: #06b6d4 !important;
}

/* Log Terminal */
.cm-s-default, .cm-content {
    background-color: #000000 !important;
    color: #10b981 !important;
    font-family: 'JetBrains Mono', monospace !important;
    border-radius: 8px !important;
    border: 1px solid #374151 !important;
}
'''

cyber_theme = gr.themes.Monochrome(primary_hue="cyan", secondary_hue="blue", neutral_hue="slate")

with gr.Blocks(title="OKX Quantum Elite", theme=cyber_theme, css=custom_css) as demo:
    gr.Markdown("# ⚡ OKX QUANTUM ELITE TERMINAL")
    
    with gr.Row():
        start_btn   = gr.Button("▶️ Start Bot", variant="primary")
        stop_btn    = gr.Button("⏹️ Stop")
        refresh_btn = gr.Button("🔄 Refresh Data")
        reset_btn   = gr.Button("🗑️ Reset Stats", variant="stop")
    
    with gr.Row():
        txt_status = gr.Markdown("**STATUS:** LOADING...")
        txt_bal    = gr.Textbox(label="Available Balance (USDT)")
        txt_upl    = gr.Textbox(label="Live PNL (USDT)")
        
    with gr.Row():
        txt_today  = gr.Textbox(label="Daily PNL")
        txt_wr     = gr.Textbox(label="Win Rate")
        txt_pf     = gr.Textbox(label="Profit Factor")
        
    gr.Markdown("### 📺 ACTIVE POSITIONS")
    tbl_pos = gr.DataFrame(headers=["SYMBOL", "STRATEGY", "SIDE", "ENTRY", "MANAGEMENT", "LIVE PNL", "STATUS"], interactive=False)
    
    gr.Markdown("### 📜 TRADE HISTORY")
    tbl_closed = gr.DataFrame(headers=["SYMBOL", "SIDE", "STRATEGY", "ENTRY", "EXIT", "REASON", "PNL"], interactive=False)
    
    gr.Markdown("### 💻 SYSTEM TERMINAL")
    txt_logs = gr.Code(language="shell", interactive=False)
    
    outputs = [txt_status, txt_bal, txt_upl, txt_today, txt_wr, txt_pf, tbl_pos, tbl_closed, txt_logs]
    
    start_btn.click(fn=lambda: (runtime.start(), fetch_dashboard_data())[1], outputs=outputs)
    stop_btn.click(fn=lambda: (runtime.stop(), fetch_dashboard_data())[1], outputs=outputs)
    refresh_btn.click(fn=fetch_dashboard_data, outputs=outputs)
    reset_btn.click(fn=lambda: (runtime.reset_database(), fetch_dashboard_data())[1], outputs=outputs)
    
    demo.load(fn=fetch_dashboard_data, outputs=outputs)
    
    if hasattr(gr, "Timer"):
        gr.Timer(5).tick(fn=fetch_dashboard_data, outputs=outputs)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
