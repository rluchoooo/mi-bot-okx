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
        sl_lbl    = _fmt(t.trail_sl or t.sl_price)
        tp_lbl    = f'<span class="tp-col">{_fmt(t.tp_price)}</span>' if t.tp_price else '<span class="badge badge-ts">🎯 TRAILING</span>'
        
        status_val = (t.status.value if hasattr(t.status, "value") else str(t.status)).upper()
        if status_val == "OPEN":
            status_html = '<span class="badge badge-open">🔵 OPEN</span>'
        elif status_val == "BREAKEVEN" or t.be_activated:
            status_html = '<span class="badge badge-be">🛡️ BREAKEVEN</span>'
        elif status_val == "TRAILING" or t.trail_activated:
            status_html = '<span class="badge badge-ts">🎯 TRAILING</span>'
        elif status_val == "EARLY_EXIT":
            status_html = '<span class="badge badge-shock">⚡ EARLY EXIT</span>'
        else:
            status_html = f'<span class="badge badge-stale">{status_val}</span>'

        # BE / TRAIL Column Logic
        if t.trail_activated or status_val == "TRAILING":
            trail_val = t.trail_sl or t.sl_price
            shield_html = f'<span class="badge badge-ts">🎯 TS: {_fmt(trail_val)}</span>'
        elif t.be_activated or status_val == "BREAKEVEN":
            be_val = t.sl_price
            shield_html = f'<span class="badge badge-be">🛡️ BE: {_fmt(be_val)}</span>'
        else:
            shield_html = '<span class="badge badge-stale">⏳ PENDIENTE</span>'

        # Live OKX Position Mark Price & PnL
        pos_data = getattr(runtime, "last_positions", {}).get(t.symbol, {})
        mark_px = float(pos_data.get("markPx", 0)) if pos_data and pos_data.get("markPx") else 0.0
        upl_val = float(pos_data.get("upl", 0)) if pos_data and pos_data.get("upl") else 0.0
        
        if mark_px > 0:
            price_lbl = _fmt(mark_px)
            sign = "+" if upl_val >= 0 else ""
            pnl_cls = "pos" if upl_val >= 0 else "neg"
            pnl_lbl = f'<span class="{pnl_cls}"><b>{sign}{upl_val:.2f} USDT</b></span>'
        else:
            price_lbl = '<span class="muted">⏳ Cargando...</span>'
            pnl_lbl = '<span class="muted">⏳ Cargando...</span>'

        pos_rows += f"""
<tr>
  <td style="color: #ff9f43 !important; font-weight: 900;">{sym}</td>
  <td class="{side_cls}">{side_lbl}</td>
  <td><span class="tag tag-strat">{strat_lbl}</span></td>
  <td>{_fmt(t.entry_price)}</td>
  <td style="font-weight: 800;">{price_lbl}</td>
  <td>{pnl_lbl}</td>
  <td><span class="warn-sl">{sl_lbl}</span></td>
  <td>{tp_lbl}</td>
  <td>{shield_html}</td>
  <td>{status_html}</td>
</tr>"""

    if not pos_rows:
        pos_rows = "<tr><td colspan='10' class='muted center'>Sin posiciones abiertas. Escaneando mercado...</td></tr>"

    # ── Closed trade rows ──
    trade_rows = ""
    for t in closed_trades:
        pnl   = t.realized_pnl or 0
        sym   = _esc(t.symbol.replace("-USDT-SWAP", "USDT"))
        side_cls = "pos" if (t.side.value if hasattr(t.side, "value") else t.side) == "long" else "neg"
        side_lbl = (t.side.value if hasattr(t.side, "value") else t.side).upper()
        sign  = "+" if pnl >= 0 else ""
        
        reason_raw = (t.close_reason or "").upper()
        if "TAKE_PROFIT" in reason_raw:
            reason_html = '<span class="badge badge-tp">✅ TAKE PROFIT</span>'
        elif "TRAILING" in reason_raw:
            reason_html = '<span class="badge badge-ts">🎯 TRAILING</span>'
        elif "BREAKEVEN" in reason_raw:
            reason_html = '<span class="badge badge-be">🛡️ BREAKEVEN</span>'
        elif "STOP_LOSS" in reason_raw:
            reason_html = '<span class="badge badge-sl">🛑 STOP LOSS</span>'
        elif "SHOCK" in reason_raw or "KILL" in reason_raw:
            reason_html = '<span class="badge badge-shock">⚡ SHOCK CUT</span>'
        elif "STALE" in reason_raw:
            reason_html = '<span class="badge badge-stale">🗑️ STALE</span>'
        else:
            reason_html = f'<span class="badge badge-stale">{_esc(reason_raw)}</span>'
            
        strat  = STRATEGY_SHORT.get(t.strategy.value if hasattr(t.strategy, "value") else str(t.strategy), "?")
        trade_rows += f"""
<tr>
  <td style="color: #f59e0b !important;">{sym}</td>
  <td class="{side_cls}">{side_lbl}</td>
  <td><span class="tag tag-strat">{strat}</span></td>
  <td>{_fmt(t.entry_price)}</td>
  <td>{_fmt(t.close_price or 0)}</td>
  <td>{reason_html}</td>
  <td class="{_pnl_cls(pnl)}"><b>{sign}{pnl:.2f} USDT</b></td>
</tr>"""

    if not trade_rows:

        trade_rows = "<tr><td colspan='7' class='muted center'>Sin operaciones cerradas desde este arranque.</td></tr>"

    # ── Terminal lines ──
    terminal = "".join(
        f"<div><span class='term-prefix'>[QUANTUM]</span> {_esc(l)}</div>" for l in reversed(logs)
    ) or "<div><span class='term-prefix'>[SYSTEM]</span> Terminal listo. Aguardando señales...</div>"

    win_bar_pct = min(max(win_rate, 3), 100)

    return f"""
<div class="terminal-shell">
  <div class="topbar">
    <div class="brand">
      <div class="bolt">Q</div>
      <div>
        <div class="brand-name">OKX DEMO <span style="font-weight:300;opacity:0.7">|</span> QUANTUM V10 PRO</div>
        <div class="badges"><span>ELITE</span><span>DEMO EXCHANGE</span><span>DUAL STRATEGY</span><span>$8 RISK</span></div>
      </div>
    </div>
    <div style="text-align: right; margin-right: 20px;">
      <div style="font-size:11px; color:var(--text); font-weight:800; letter-spacing:0.05em">SALDO OKX (EQUITY)</div>
      <div style="font-size:24px; font-weight:900; color:var(--green)">${runtime.current_exchange_balance:,.2f}</div>
    </div>
    <div class="status-pill {status_cls}">{running_badge}</div>
  </div>

  <div class="shield-bar {shield_cls}">
    <span class="shield-label">ESCUDO MACRO BTC →</span>
    <span class="shield-status">{shield_label}</span>
  </div>

  <div class="grid hero-grid">
    <section class="card">
      <div class="label">RIESGO POR OPERACIÓN</div>
      <div class="big">$8.00 USDT</div>
      <div class="sub">Apalancamiento 10X | Máx. 10 posiciones | Stop automático por símbolo</div>
      <div class="mini {_pnl_cls(total_pnl)}">PnL Total Realizado: {'+' if total_pnl>=0 else ''}{total_pnl:.2f} USDT</div>
    </section>
    <section class="card">
      <div class="label">POSICIONES ACTIVAS</div>
      <div class="big">{len(open_trades)} / 10</div>
      <div class="sub">Último escaneo: {_esc(runtime.last_scan)}</div>
      <div class="mini warn">{_esc(runtime.last_error or 'Sin errores')}</div>
    </section>
    <section class="card strategy-card">
      <div class="label">ESTRATEGIAS ACTIVAS</div>
      <div class="kv"><span>A: QUANTUM SMC V10 PRO</span><b class="pos">ON</b></div>
      <div class="sub">- Filtro SMC → FVG + Sweep</div>
      <div class="sub">- Volumen SMA → > 1.25x</div>
      <div class="sub">- Entrada Limit → Sniper</div>
      <div class="kv"><span>B: SUPERTREND PULLBACK V3</span><b class="pos">ON</b></div>
      <div class="sub">- EMA 9/21/50 + ADX > 20</div>
    </section>
  </div>

  <div class="grid stat-grid">
    <section class="stat-card"><div>PNL REALIZADO</div><strong class="{_pnl_cls(total_pnl)}">{'+' if total_pnl>=0 else ''}{total_pnl:.2f}</strong><small>USDT</small></section>
    <section class="stat-card accent-a"><div>WIN RATE</div><strong>{win_rate:.1f}%</strong><small>{total_trades} trades</small></section>
    <section class="stat-card accent-b"><div>PROFIT FACTOR</div><strong>{pf:.2f}</strong><small>Bruto</small></section>
    <section class="stat-card accent-c"><div>MEJOR / PEOR</div><strong class="pos">+{best:.2f}</strong><small class="neg">{worst:.2f} USDT</small></section>
  </div>

  <div class="grid main-grid">
    <section class="card positions-card">
      <div class="section-head"><span>MONITOR DE POSICIONES</span><b>{len(open_trades)} ACTIVAS</b></div>
      <table>
        <thead><tr><th>SÍMBOLO</th><th>LADO</th><th>ESTRATEGIA</th><th>ENTRADA</th><th>PRECIO ACT.</th><th>PNL ACT.</th><th>STOP LOSS</th><th>TAKE PROFIT</th><th>BE / TRAIL</th><th>ESTADO</th></tr></thead>
        <tbody>{pos_rows}</tbody>
      </table>
    </section>
    <section class="card">
      <div class="section-head"><span>CICLO DE VIDA</span></div>
      <div class="lifecycle-legend">
        <div>🔵 <b class="badge badge-open">OPEN</b> <span>Buscando recorrido</span></div>
        <div>🛡️ <b class="badge badge-be">BREAKEVEN</b> <span>SL en entrada +$1.60</span></div>
        <div>🎯 <b class="badge badge-ts">TRAILING</b> <span>Persiguiendo precio</span></div>
        <div>⚡ <b class="badge badge-shock">EARLY EXIT</b> <span>Estructura fallida</span></div>
      </div>
      <div class="label" style="margin-top:18px">RENDIMIENTO</div>
      <div class="bar"><span style="width:{win_bar_pct:.1f}%"></span></div>
      <div class="perf-grid">
        <div><small>WIN RATE</small><strong>{win_rate:.1f}%</strong></div>
        <div><small>PROFIT FACTOR</small><strong>{pf:.2f}</strong></div>
        <div><small>MEJOR TRADE</small><strong class="pos">+{best:.2f}</strong></div>
        <div><small>PEOR TRADE</small><strong class="neg">{worst:.2f}</strong></div>
      </div>
    </section>
  </div>

  <div class="grid lower-grid">
    <section class="card history-card">
      <div class="section-head"><span>HISTORIAL DE TRADES</span></div>
      <table>
        <thead><tr><th>SÍMBOLO</th><th>LADO</th><th>ESTRATEGIA</th><th>ENTRADA</th><th>SALIDA</th><th>CAUSA DE CIERRE</th><th>PNL</th></tr></thead>
        <tbody>{trade_rows}</tbody>
      </table>
    </section>
    <section class="card terminal-card">
      <div class="section-head"><span>TERMINAL DE EJECUCIÓN</span></div>
      <div class="terminal">{terminal}</div>
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
  --bg: #000000;
  --panel: #080912;
  --panel-2: #05060b;
  --line: rgba(255, 255, 255, 0.15);
  --text: #ffffff;
  --title: #ffffff;
  --green: #00ff88;
  --red: #ff2a55;
  --cyan: #00e5ff;
  --purple: #a67cff;
  --muted: #cbd5e1;
  --warn: #ffb74d;
}

/* Force solid black background on the entire Gradio app and components */
html, body, .gradio-container, .main-container, .gradio-container-3-50-2 {
  background-color: #000000 !important;
  background: #000000 !important;
  font-family: 'Outfit', sans-serif;
  color: #ffffff !important;
  max-width: 100% !important;
  padding: 0 !important;
  margin: 0 !important;
}

.terminal-shell {
  max-width: 100%;
  margin: 0 auto;
  padding: 24px 48px;
}

/* Premium Header Bar */
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  padding: 24px 32px;
  background: #080912 !important;
  border: 2px solid rgba(255, 255, 255, 0.15) !important;
  border-radius: 20px;
  box-shadow: 0 10px 40px rgba(0,0,0,0.7);
  border-bottom: 5px solid #00e5ff !important;
}

.brand {
  display: flex;
  gap: 20px;
  align-items: center;
}

.bolt {
  width: 56px;
  height: 56px;
  background: linear-gradient(135deg, var(--purple), var(--cyan));
  border-radius: 16px;
  display: grid;
  place-items: center;
  font-size: 28px;
  font-weight: 900;
  color: white;
  box-shadow: 0 0 20px rgba(0,229,255,0.4);
}

.brand-name {
  font-size: 28px;
  font-weight: 900;
  letter-spacing: .02em;
  color: #ffffff !important;
  text-shadow: 0 2px 10px rgba(0,0,0,0.5);
}

.badges {
  display: flex;
  gap: 10px;
  margin-top: 8px;
  flex-wrap: wrap;
}

.badges span {
  border-radius: 8px;
  padding: 4px 10px;
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.badges span:nth-child(1) {
  background: rgba(166, 124, 255, 0.15) !important;
  border: 1px solid #a67cff !important;
  color: #a67cff !important;
}
.badges span:nth-child(2) {
  background: rgba(0, 229, 255, 0.15) !important;
  border: 1px solid #00e5ff !important;
  color: #00e5ff !important;
}
.badges span:nth-child(3) {
  background: rgba(0, 255, 136, 0.15) !important;
  border: 1px solid #00ff88 !important;
  color: #00ff88 !important;
}
.badges span:nth-child(4) {
  background: rgba(255, 183, 77, 0.15) !important;
  border: 1px solid #ffb74d !important;
  color: #ffb74d !important;
}

.status-pill {
  padding: 12px 28px;
  border-radius: 999px;
  font-size: 15px;
  font-weight: 900;
  border: 2px solid;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: white;
}

.status-pill.ok {
  border-color: var(--green) !important;
  background: rgba(0,255,136,0.15) !important;
  box-shadow: 0 0 20px rgba(0,255,136,0.3) inset, 0 0 15px rgba(0,255,136,0.3) !important;
}

.status-pill.warn {
  border-color: var(--warn) !important;
  background: rgba(255,183,77,0.15) !important;
  box-shadow: 0 0 20px rgba(255,183,77,0.3) inset, 0 0 15px rgba(255,183,77,0.3) !important;
}

/* Bitcoin Macro Shield Bar */
.shield-bar {
  background: #080912 !important;
  border: 2px solid rgba(255, 255, 255, 0.1) !important;
  border-radius: 12px;
  margin: 20px 0;
  padding: 14px 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  box-shadow: 0 5px 20px rgba(0,0,0,0.6);
}

.shield-active {
  border: 2px solid #ff2a55 !important;
  background: rgba(255, 42, 85, 0.1) !important;
  box-shadow: 0 0 25px rgba(255, 42, 85, 0.3) !important;
}

.shield-label {
  font-size: 12px;
  font-weight: 900;
  color: #ffffff !important;
  letter-spacing: 0.1em;
}

.shield-active .shield-label {
  color: #ff2a55 !important;
  text-shadow: 0 0 8px rgba(255, 42, 85, 0.4);
}

.shield-status {
  font-size: 14px;
  font-weight: 900;
  color: #00e5ff !important;
  text-shadow: 0 0 8px rgba(0, 229, 255, 0.4);
}

.shield-active .shield-status {
  color: #ff2a55 !important;
  text-shadow: 0 0 8px rgba(255, 42, 85, 0.4);
}

/* Grid layout rules */
.grid {
  display: grid;
  gap: 24px;
}
.hero-grid { grid-template-columns: 1fr 1fr 1.2fr; }
.stat-grid { grid-template-columns: repeat(4,1fr); margin-top: 24px; }
.main-grid { grid-template-columns: 2fr 1fr; margin-top: 24px; }
.lower-grid { grid-template-columns: 1.3fr 1fr; margin-top: 24px; }

/* Premium Card and Stat Card styling */
.card, .stat-card {
  background: #080912 !important;
  border: 2px solid rgba(255, 255, 255, 0.15) !important;
  border-radius: 20px !important;
  padding: 28px;
  box-shadow: 0 10px 30px rgba(0,0,0,0.6) !important;
  transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1), box-shadow 0.25s, border-color 0.25s;
  position: relative;
  overflow: hidden;
}

.card::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 5px;
  background: linear-gradient(90deg, var(--purple), var(--cyan));
}

.card:hover, .stat-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 15px 35px rgba(0, 229, 255, 0.18) !important;
  border-color: rgba(0, 229, 255, 0.4) !important;
}

/* Headings and labels styling */
.label {
  color: #00e5ff !important;
  font-size: 13px !important;
  font-weight: 900 !important;
  letter-spacing: .12em !important;
  text-transform: uppercase !important;
  text-shadow: 0 0 8px rgba(0, 229, 255, 0.3);
  margin-bottom: 12px;
}

.big {
  font-size: 42px !important;
  font-weight: 900 !important;
  margin-top: 16px;
  color: #ffffff !important;
  text-shadow: 0 2px 10px rgba(0,0,0,0.5);
}

.sub {
  color: #cbd5e1 !important;
  font-size: 14px;
  margin-top: 10px;
  font-weight: 500;
  opacity: 0.95;
}

.mini {
  margin-top: 16px;
  font-size: 14px;
  font-weight: 900;
  color: white;
}

.kv {
  display: flex;
  justify-content: space-between;
  margin-top: 14px;
  font-size: 14px;
  font-weight: 800;
  color: white;
  border-bottom: 1px dashed var(--line);
  padding-bottom: 8px;
}

.kv b {
  color: var(--cyan);
}

.strategy-card .kv span {
  color: #ffffff !important;
  font-size: 13px;
}

/* Stat Cards accent customization */
.stat-card {
  min-height: 120px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  border-left: 5px solid rgba(255, 255, 255, 0.2) !important;
}

.stat-card div {
  font-size: 13px;
  font-weight: 900;
  margin-bottom: 12px;
  color: #ffffff !important;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.stat-card strong {
  display: block;
  font-size: 34px !important;
  font-weight: 900 !important;
  color: white;
}

.stat-card small {
  color: #cbd5e1 !important;
  display: block;
  margin-top: 8px;
  font-size: 12px;
  font-weight: 700;
}

.stat-card.accent-a { border-left: 5px solid #00e5ff !important; }
.stat-card.accent-a strong { color: #00e5ff !important; text-shadow: 0 0 10px rgba(0, 229, 255, 0.4) !important; }

.stat-card.accent-b { border-left: 5px solid #a67cff !important; }
.stat-card.accent-b strong { color: #a67cff !important; text-shadow: 0 0 10px rgba(166, 124, 255, 0.4) !important; }

.stat-card.accent-c { border-left: 5px solid #ffb74d !important; }
.stat-card.accent-c strong.pos { color: #00ff88 !important; text-shadow: 0 0 10px rgba(0, 255, 136, 0.4) !important; }
.stat-card.accent-c small.neg { color: #ff2a55 !important; font-size: 14px !important; font-weight: 900 !important; text-shadow: 0 0 10px rgba(255, 42, 85, 0.4) !important; }

/* Neon Color Overrides for PnL text and positive/negative states */
.terminal-shell table td.pos,
.terminal-shell table td.pos *,
.terminal-shell .pos,
.terminal-shell .pos * {
  color: #00ff88 !important;
  text-shadow: 0 0 10px rgba(0, 255, 136, 0.3) !important;
}

.terminal-shell table td.neg,
.terminal-shell table td.neg *,
.terminal-shell .neg,
.terminal-shell .neg * {
  color: #ff2a55 !important;
  text-shadow: 0 0 10px rgba(255, 42, 85, 0.3) !important;
}

.terminal-shell .warn { color: #ffb74d !important; }
.terminal-shell .warn-sl { color: #ff3366 !important; font-weight: 900 !important; text-shadow: 0 0 8px rgba(255, 51, 102, 0.3) !important; }
.terminal-shell .tp-col { color: #00e5ff !important; font-weight: 900 !important; text-shadow: 0 0 8px rgba(0, 229, 255, 0.3) !important; }

.center { text-align: center; }
.muted { color: #cbd5e1 !important; opacity: 0.8; }

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.section-head span {
  font-size: 18px;
  font-weight: 900;
  color: #ffffff !important;
  letter-spacing: 0.05em;
  text-shadow: 0 2px 10px rgba(0,0,0,0.5);
}

.section-head b {
  color: #00ff88 !important;
  background: rgba(0, 255, 136, 0.15) !important;
  padding: 6px 16px !important;
  border-radius: 999px !important;
  font-size: 11px !important;
  font-weight: 900 !important;
  border: 1px solid rgba(0, 255, 136, 0.4) !important;
  text-transform: uppercase;
}

/* Premium Table Styling */
table {
  width: 100%;
  border-collapse: collapse;
}

th {
  padding: 16px 12px;
  border-bottom: 2px solid rgba(166, 124, 255, 0.3) !important;
  text-align: left;
  font-size: 13px !important;
  font-weight: 900 !important;
  color: #a67cff !important; /* purple header text */
  letter-spacing: 0.08em;
  text-transform: uppercase;
  background: rgba(255, 255, 255, 0.02) !important;
}

td {
  padding: 16px 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  text-align: left;
  font-size: 14px;
  font-weight: 700;
  color: white !important;
}

/* First Column of tables is Symbol and deserves a beautiful amber styling */
.terminal-shell table td:first-child {
  color: #ff9f43 !important;
  font-weight: 900 !important;
  text-shadow: 0 0 8px rgba(255, 159, 67, 0.3) !important;
}

.tag {
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
}

.tag-strat {
  background: linear-gradient(135deg, #a67cff, #7000ff) !important;
  border: 1px solid #a67cff !important;
  color: #ffffff !important;
  box-shadow: 0 0 10px rgba(166, 124, 255, 0.4) !important;
}

/* Premium Badges styling */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 8px;
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border: 2px solid !important;
}

.badge-tp { background: rgba(0, 255, 136, 0.15) !important; border-color: #00ff88 !important; color: #00ff88 !important; }
.badge-sl { background: rgba(255, 42, 85, 0.15) !important; border-color: #ff2a55 !important; color: #ff2a55 !important; }
.badge-be { background: rgba(0, 229, 255, 0.15) !important; border-color: #00e5ff !important; color: #00e5ff !important; }
.badge-ts { background: rgba(166, 124, 255, 0.15) !important; border-color: #a67cff !important; color: #a67cff !important; }
.badge-shock { background: rgba(255, 183, 77, 0.15) !important; border-color: #ffb74d !important; color: #ffb74d !important; }
.badge-stale { background: rgba(255, 255, 255, 0.1) !important; border-color: #cbd5e1 !important; color: #cbd5e1 !important; }
.badge-open { background: rgba(59, 130, 246, 0.15) !important; border-color: #3b82f6 !important; color: #3b82f6 !important; }

/* Lifecycle legend item layouts */
.lifecycle-legend {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.lifecycle-legend div {
  padding: 14px 18px !important;
  background: #0d0f1a !important;
  border-radius: 12px !important;
  border: 2px solid rgba(255, 255, 255, 0.1) !important;
  color: #ffffff !important;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 16px;
}

.lifecycle-legend div span {
  color: #ffffff !important;
}

.bar {
  height: 12px;
  background: #1e243b;
  border-radius: 999px;
  overflow: hidden;
  margin: 18px 0 24px;
}

.bar span {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, var(--green), var(--cyan));
  box-shadow: 0 0 10px rgba(0, 255, 136, 0.5);
}

.perf-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.perf-grid small {
  color: #cbd5e1 !important;
  font-weight: 800;
}

.perf-grid strong {
  display: block;
  font-size: 26px;
  margin-top: 6px;
  color: white !important;
}

/* Premium Terminal Output */
.terminal {
  background: #000000 !important;
  border-radius: 16px;
  border: 2px solid rgba(255, 255, 255, 0.15) !important;
  padding: 20px;
  min-height: 300px;
  max-height: 450px;
  box-shadow: inset 0 5px 20px rgba(0,0,0,0.8);
  font-family: "Cascadia Mono", Consolas, monospace;
  color: #ffffff !important; /* crisply readable logs */
  font-size: 13px;
  line-height: 1.8;
  overflow-y: auto;
}

.term-prefix {
  color: #00ff88 !important;
  font-weight: 900;
  text-shadow: 0 0 8px rgba(0,255,136,0.4);
  margin-right: 6px;
}

/* Gradio Controls and Buttons styling */
.control-row {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}

.control-row button {
  font-family: 'Outfit', sans-serif !important;
  font-weight: 900 !important;
  font-size: 13px !important;
  letter-spacing: 0.07em !important;
  text-transform: uppercase !important;
  border-radius: 12px !important;
  padding: 14px 24px !important;
  border: 2px solid transparent !important;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
  color: #ffffff !important;
  cursor: pointer !important;
}

.btn-start {
  background: rgba(0, 255, 136, 0.15) !important;
  border-color: #00ff88 !important;
  box-shadow: 0 0 15px rgba(0, 255, 136, 0.2) !important;
}
.btn-start:hover {
  background: #00ff88 !important;
  color: #000000 !important;
  box-shadow: 0 0 25px rgba(0, 255, 136, 0.5) !important;
}

.btn-stop {
  background: rgba(255, 42, 85, 0.15) !important;
  border-color: #ff2a55 !important;
  box-shadow: 0 0 15px rgba(255, 42, 85, 0.2) !important;
}
.btn-stop:hover {
  background: #ff2a55 !important;
  color: #ffffff !important;
  box-shadow: 0 0 25px rgba(255, 42, 85, 0.5) !important;
}

.btn-refresh {
  background: rgba(0, 229, 255, 0.15) !important;
  border-color: #00e5ff !important;
  box-shadow: 0 0 15px rgba(0, 229, 255, 0.2) !important;
}
.btn-refresh:hover {
  background: #00e5ff !important;
  color: #000000 !important;
  box-shadow: 0 0 25px rgba(0, 229, 255, 0.5) !important;
}

.btn-shield {
  background: rgba(166, 124, 255, 0.15) !important;
  border-color: #a67cff !important;
  box-shadow: 0 0 15px rgba(166, 124, 255, 0.2) !important;
}
.btn-shield:hover {
  background: #a67cff !important;
  color: #ffffff !important;
  box-shadow: 0 0 25px rgba(166, 124, 255, 0.5) !important;
}

.btn-reset {
  background: rgba(255, 183, 77, 0.15) !important;
  border-color: #ffb74d !important;
  box-shadow: 0 0 15px rgba(255, 183, 77, 0.2) !important;
}
.btn-reset:hover {
  background: #ffb74d !important;
  color: #000000 !important;
  box-shadow: 0 0 25px rgba(255, 183, 77, 0.5) !important;
}

@media (max-width:1100px) {
  .hero-grid, .main-grid, .lower-grid { grid-template-columns: 1fr; }
  .stat-grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width:700px) {
  .stat-grid { grid-template-columns: 1fr; }
  table { min-width: 700px; }
  .positions-card { overflow-x: auto; }
}
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
