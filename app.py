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
        
    running_badge = "QUANTUM ACTIVE" if runtime.running else "STOPPED"
    status_cls    = "ok" if runtime.running else "warn"
    shield_label  = _esc(runtime.shield.status_label)
    shield_cls    = "" if "LIBRE" in shield_label else "shield-active"

    # ── Position rows ──
    pos_rows = ""
    for t in open_trades:
        raw_strat = t.strategy.value if hasattr(t.strategy, "value") else str(t.strategy)
        raw_strat = raw_strat.replace("Strategy.", "")
        strat_lbl = STRATEGY_SHORT.get(raw_strat, raw_strat)
        side_cls  = "pos" if (t.side.value if hasattr(t.side, "value") else t.side) == "long" else "neg"
        side_lbl  = (t.side.value if hasattr(t.side, "value") else t.side).upper()
        sym       = _esc(t.symbol.replace("-USDT-SWAP", "USDT"))
        
        status_val = (t.status.value if hasattr(t.status, "value") else str(t.status)).upper()
        if status_val == "OPEN" or status_val == "BREAKEVEN" or status_val == "TRAILING":
            status_html = '<span class="badge-monitor">MONITOR</span>'
        else:
            status_html = f'<span class="muted">{status_val}</span>'

        # Build badges
        badges = []
        badges.append('<span class="badge-live">⚡ LIVE</span>')
        
        if getattr(t, "trailing_active", 0) == 1 or status_val == "TRAILING":
            badges.append('<span class="badge-trailing">🎯 TRAILING</span>')
        elif getattr(t, "tp2_filled", 0) == 1:
            badges.append('<span class="badge-trailing">🎯 TRAILING</span>')
        elif getattr(t, "tp1_filled", 0) == 1:
            badges.append('<span class="badge-neutral">Seeking TP2</span>')
        else:
            badges.append('<span class="badge-neutral">Seeking TP1</span>')
            
        if getattr(t, "profit_lock_active", 0) == 1 or status_val == "BREAKEVEN":
            badges.append('<span class="badge-breakeven">🛡️ BREAKEVEN</span>')

        badge_html = "".join(badges)
        
        sl_text  = _fmt(t.sl_price)  if t.sl_price  else "N/A"
        tp1_text = _fmt(t.tp1_price) if getattr(t, "tp1_price", None) else (_fmt(t.tp_price) if t.tp_price else "N/A")
        tp2_text = _fmt(t.tp2_price) if getattr(t, "tp2_price", None) else "N/A"
        sltp_html = (
            f'<div class="sltp-row">'
            f'SL <span class="sl-val">{sl_text}</span> '
            f'| TP1 <span class="tp1-val">{tp1_text}</span> '
            f'| TP2 <span class="tp2-val">{tp2_text}</span></div>'
        )
        
        shield_html = f'<div class="flex-col-center"><div class="flex-row-center" style="gap:6px;">{badge_html}</div>{sltp_html}</div>'

        live_upl = 0.0
        pos = {} 
        t_side = "long" 
        if hasattr(runtime, "last_positions") and runtime.last_positions:
            if hasattr(t.side, "value"): t_side = t.side.value.lower()
            elif isinstance(t.side, str) and "." in t.side: t_side = t.side.split(".")[-1].lower()
            else: t_side = str(t.side).lower()

            lp = runtime.last_positions
            for sym_key in [
                t.symbol, t.symbol.replace("-USDT-SWAP", "USDT"),
                t.symbol.replace("-SWAP", ""), t.symbol.replace("-USDT-SWAP", "-USDT"),
            ]:
                candidate = lp.get((sym_key, t_side), {})
                if candidate:
                    pos = candidate
                    break

            if pos:
                try:
                    upl_raw = pos.get("upl", "") or "0"
                    live_upl = float(upl_raw) if upl_raw else 0.0
                except (ValueError, TypeError): pass

        upl_val = live_upl
        sign    = "+" if upl_val >= 0 else ""
        pnl_col = _pnl_color(upl_val)
        if not pos:
            pnl_lbl = f'<span class="pnl-muted">+0.0000</span>'
        else:
            pnl_lbl = f'<span class="pnl-active" style="color: {pnl_col} !important; text-shadow: 0 0 8px {pnl_col}60;">{sign}{upl_val:.4f}</span>'

        pos_rows += f"""
<tr class="table-row">
  <td class="td-sym">{sym}</td>
  <td class="td-strat">{strat_lbl}</td>
  <td class="{side_cls} td-side">{side_lbl}</td>
  <td class="td-entry">{_fmt(t.entry_price)}</td>
  <td>{shield_html}</td>
  <td>{pnl_lbl}</td>
  <td>{status_html}</td>
</tr>"""

    if not pos_rows:
        pos_rows = "<tr><td colspan='7' class='muted center td-empty'>No active positions.</td></tr>"

    # ── Closed trade rows ──
    trade_rows = ""
    for t in closed_trades:
        pnl   = t.realized_pnl or 0
        sym   = _esc(t.symbol.replace("-USDT-SWAP", "USDT"))
        side_cls = "pos" if (t.side.value if hasattr(t.side, "value") else t.side) == "long" else "neg"
        side_lbl = (t.side.value if hasattr(t.side, "value") else t.side).upper()
        sign  = "+" if pnl >= 0 else ""
        
        reason_raw = (t.close_reason or "").upper()
        if "TP2" in reason_raw: reason_html = '<span class="reason-tp2">TAKE PROFIT 2</span>'
        elif "TP1" in reason_raw: reason_html = '<span class="reason-tp1">TAKE PROFIT 1</span>'
        elif "TAKE_PROFIT" in reason_raw: reason_html = '<span class="reason-tp">TAKE PROFIT</span>'
        elif "TRAILING" in reason_raw: reason_html = '<span class="reason-trail">TRAILING STOP</span>'
        elif "BREAKEVEN" in reason_raw: reason_html = '<span class="reason-be">BREAKEVEN</span>'
        elif "STOP_LOSS" in reason_raw or "SL" in reason_raw:
            if pnl >= 0: reason_html = '<span class="reason-be">BREAKEVEN</span>'
            else: reason_html = '<span class="reason-sl">STOP LOSS</span>'
        else: reason_html = f'<span class="muted" style="font-size: 11px;">{_esc(reason_raw)}</span>'
            
        raw_strat = t.strategy.value if hasattr(t.strategy, "value") else str(t.strategy)
        raw_strat = raw_strat.replace("Strategy.", "")
        strat  = STRATEGY_SHORT.get(raw_strat, raw_strat)
        pnl_col = _pnl_color(pnl)
        trade_rows += f"""
<tr class="table-row">
  <td class="td-sym">{sym}</td>
  <td class="{side_cls} td-side">{side_lbl}</td>
  <td class="td-strat">{strat}</td>
  <td class="td-entry">{_fmt(t.entry_price)}</td>
  <td class="td-entry">{_fmt(t.close_price or 0)}</td>
  <td>{reason_html}</td>
  <td class="pnl-active" style="color: {pnl_col} !important; text-shadow: 0 0 8px {pnl_col}60;">{sign}{pnl:.4f}</td>
</tr>"""

    if not trade_rows:
        trade_rows = "<tr><td colspan='7' class='muted center td-empty'>No closed operations.</td></tr>"

    terminal = "".join(
        f"<div><span class='term-prefix'>[QUANTUM]</span> <span class='term-text'>{_esc(l)}</span></div>" for l in reversed(logs)
    ) or "<div><span class='term-prefix'>[SYSTEM]</span> Terminal ready. Awaiting signals...</div>"

    win_bar_pct = min(max(win_rate, 3), 100)
    avg_win = stats.get("avg_win", 0)
    avg_loss = stats.get("avg_loss", 0)
    edge = (win_rate / 100 * avg_win) - ((100 - win_rate) / 100 * avg_loss)
    edge_cls = "pos" if edge >= 0 else "neg"

    return f"""
<div class="glass-shell">
  <!-- Top Bar -->
  <div class="glass-topbar">
    <div class="brand-group">
      <div class="brand-logo">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M13 2L3 14H12L11 22L21 10H12L13 2Z" fill="url(#paint0_linear)"/>
          <defs>
            <linearGradient id="paint0_linear" x1="12" y1="2" x2="12" y2="22" gradientUnits="userSpaceOnUse">
              <stop stop-color="#38BDF8"/>
              <stop offset="1" stop-color="#818CF8"/>
            </linearGradient>
          </defs>
        </svg>
      </div>
      <div class="brand-text">
        <h1>OKX QUANTUM <span class="divider">/</span> <span class="light">ELITE</span></h1>
        <div class="brand-badges">
          <span class="badge-outline">V10 PRO</span>
          <span class="badge-live-mode">LIVE</span>
        </div>
      </div>
    </div>
    <div class="status-indicator">
      <div class="glass-pill {status_cls}">
        <span class="pulse-dot"></span>
        {running_badge}
      </div>
    </div>
  </div>

  <!-- Row 1: Hero Cards -->
  <div class="layout-grid hero-grid">
    <section class="glass-card balance-card">
      <div class="card-header">AVAILABLE BALANCE</div>
      <div class="balance-value">{runtime.current_exchange_balance:,.2f} <span class="currency">USDT</span></div>
      <div class="balance-sub">Total Assets</div>
      <div class="live-pnl-box">
        <span class="live-pnl-label">LIVE PNL</span>
        <span class="live-pnl-value {_pnl_cls(live_upl_total)}">{'+' if live_upl_total>=0 else ''}{live_upl_total:.2f} USDT</span>
      </div>
    </section>

    <section class="glass-card bias-card">
      <div class="card-header">MARKET BIAS</div>
      <div class="bias-content">
        <div class="bias-icon {'pos' if 'BULL' in shield_label else 'neg' if 'BEAR' in shield_label else 'neu'}">
          {'↗' if 'BULL' in shield_label else '↘' if 'BEAR' in shield_label else '→'}
        </div>
        <div class="bias-text {'pos' if 'BULL' in shield_label else 'neg' if 'BEAR' in shield_label else 'neu'}">
          {'LONG' if 'BULL' in shield_label else 'SHORT' if 'BEAR' in shield_label else 'NEUTRAL'}
        </div>
      </div>
      <div class="bias-sub">
        BTC FILTER: {'BULLISH' if 'BULL' in shield_label else 'BEARISH' if 'BEAR' in shield_label else 'DISABLED'}
      </div>
    </section>

    <section class="glass-card param-card">
      <div class="card-header right">QUANTUM PARAMETERS</div>
      <div class="param-list">
        <div class="param-item"><span>LEVERAGE</span><b class="highlight">10X</b></div>
        <div class="param-item"><span>AMOUNT</span><b>$8 USDT</b></div>
        <div class="param-item"><span>LATENCY</span><b>15 MS</b></div>
        <div class="param-item"><span>MIN VOL</span><b>$500K</b></div>
        <div class="param-item"><span>ATR RISK</span><b>2.0 / 4.0</b></div>
        <div class="param-item"><span>PROTECT</span><b>BE 30% / TS 50%</b></div>
      </div>
    </section>
  </div>

  <!-- Row 2: Stats -->
  <div class="layout-grid stat-grid">
    <section class="glass-card stat-accent-red">
      <div class="card-header">DAILY PNL</div>
      <div class="stat-big {_pnl_cls(pnl_today)}">{'+' if pnl_today>=0 else ''}{pnl_today:.2f}</div>
      <div class="stat-footer">
        W: {stats.get('wins_count', 0)} <span class="divider">|</span> L: {stats.get('losses_count', 0)} <span class="divider">|</span> TRADES: {total_trades}
      </div>
    </section>

    <section class="glass-card dual-stat">
      <div class="stat-half">
        <div class="card-header">AVG WIN</div>
        <div class="stat-med pos">+${avg_win:.2f}</div>
      </div>
      <div class="stat-divider"></div>
      <div class="stat-half">
        <div class="card-header">AVG LOSS</div>
        <div class="stat-med neg">-${avg_loss:.2f}</div>
      </div>
    </section>

    <section class="glass-card stat-accent-blue right">
      <div class="card-header right">MATHEMATICAL EDGE</div>
      <div class="stat-big {edge_cls}">{'+' if edge>=0 else ''}${edge:.2f}</div>
      <div class="stat-footer">EXPECTED VALUE PER TRADE</div>
    </section>
  </div>

  <!-- Row 3: Active Positions -->
  <div class="layout-grid main-grid">
    <section class="glass-card table-card active-card">
      <div class="table-header">
        <div class="table-title"><span class="icon">⚡</span> ACTIVE POSITIONS</div>
        <div class="table-badge">{len(open_trades)} LIVE</div>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>SYMBOL</th>
              <th>STRATEGY</th>
              <th>SIDE</th>
              <th>ENTRY</th>
              <th>MANAGEMENT</th>
              <th>LIVE PNL</th>
              <th>STATUS</th>
            </tr>
          </thead>
          <tbody>{pos_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="glass-card perf-card">
      <div class="table-header"><div class="table-title"><span class="icon">📈</span> GLOBAL PERFORMANCE</div></div>
      <div class="win-rate-section">
        <div class="wr-header">
          <span>WIN RATE</span>
          <strong>{win_rate:.1f}%</strong>
        </div>
        <div class="progress-bg"><div class="progress-fill" style="width:{win_bar_pct:.1f}%"></div></div>
      </div>
      <div class="metrics-grid">
        <div class="metric-box">
          <span>PROFIT FACTOR</span>
          <strong>{pf:.2f}</strong>
        </div>
        <div class="metric-box">
          <span>RISK/REWARD</span>
          <strong>1:{(avg_win/avg_loss if avg_loss > 0 else 0):.1f}</strong>
        </div>
        <div class="metric-box">
          <span>BEST TRADE</span>
          <strong class="pos">+{best:.2f}</strong>
        </div>
        <div class="metric-box">
          <span>WORST TRADE</span>
          <strong class="neg">{worst:.2f}</strong>
        </div>
      </div>
    </section>
  </div>

  <!-- Row 4: History & Terminal -->
  <div class="layout-grid lower-grid">
    <section class="glass-card table-card history-card">
      <div class="table-header">
        <div class="table-title"><span class="icon">📜</span> TRADE HISTORY</div>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>SYMBOL</th>
              <th>SIDE</th>
              <th>STRATEGY</th>
              <th>ENTRY</th>
              <th>EXIT</th>
              <th>REASON</th>
              <th>PNL</th>
            </tr>
          </thead>
          <tbody>{trade_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="glass-card term-card">
      <div class="table-header">
        <div class="table-title"><span class="icon">💻</span> SYSTEM TERMINAL</div>
      </div>
      <div class="terminal-window">
        <div class="term-content">{terminal}</div>
      </div>
    </section>
  </div>
</div>
"""

