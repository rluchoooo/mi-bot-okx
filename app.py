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
}


# ──────────────────────────────────────────────────────────────────────
# Dashboard HTML
# ──────────────────────────────────────────────────────────────────────

def build_dashboard() -> str:
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

    # Aggregate Live PNL from last_positions
    live_upl_total = 0.0
    for pos in getattr(runtime, "last_positions", {}).values():
        try:
            upl_raw = pos.get("upl", "") or "0"
            live_upl_total += float(upl_raw) if upl_raw else 0.0
        except (ValueError, TypeError):
            pass
        
    running_badge = "QUANTUM ACTIVO" if runtime.running else "DETENIDO"
    status_cls    = "ok" if runtime.running else "warn"
    shield_label  = _esc(runtime.shield.status_label)
    shield_cls    = "" if "LIBRE" in shield_label else "shield-active"

    # ── Position rows ──
    pos_rows = ""
    for t in open_trades:
        strat_lbl = STRATEGY_SHORT.get(t.strategy.value if hasattr(t.strategy, "value") else str(t.strategy), t.strategy)
        side_cls  = "pos" if (t.side.value if hasattr(t.side, "value") else t.side) == "long" else "neg"
        side_lbl  = (t.side.value if hasattr(t.side, "value") else t.side).upper()
        sym       = _esc(t.symbol.replace("-USDT-SWAP", "USDT"))
        
        status_val = (t.status.value if hasattr(t.status, "value") else str(t.status)).upper()
        if status_val == "OPEN" or status_val == "BREAKEVEN" or status_val == "TRAILING":
            status_html = '<span style="border: 1px solid #3b82f6; color: #3b82f6; padding: 3px 8px; border-radius: 4px; font-size: 10px; font-weight: 800; cursor: default; letter-spacing: 0.05em;">MONITOR</span>'
        else:
            status_html = f'<span class="muted">{status_val}</span>'

        # Build badges
        badges = []
        badges.append('<span style="background: rgba(0, 255, 136, 0.1); border: 1px solid rgba(0, 255, 136, 0.3); color: #00ff88; padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 800; margin-right: 5px;">⚡ LIVE</span>')
        
        if getattr(t, "trailing_active", 0) == 1 or status_val == "TRAILING":
            badges.append('<span style="background: rgba(255, 166, 0, 0.1); border: 1px solid rgba(255, 166, 0, 0.3); color: #ffa600; padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 800; margin-right: 5px;">TRAILING</span>')
        elif getattr(t, "tp2_filled", 0) == 1:
            badges.append('<span style="background: rgba(255, 166, 0, 0.1); border: 1px solid rgba(255, 166, 0, 0.3); color: #ffa600; padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 800; margin-right: 5px;">TRAILING</span>')
        elif getattr(t, "tp1_filled", 0) == 1:
            badges.append('<span style="background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); color: #cbd5e1; padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 700; margin-right: 5px;">Buscando Take 2</span>')
        else:
            badges.append('<span style="background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); color: #cbd5e1; padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 700; margin-right: 5px;">Buscando Take 1</span>')
            
        if getattr(t, "profit_lock_active", 0) == 1 or status_val == "BREAKEVEN":
            badges.append('<span style="background: rgba(166, 124, 255, 0.1); border: 1px solid rgba(166, 124, 255, 0.3); color: #a67cff; padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 800;">BREAKEVEN</span>')

        badge_html = "".join(badges)
        
        sl_text  = _fmt(t.sl_price)  if t.sl_price  else "N/A"
        tp1_text = _fmt(t.tp1_price) if getattr(t, "tp1_price", None) else (_fmt(t.tp_price) if t.tp_price else "N/A")
        tp2_text = _fmt(t.tp2_price) if getattr(t, "tp2_price", None) else "N/A"
        sltp_html = (
            f'<div style="font-size: 10px; color: #64748b; margin-top: 6px; font-weight: 600;">'
            f'SL <span style="color:#ff4d6d">{sl_text}</span> '
            f'| TP1 <span style="color:#00ff88">{tp1_text}</span> '
            f'| TP2 <span style="color:#00e5ff">{tp2_text}</span></div>'
        )
        
        shield_html = f'<div style="display: flex; flex-direction: column; justify-content: center;"><div style="display: flex; align-items: center;">{badge_html}</div>{sltp_html}</div>'

        # Match live_upl by instId directly from last_positions dict values
        live_upl = 0.0
        if hasattr(runtime, "last_positions"):
            for pos in runtime.last_positions.values():
                if pos.get("instId") == t.symbol:
                    try:
                        upl_raw = pos.get("upl", "") or "0"
                        live_upl = float(upl_raw) if upl_raw else 0.0
                    except (ValueError, TypeError):
                        pass
                    break
                    
        upl_val = live_upl
        sign    = "+" if upl_val >= 0 else ""
        pnl_col = _pnl_color(upl_val)
        pnl_lbl = f'<span style="color: {pnl_col} !important; font-weight: 900; text-shadow: 0 0 5px {pnl_col}80;">{sign}{upl_val:.4f}</span>'

        pos_rows += f"""
<tr style="border-bottom: 1px solid var(--line);">
  <td style="padding: 15px 24px; color: white !important; font-weight: 800;">{sym}</td>
  <td><span style="color: #00e5ff; font-weight: 700; font-size: 11px;">{strat_lbl}</span></td>
  <td class="{side_cls}" style="font-weight: 800;">{side_lbl}</td>
  <td style="color: #cbd5e1; font-weight: 600;">{_fmt(t.entry_price)}</td>
  <td>{shield_html}</td>
  <td>{pnl_lbl}</td>
  <td>{status_html}</td>
</tr>"""

    if not pos_rows:
        pos_rows = "<tr><td colspan='7' class='muted center' style='padding: 20px;'>Sin posiciones activas.</td></tr>"

    # ── Closed trade rows ──
    trade_rows = ""
    for t in closed_trades:
        pnl   = t.realized_pnl or 0
        sym   = _esc(t.symbol.replace("-USDT-SWAP", "USDT"))
        side_cls = "pos" if (t.side.value if hasattr(t.side, "value") else t.side) == "long" else "neg"
        side_lbl = (t.side.value if hasattr(t.side, "value") else t.side).upper()
        sign  = "+" if pnl >= 0 else ""
        
        reason_raw = (t.close_reason or "").upper()
        if "TP2" in reason_raw: reason_html = '<span style="color: #ff007f; font-weight: 800; font-size: 11px;">TAKE PROFIT 2</span>'
        elif "TP1" in reason_raw: reason_html = '<span style="color: #00e5ff; font-weight: 800; font-size: 11px;">TAKE PROFIT 1</span>'
        elif "TAKE_PROFIT" in reason_raw: reason_html = '<span style="color: #00ff88; font-weight: 800; font-size: 11px;">TAKE PROFIT</span>'
        elif "TRAILING" in reason_raw: reason_html = '<span style="color: #a67cff; font-weight: 800; font-size: 11px;">TRAILING STOP</span>'
        elif "BREAKEVEN" in reason_raw: reason_html = '<span style="color: #00e5ff; font-weight: 800; font-size: 11px;">BREAKEVEN</span>'
        elif "STOP_LOSS" in reason_raw or "SL" in reason_raw:
            if pnl >= 0: reason_html = '<span style="color: #00e5ff; font-weight: 800; font-size: 11px;">BREAKEVEN</span>'
            else: reason_html = '<span style="color: #ff2a55; font-weight: 800; font-size: 11px;">STOP LOSS</span>'
        else: reason_html = f'<span class="muted" style="font-size: 11px;">{_esc(reason_raw)}</span>'
            
        strat  = STRATEGY_SHORT.get(t.strategy.value if hasattr(t.strategy, "value") else str(t.strategy), "?")
        pnl_col = _pnl_color(pnl)
        trade_rows += f"""
<tr style="border-bottom: 1px solid var(--line);">
  <td style="padding: 15px 24px; color: white !important; font-weight: 800;">{sym}</td>
  <td class="{side_cls}" style="font-weight: 800;">{side_lbl}</td>
  <td><span style="color: #00e5ff; font-weight: 700; font-size: 11px;">{strat}</span></td>
  <td style="color: #cbd5e1; font-weight: 600;">{_fmt(t.entry_price)}</td>
  <td style="color: #cbd5e1; font-weight: 600;">{_fmt(t.close_price or 0)}</td>
  <td>{reason_html}</td>
  <td style="color: {pnl_col} !important; font-weight: 900; text-shadow: 0 0 5px {pnl_col}80;">{sign}{pnl:.4f}</td>
</tr>"""

    if not trade_rows:
        trade_rows = "<tr><td colspan='7' class='muted center' style='padding: 20px;'>Sin operaciones cerradas.</td></tr>"

    # ── Terminal lines ──
    terminal = "".join(
        f"<div><span class='term-prefix'>[QUANTUM]</span> {_esc(l)}</div>" for l in reversed(logs)
    ) or "<div><span class='term-prefix'>[SYSTEM]</span> Terminal listo. Aguardando señales...</div>"

    win_bar_pct = min(max(win_rate, 3), 100)
    
    avg_win = stats.get("avg_win", 0)
    avg_loss = stats.get("avg_loss", 0)
    edge = (win_rate / 100 * avg_win) - ((100 - win_rate) / 100 * avg_loss)
    edge_cls = "pos" if edge >= 0 else "neg"

    return f"""
<div class="terminal-shell">
  <!-- Top Bar -->
  <div class="topbar">
    <div class="brand">
      <div class="bolt">⚡</div>
      <div>
        <div class="brand-name">OKX QUANTUM <span style="font-weight:300;opacity:0.7">|</span> ELITE TERMINAL</div>
        <div class="badges"><span>ELITE V10 PRO</span><span style="border-color: #ff2a55 !important; color: #ff2a55 !important; background: rgba(255, 42, 85, 0.15) !important;">LIVE MODE</span></div>
      </div>
    </div>
    
    <div style="text-align: right;">
      <div class="status-pill {status_cls}">{running_badge}</div>
    </div>
  </div>

  <!-- Row 1: Hero Cards -->
  <div class="grid hero-grid">
    <!-- Saldos -->
    <section class="card">
      <div class="label">SALDOS DISPONIBLES</div>
      <div class="big">{runtime.current_exchange_balance:,.3f} USDT</div>
      <div class="sub" style="margin-top: 5px;">50.000,00 USDC</div>
      <div class="mini" style="margin-top: 25px; color: white;">
        PNL VIVO: <span class="{_pnl_cls(live_upl_total)}"><b>{'+' if live_upl_total>=0 else ''}{live_upl_total:.2f} USDT</b></span>
      </div>
    </section>

    <!-- Sesgo de Mercado -->
    <section class="card center-content">
      <div class="label">SESGO DE MERCADO</div>
      <div style="display: flex; align-items: center; justify-content: center; gap: 20px; margin-top: 15px;">
        <div style="font-size: 40px; color: {'#00ff88' if 'BULL' in shield_label else '#ff2a55' if 'BEAR' in shield_label else '#00e5ff'};">
          {'↗' if 'BULL' in shield_label else '↘' if 'BEAR' in shield_label else '→'}
        </div>
        <div style="font-size: 38px; font-weight: 900; color: {'#00ff88' if 'BULL' in shield_label else '#ff2a55' if 'BEAR' in shield_label else '#00e5ff'}; text-shadow: 0 0 15px currentColor;">
          {'LARGO' if 'BULL' in shield_label else 'CORTO' if 'BEAR' in shield_label else 'LIBRE'}
        </div>
      </div>
      <div class="sub" style="margin-top: 15px; font-weight: 700; color: #00ff88;">
        BTC ... FILTRO {'BULLISH' if 'BULL' in shield_label else 'BEARISH' if 'BEAR' in shield_label else 'NEUTRAL'}
      </div>
    </section>

    <!-- Estrategia -->
    <section class="card strategy-card">
      <div class="label" style="text-align: right;">ESTRATEGIA OKX QUANTUM V10</div>
      <div class="kv"><span>APALANCAMIENTO</span><b style="color: #00e5ff;">10X</b></div>
      <div class="kv"><span>MONTO</span><b>$8 USDT</b></div>
      <div class="kv"><span>EJECUCIÓN</span><b>15 MS (WS) / 15 S (REST)</b></div>
      <div class="kv"><span>VOL. MIN (24H)</span><b>$500K</b></div>
      <div class="kv"><span>ATR SL / TP</span><b>2.0 / 4.0</b></div>
      <div class="kv"><span>PROTECCIÓN</span><b>BE 30% / TS 50%</b></div>
    </section>
  </div>

  <!-- Row 2: Stats Grid -->
  <div class="grid stat-grid">
    <!-- PNL Diario -->
    <section class="stat-card" style="border-left: 5px solid #ff2a55 !important;">
      <div class="label" style="color: white !important; font-size: 11px !important;">PNL DIARIO (RESETEO 11 PM)</div>
      <strong class="{_pnl_cls(pnl_today)}" style="font-size: 38px; display: block; margin-top: 10px; margin-bottom: 10px;">
        {'+' if pnl_today>=0 else ''}{pnl_today:.2f}
      </strong>
      <div style="font-size: 14px; font-weight: 800; color: white;">
        G: {stats.get('wins_count', 0)} &nbsp;&nbsp; P: {stats.get('losses_count', 0)} &nbsp;&nbsp; <span style="color: #cbd5e1;">OPS: {total_trades}</span>
      </div>
    </section>

    <!-- Promedio Ganador / Perdedor -->
    <section class="stat-card" style="display: flex; gap: 20px;">
      <div style="flex: 1;">
        <div class="label" style="color: white !important; font-size: 11px !important;">PROMEDIO GANADOR (AVG WIN)</div>
        <strong class="pos" style="font-size: 28px; display: block; margin-top: 10px;">+${avg_win:.2f}</strong>
      </div>
      <div style="width: 1px; background: var(--line);"></div>
      <div style="flex: 1;">
        <div class="label" style="color: white !important; font-size: 11px !important;">PROMEDIO PERDEDOR (AVG LOSS)</div>
        <strong class="neg" style="font-size: 28px; display: block; margin-top: 10px;">-${avg_loss:.2f}</strong>
      </div>
    </section>

    <!-- Expectativa Matemática -->
    <section class="stat-card" style="border-right: 5px solid #00e5ff !important; text-align: right;">
      <div class="label" style="color: white !important; font-size: 11px !important;">EXPECTATIVA MATEMÁTICA (EDGE)</div>
      <strong class="{edge_cls}" style="font-size: 38px; display: block; margin-top: 10px; margin-bottom: 10px;">
        {'+' if edge>=0 else ''}${edge:.2f}
      </strong>
      <small style="color: #cbd5e1;">VALOR ESPERADO POR TRADE</small>
    </section>
  </div>

  <!-- Row 3: Main Grid -->
  <div class="grid main-grid">
    <!-- Monitor Posiciones -->
    <section class="card positions-card" style="padding: 0; overflow: hidden;">
      <div class="section-head" style="padding: 20px 24px; border-bottom: 1px solid var(--line); margin: 0;">
        <span style="color: #00e5ff;">📺 MONITOR DE POSICIONES ACTIVAS</span>
        <b style="color: white; background: rgba(0, 229, 255, 0.2); padding: 4px 12px; border-radius: 999px;">{len(open_trades)} ACTIVAS</b>
      </div>
      <table style="margin: 0; width: 100%;">
        <thead>
          <tr>
            <th style="padding: 15px 24px;">SÍMBOLO</th>
            <th>ESTRATEGIA</th>
            <th>DIRECCIÓN</th>
            <th>ENTRADA</th>
            <th>GESTIÓN</th>
            <th>PNL VIVO</th>
            <th>STATUS</th>
          </tr>
        </thead>
        <tbody>{pos_rows}</tbody>
      </table>
    </section>

    <!-- Rendimiento Global -->
    <section class="card">
      <div class="section-head"><span style="color: #a67cff;">📈 RENDIMIENTO GLOBAL</span></div>
      
      <div style="margin-top: 25px;">
        <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
          <small style="color: #cbd5e1; font-weight: 800; font-size: 12px;">WIN RATE</small>
          <strong style="color: white; font-size: 16px;">{win_rate:.1f}%</strong>
        </div>
        <div class="bar" style="margin: 0 0 30px 0;"><span style="width:{win_bar_pct:.1f}%"></span></div>
      </div>

      <div class="perf-grid">
        <div>
          <small>PROFIT FACTOR</small>
          <strong style="font-size: 32px;">{pf:.2f}</strong>
        </div>
        <div>
          <small>RISK / REWARD</small>
          <strong style="font-size: 32px; color: white;">1 : {(avg_win/avg_loss if avg_loss > 0 else 0):.1f}</strong>
        </div>
        <div style="margin-top: 15px;">
          <small>MEJOR TRADE</small>
          <strong class="pos">+{best:.2f}</strong>
        </div>
        <div style="margin-top: 15px;">
          <small>PEOR TRADE</small>
          <strong class="neg">{worst:.2f}</strong>
        </div>
      </div>
    </section>
  </div>

  <!-- Row 4: Lower Grid -->
  <div class="grid lower-grid" style="margin-top: 24px;">
    <section class="card history-card" style="padding: 0; overflow: hidden;">
      <div class="section-head" style="padding: 20px 24px; border-bottom: 1px solid var(--line); margin: 0;">
        <span style="color: white;">📜 HISTORIAL DE TRADES</span>
      </div>
      <table style="margin: 0; width: 100%;">
        <thead>
          <tr>
            <th style="padding: 15px 24px;">SÍMBOLO</th>
            <th>DIRECCIÓN</th>
            <th>ESTRATEGIA</th>
            <th>ENTRADA</th>
            <th>SALIDA</th>
            <th>CAUSA DE CIERRE</th>
            <th>PNL</th>
          </tr>
        </thead>
        <tbody>{trade_rows}</tbody>
      </table>
    </section>
    
    <section class="card terminal-card" style="padding: 0;">
      <div class="section-head" style="padding: 20px 24px; border-bottom: 1px solid var(--line); margin: 0;">
        <span style="color: white;">💻 TERMINAL DE EJECUCIÓN</span>
      </div>
      <div class="terminal" style="border: none; border-radius: 0; margin: 0; max-height: 380px;">{terminal}</div>
    </section>
  </div>
</div>
"""

# ──────────────────────────────────────────────────────────────────────
# CSS
# ──────────────────────────────────────────────────────────────────────

APP_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;500;700;900&display=swap');
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0b0c10;
  --panel: #12141a;
  --panel-2: #161821;
  --line: rgba(255, 255, 255, 0.05);
  --text: #ffffff;
  --title: #ffffff;
  --green: #00ff88;
  --red: #ff2a55;
  --cyan: #00e5ff;
  --purple: #a67cff;
  --muted: #64748b;
  --warn: #ffb74d;
}

html, body, .gradio-container, .main-container, .gradio-container-3-50-2 {
  background-color: var(--bg) !important;
  background: var(--bg) !important;
  font-family: 'Outfit', sans-serif;
  color: #ffffff !important;
  max-width: 100% !important;
  padding: 0 !important;
  margin: 0 !important;
}

.terminal-shell {
  max-width: 1600px;
  margin: 0 auto;
  padding: 24px;
}

.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  padding: 16px 24px;
  background: transparent !important;
  border-bottom: 1px solid var(--line);
}

.brand {
  display: flex;
  gap: 16px;
  align-items: center;
}

.bolt {
  width: 48px;
  height: 48px;
  background: #00e5ff;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 24px;
  color: black;
}

.brand-name {
  font-size: 22px;
  font-weight: 900;
  letter-spacing: .05em;
  color: #ffffff !important;
}