# ──────────────────────────────────────────────────────────────────────
# CSS
# ──────────────────────────────────────────────────────────────────────

APP_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;700&display=swap');

:root {
  --bg-color: #020617; /* Slate 950 */
  --bg-gradient: radial-gradient(circle at 50% 0%, #1e1b4b, #020617 70%);
  --glass-bg: rgba(15, 23, 42, 0.4); /* Slate 900 translucent */
  --glass-border: rgba(255, 255, 255, 0.08);
  --glass-highlight: rgba(255, 255, 255, 0.12);
  --text-main: #f8fafc;
  --text-muted: #94a3b8;
  --accent-cyan: #38bdf8;
  --accent-purple: #818cf8;
  --pos-color: #34d399; /* Emerald 400 */
  --neg-color: #fb7185; /* Rose 400 */
  --warn-color: #fbbf24;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html, body, .gradio-container, .main-container, .gradio-container-3-50-2 {
  background-color: var(--bg-color) !important;
  background-image: var(--bg-gradient) !important;
  background-attachment: fixed !important;
  font-family: 'Inter', sans-serif !important;
  color: var(--text-main) !important;
  max-width: 100% !important;
  padding: 0 !important;
  margin: 0 !important;
}

/* Utilities */
.pos { color: var(--pos-color) !important; }
.neg { color: var(--neg-color) !important; }
.neu { color: var(--accent-cyan) !important; }
.highlight { color: var(--accent-cyan) !important; }
.muted { color: var(--text-muted) !important; }
.center { text-align: center; }
.right { text-align: right; }

.flex-col-center { display: flex; flex-direction: column; justify-content: center; }
.flex-row-center { display: flex; align-items: center; }

/* Main Shell */
.glass-shell {
  max-width: 1600px;
  margin: 0 auto;
  padding: 40px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

/* Topbar */
.glass-topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 32px;
  background: var(--glass-bg);
  border: 1px solid var(--glass-border);
  border-radius: 20px;
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  box-shadow: 0 4px 30px rgba(0, 0, 0, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.05);
}

.brand-group { display: flex; gap: 20px; align-items: center; }
.brand-logo { 
  width: 44px; height: 44px; border-radius: 12px;
  background: rgba(255,255,255,0.03); border: 1px solid var(--glass-border);
  display: grid; place-items: center; box-shadow: 0 4px 15px rgba(0,0,0,0.3);
}
.brand-text h1 { 
  font-size: 20px; font-weight: 800; letter-spacing: 0.05em; margin-bottom: 4px; 
  background: linear-gradient(to right, #fff, #94a3b8); -webkit-background-clip: text; color: transparent;
}
.brand-text .light { font-weight: 300; }
.brand-text .divider { opacity: 0.3; margin: 0 4px; }
.brand-badges { display: flex; gap: 8px; }
.badge-outline { 
  font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 6px; 
  border: 1px solid var(--glass-border); color: var(--text-muted); letter-spacing: 0.05em;
}
.badge-live-mode {
  font-size: 10px; font-weight: 800; padding: 2px 8px; border-radius: 6px;
  background: rgba(52, 211, 153, 0.1); border: 1px solid rgba(52, 211, 153, 0.2); color: var(--pos-color);
}

.glass-pill {
  padding: 10px 24px; border-radius: 12px; font-size: 13px; font-weight: 700; letter-spacing: 0.05em;
  display: flex; align-items: center; gap: 10px; border: 1px solid var(--glass-border);
  background: rgba(0,0,0,0.2); backdrop-filter: blur(10px);
}
.glass-pill.ok { color: var(--pos-color); border-color: rgba(52, 211, 153, 0.3); box-shadow: 0 0 20px rgba(52, 211, 153, 0.1) inset; }
.glass-pill.warn { color: var(--neg-color); border-color: rgba(251, 113, 133, 0.3); box-shadow: 0 0 20px rgba(251, 113, 133, 0.1) inset; }
.pulse-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; box-shadow: 0 0 8px currentColor; animation: pulse 2s infinite; }
@keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.4; } 100% { opacity: 1; } }

/* Grids */
.layout-grid { display: grid; gap: 24px; }
.hero-grid { grid-template-columns: 1fr 1.2fr 1.5fr; }
.stat-grid { grid-template-columns: 1fr 1.5fr 1fr; }
.main-grid { grid-template-columns: 2.2fr 1fr; }
.lower-grid { grid-template-columns: 1.8fr 1.2fr; }

/* Cards */
.glass-card {
  background: var(--glass-bg);
  border: 1px solid var(--glass-border);
  border-radius: 20px;
  padding: 28px;
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  box-shadow: 0 10px 30px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.05);
  transition: transform 0.3s ease, border-color 0.3s ease;
  position: relative;
  overflow: hidden;
}
.glass-card:hover { transform: translateY(-2px); border-color: var(--glass-highlight); }
.card-header { font-size: 11px; font-weight: 700; color: var(--text-muted); letter-spacing: 0.1em; text-transform: uppercase; margin-bottom: 12px; }

/* Hero Specifics */
.balance-value { font-size: 38px; font-weight: 800; color: #fff; margin-top: 8px; }
.balance-value .currency { font-size: 20px; font-weight: 500; color: var(--text-muted); }
.balance-sub { font-size: 13px; color: var(--text-muted); font-weight: 500; margin-top: 4px; }
.live-pnl-box { margin-top: 24px; padding: 12px 16px; background: rgba(0,0,0,0.2); border-radius: 12px; border: 1px solid var(--glass-border); display: inline-flex; gap: 16px; align-items: center; }
.live-pnl-label { font-size: 11px; font-weight: 700; color: var(--text-muted); letter-spacing: 0.05em; }
.live-pnl-value { font-size: 18px; font-weight: 800; }

.bias-card { display: flex; flex-direction: column; align-items: center; justify-content: center; }
.bias-content { display: flex; align-items: center; gap: 16px; margin-top: 8px; }
.bias-icon { font-size: 40px; }
.bias-text { font-size: 40px; font-weight: 800; text-shadow: 0 0 20px rgba(0,0,0,0.5); }
.bias-sub { margin-top: 16px; font-size: 12px; font-weight: 700; color: var(--accent-purple); background: rgba(129, 140, 248, 0.1); padding: 4px 12px; border-radius: 8px; }

.param-list { display: flex; flex-direction: column; gap: 12px; margin-top: 16px; }
.param-item { display: flex; justify-content: space-between; align-items: center; font-size: 13px; font-weight: 600; color: var(--text-muted); border-bottom: 1px dashed rgba(255,255,255,0.05); padding-bottom: 8px; }
.param-item b { color: #fff; font-weight: 700; }

/* Stats Specifics */
.stat-accent-red { border-left: 4px solid var(--neg-color) !important; }
.stat-accent-blue { border-right: 4px solid var(--accent-cyan) !important; }
.stat-big { font-size: 42px; font-weight: 800; margin: 12px 0; line-height: 1; }
.stat-footer { font-size: 11px; font-weight: 600; color: var(--text-muted); letter-spacing: 0.05em; }
.stat-footer .divider { margin: 0 8px; opacity: 0.3; }

.dual-stat { display: flex; padding: 0 !important; }
.stat-half { flex: 1; padding: 28px; display: flex; flex-direction: column; justify-content: center; }
.stat-divider { width: 1px; background: linear-gradient(to bottom, transparent, var(--glass-border), transparent); }
.stat-med { font-size: 32px; font-weight: 800; margin-top: 8px; }

/* Tables */
.table-card { padding: 0 !important; display: flex; flex-direction: column; }
.table-header { padding: 20px 28px; border-bottom: 1px solid var(--glass-border); display: flex; justify-content: space-between; align-items: center; background: rgba(255,255,255,0.02); }
.table-title { font-size: 13px; font-weight: 700; letter-spacing: 0.05em; color: #fff; display: flex; align-items: center; gap: 8px; }
.table-title .icon { opacity: 0.8; }
.table-badge { background: rgba(255,255,255,0.05); border: 1px solid var(--glass-border); padding: 4px 12px; border-radius: 20px; font-size: 11px; font-weight: 700; }
.table-wrapper { flex: 1; overflow-x: auto; }

table { width: 100%; border-collapse: collapse; text-align: left; }
th { padding: 16px 28px; font-size: 10px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--glass-border); background: rgba(0,0,0,0.1); }
td { padding: 16px 28px; font-size: 13px; font-weight: 500; border-bottom: 1px solid rgba(255,255,255,0.02); transition: background 0.2s ease; }
tr:hover td { background: rgba(255,255,255,0.03); }

.td-sym { color: #fff; font-weight: 700; }
.td-strat { color: var(--accent-cyan); font-size: 11px; font-weight: 600; }
.td-side { font-weight: 800; }
.td-entry { color: #cbd5e1; font-family: 'JetBrains Mono', monospace; font-size: 12px; }
.td-empty { padding: 32px; font-size: 13px; color: var(--text-muted); }

/* Badges */
.badge-monitor { border: 1px solid var(--accent-purple); color: var(--accent-purple); padding: 4px 10px; border-radius: 6px; font-size: 10px; font-weight: 700; }
.badge-live { background: rgba(52, 211, 153, 0.1); border: 1px solid rgba(52, 211, 153, 0.3); color: var(--pos-color); padding: 3px 8px; border-radius: 6px; font-size: 10px; font-weight: 700; }
.badge-trailing { background: rgba(251, 191, 36, 0.1); border: 1px solid rgba(251, 191, 36, 0.3); color: var(--warn-color); padding: 3px 8px; border-radius: 6px; font-size: 10px; font-weight: 700; }
.badge-neutral { background: rgba(255, 255, 255, 0.05); border: 1px solid var(--glass-border); color: #cbd5e1; padding: 3px 8px; border-radius: 6px; font-size: 10px; font-weight: 600; }
.badge-breakeven { background: rgba(129, 140, 248, 0.1); border: 1px solid rgba(129, 140, 248, 0.3); color: var(--accent-purple); padding: 3px 8px; border-radius: 6px; font-size: 10px; font-weight: 700; }

.reason-tp2 { color: #f472b6; font-weight: 700; font-size: 11px; }
.reason-tp1 { color: var(--accent-cyan); font-weight: 700; font-size: 11px; }
.reason-tp { color: var(--pos-color); font-weight: 700; font-size: 11px; }
.reason-trail { color: var(--accent-purple); font-weight: 700; font-size: 11px; }
.reason-be { color: var(--accent-cyan); font-weight: 700; font-size: 11px; }
.reason-sl { color: var(--neg-color); font-weight: 700; font-size: 11px; }

.sltp-row { font-size: 11px; color: var(--text-muted); margin-top: 8px; font-family: 'JetBrains Mono', monospace; }
.sl-val { color: var(--neg-color); font-weight: 600; }
.tp1-val { color: var(--pos-color); font-weight: 600; }
.tp2-val { color: var(--accent-cyan); font-weight: 600; }
.pnl-active { font-family: 'JetBrains Mono', monospace; font-size: 14px; font-weight: 700; }
.pnl-muted { color: var(--text-muted); font-family: 'JetBrains Mono', monospace; font-size: 12px; }

/* Performance */
.win-rate-section { margin-top: 24px; }
.wr-header { display: flex; justify-content: space-between; margin-bottom: 12px; font-size: 12px; font-weight: 700; color: var(--text-muted); }
.wr-header strong { color: #fff; font-size: 18px; }
.progress-bg { height: 8px; background: rgba(0,0,0,0.3); border-radius: 4px; overflow: hidden; box-shadow: inset 0 1px 3px rgba(0,0,0,0.5); }
.progress-fill { height: 100%; background: linear-gradient(90deg, var(--accent-purple), var(--accent-cyan)); border-radius: 4px; box-shadow: 0 0 10px var(--accent-cyan); }

.metrics-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 32px; }
.metric-box { display: flex; flex-direction: column; gap: 8px; }
.metric-box span { font-size: 11px; font-weight: 700; color: var(--text-muted); }
.metric-box strong { font-size: 24px; font-weight: 800; color: #fff; }

/* Terminal */
.term-card { padding: 0 !important; display: flex; flex-direction: column; }
.terminal-window { flex: 1; background: rgba(0,0,0,0.4); padding: 24px; overflow-y: auto; max-height: 400px; border-bottom-left-radius: 20px; border-bottom-right-radius: 20px; }
.term-content { font-family: 'JetBrains Mono', monospace; font-size: 12px; line-height: 1.8; color: #cbd5e1; }
.term-prefix { color: var(--accent-cyan); font-weight: 700; margin-right: 12px; }
.term-text { opacity: 0.9; }

/* Controls */
.control-row { position: absolute; top: 40px; right: 50px; z-index: 100; display: flex; gap: 12px; }
.control-row button {
  background: var(--glass-bg) !important;
  border: 1px solid var(--glass-border) !important;
  color: #fff !important;
  font-family: 'Inter', sans-serif !important;
  font-size: 11px !important;
  font-weight: 600 !important;
  padding: 10px 20px !important;
  border-radius: 8px !important;
  letter-spacing: 0.05em !important;
  transition: all 0.2s ease !important;
  box-shadow: 0 4px 15px rgba(0,0,0,0.2) !important;
}
.control-row button:hover { background: rgba(255,255,255,0.05) !important; border-color: rgba(255,255,255,0.2) !important; transform: translateY(-1px) !important; box-shadow: 0 6px 20px rgba(0,0,0,0.3) !important; }
#btn-start { border-bottom: 2px solid var(--pos-color) !important; }
#btn-stop { border-bottom: 2px solid var(--neg-color) !important; }

/* Hide Gradio specifics */
footer { display: none !important; }
.gradio-container { padding: 0 !important; }
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