.badges {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}

.badges span {
  border-radius: 4px;
  padding: 3px 8px;
  font-size: 10px;
  font-weight: 800;
  text-transform: uppercase;
  background: rgba(0, 229, 255, 0.15);
  border: 1px solid #00e5ff;
  color: #00e5ff;
}

.status-pill {
  padding: 8px 24px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 900;
  border: 1px solid var(--line);
  letter-spacing: 0.05em;
  color: white;
  background: var(--panel);
}

.ok { border-color: var(--green); color: var(--green); }
.warn { border-color: var(--red); color: var(--red); }

.grid {
  display: grid;
  gap: 24px;
}
.hero-grid { grid-template-columns: 1fr 1.2fr 1.5fr; }
.stat-grid { grid-template-columns: 1fr 1fr 1fr; margin-top: 24px; }
.main-grid { grid-template-columns: 2fr 1fr; margin-top: 24px; }
.lower-grid { grid-template-columns: 1.5fr 1fr; }

.card, .stat-card {
  background: var(--panel) !important;
  border: 1px solid var(--line) !important;
  border-radius: 8px !important;
  padding: 24px;
  box-shadow: 0 4px 15px rgba(0,0,0,0.5) !important;
}

.center-content {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
}

.label {
  color: white !important;
  font-size: 12px !important;
  font-weight: 800 !important;
  letter-spacing: .05em !important;
  text-transform: uppercase !important;
}

.big {
  font-size: 36px !important;
  font-weight: 900 !important;
  margin-top: 10px;
  color: #ffffff !important;
}

.sub {
  color: var(--muted) !important;
  font-size: 12px;
  font-weight: 600;
}

.kv {
  display: flex;
  justify-content: space-between;
  margin-top: 10px;
  font-size: 12px;
  font-weight: 700;
  color: var(--muted);
  border-bottom: 1px solid var(--line);
  padding-bottom: 6px;
}
.kv b { color: white; }

.pos { color: var(--green) !important; }
.neg { color: var(--red) !important; }

.section-head {
  padding-bottom: 16px;
  font-size: 13px;
  font-weight: 900;
  letter-spacing: .05em;
  display: flex;
  justify-content: space-between;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  text-align: left;
}

th {
  color: var(--muted);
  font-weight: 800;
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}

td {
  padding: 12px 0;
}

.bar {
  height: 6px;
  background: var(--panel-2);
  border-radius: 999px;
  overflow: hidden;
}

.bar span {
  display: block;
  height: 100%;
  background: var(--cyan);
}

.perf-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}
.perf-grid small { color: var(--muted); font-size: 11px; font-weight: 800; }
.perf-grid strong { display: block; font-size: 20px; margin-top: 4px; font-weight: 900;}

.terminal {
  background: #000000 !important;
  padding: 20px;
  font-family: "Cascadia Mono", Consolas, monospace;
  color: #a3a3a3 !important;
  font-size: 11px;
  line-height: 1.6;
  overflow-y: auto;
}

.term-prefix {
  color: #00e5ff !important;
  font-weight: 800;
  margin-right: 8px;
}

.control-row {
  display: flex;
  gap: 12px;
  justify-content: flex-end;
  position: absolute;
  top: 36px;
  right: 48px;
  z-index: 100;
}

.control-row button {
  background: var(--panel) !important;
  border: 1px solid var(--line) !important;
  color: white !important;
  font-size: 11px !important;
  font-weight: 800 !important;
  padding: 8px 16px !important;
  border-radius: 4px !important;
  text-transform: uppercase !important;
  letter-spacing: .05em !important;
}

.control-row button:hover {
  background: rgba(255,255,255,0.05) !important;
  border-color: rgba(255,255,255,0.2) !important;
}

/* Specific buttons */
#btn-start { border-color: rgba(0, 255, 136, 0.3) !important; }
#btn-stop { border-color: rgba(255, 42, 85, 0.3) !important; }

/* Hide gradio footer */
footer { display: none !important; }
"""


# ──────────────────────────────────────────────────────────────────────
# Gradio App
# ──────────────────────────────────────────────────────────────────────

# Auto-start bot on container boot
runtime.start()

with gr.Blocks(title="Quantum V10 Pro Terminal", css=APP_CSS, fill_width=True) as demo:
    with gr.Row(elem_classes=["control-row"]):
        start_btn   = gr.Button("▶️ Iniciar Bot",  variant="primary", elem_classes=["btn-start"])
        stop_btn    = gr.Button("⏹️ Detener", elem_classes=["btn-stop"])
        refresh_btn = gr.Button("🔄 Actualizar", elem_classes=["btn-refresh"])
        shield_btn  = gr.Button("🛡️ Desbloquear Escudo", elem_classes=["btn-shield"])
        reset_btn   = gr.Button("🗑️ Resetear Stats", variant="stop", elem_classes=["btn-reset"])

    output = gr.HTML(build_dashboard())

    start_btn.click(fn=lambda: (runtime.start(), build_dashboard())[1], outputs=output)
    stop_btn.click(fn=lambda: (runtime.stop(), build_dashboard())[1], outputs=output)
    refresh_btn.click(fn=build_dashboard, outputs=output)
    shield_btn.click(fn=lambda: (runtime.shield.force_clear(), build_dashboard())[1], outputs=output)
    reset_btn.click(fn=lambda: (runtime.reset_database(), build_dashboard())[1], outputs=output)

    if hasattr(gr, "Timer"):
        gr.Timer(8).tick(fn=build_dashboard, outputs=output)


if __name__ == "__main__":
    demo.launch()
