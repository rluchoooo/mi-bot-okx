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

@import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700;900&family=JetBrains+Mono:wght@400;700&display=swap');

:root {
  --bg-main: #0B0E14;
  --bg-card: #151924;
  --border-color: #2A2E39;
  --text-main: #FFFFFF;
  --text-muted: #8B98A5;
  --color-up: #00B894; /* Binance green */
  --color-down: #FF4757; /* Binance red */
  --color-accent: #0984E3;
}

*, *::before, *::after { box-sizing: border-box; }

body, .gradio-container, .main-container {
  background-color: var(--bg-main) !important;
  color: var(--text-main) !important;
  font-family: 'Roboto', sans-serif !important;
  margin: 0 !important;
  padding: 0 !important;
}

.terminal-shell {
  max-width: 1600px;
  margin: 0 auto;
  padding: 20px;
  background-color: var(--bg-main);
}

.topbar, .glass-topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background-color: var(--bg-card) !important;
  padding: 15px 25px !important;
  border-radius: 8px !important;
  border: 1px solid var(--border-color) !important;
  margin-bottom: 20px !important;
}

.brand-text h1, .brand-name {
  font-size: 24px !important;
  font-weight: 900 !important;
  color: var(--text-main) !important;
  margin: 0 !important;
  letter-spacing: 1px !important;
}

.glass-pill, .status-pill {
  padding: 8px 16px !important;
  border-radius: 4px !important;
  font-weight: bold !important;
  font-size: 14px !important;
}
.glass-pill.ok, .status-pill.ok { background-color: rgba(0, 184, 148, 0.1) !important; color: var(--color-up) !important; border: 1px solid var(--color-up) !important; }
.glass-pill.warn, .status-pill.warn { background-color: rgba(255, 71, 87, 0.1) !important; color: var(--color-down) !important; border: 1px solid var(--color-down) !important; }

.layout-grid, .grid {
  display: grid !important;
  gap: 20px !important;
  margin-bottom: 20px !important;
}
.hero-grid { grid-template-columns: repeat(3, 1fr) !important; }
.stat-grid { grid-template-columns: repeat(3, 1fr) !important; }

.glass-card, .card, .stat-card {
  background-color: var(--bg-card) !important;
  border: 1px solid var(--border-color) !important;
  border-radius: 8px !important;
  padding: 20px !important;
}

.card-header, .label {
  font-size: 13px !important;
  font-weight: 700 !important;
  color: var(--text-muted) !important;
  text-transform: uppercase !important;
  margin-bottom: 10px !important;
  letter-spacing: 1px !important;
}

.balance-value, .stat-big, .big {
  font-size: 32px !important;
  font-weight: 900 !important;
  color: var(--text-main) !important;
  font-family: 'JetBrains Mono', monospace !important;
}

.live-pnl-value, .stat-med {
  font-size: 24px !important;
  font-weight: 900 !important;
  font-family: 'JetBrains Mono', monospace !important;
}

.pos { color: var(--color-up) !important; }
.neg { color: var(--color-down) !important; }
.muted { color: var(--text-muted) !important; }
.highlight { color: var(--color-accent) !important; }

.table-wrapper {
  overflow-x: auto;
}

table {
  width: 100% !important;
  border-collapse: collapse !important;
}

th {
  text-align: left !important;
  padding: 12px 15px !important;
  font-size: 12px !important;
  font-weight: 700 !important;
  color: var(--text-muted) !important;
  border-bottom: 2px solid var(--border-color) !important;
  text-transform: uppercase !important;
}

td {
  padding: 15px !important;
  font-size: 14px !important;
  font-weight: 500 !important;
  border-bottom: 1px solid var(--border-color) !important;
  color: var(--text-main) !important;
}

tr:hover td {
  background-color: rgba(255, 255, 255, 0.02) !important;
}

.badge-monitor, .badge-live, .badge-trailing, .badge-neutral, .badge-breakeven {
  padding: 4px 8px !important;
  border-radius: 4px !important;
  font-size: 11px !important;
  font-weight: bold !important;
  display: inline-block !important;
  margin-right: 5px !important;
}
.badge-live { background-color: rgba(0, 184, 148, 0.1) !important; color: var(--color-up) !important; border: 1px solid var(--color-up) !important; }
.badge-trailing { background-color: rgba(255, 165, 0, 0.1) !important; color: #FFA500 !important; border: 1px solid #FFA500 !important; }
.badge-neutral { background-color: rgba(255, 255, 255, 0.1) !important; color: var(--text-muted) !important; border: 1px solid var(--text-muted) !important; }
.badge-breakeven { background-color: rgba(9, 132, 227, 0.1) !important; color: var(--color-accent) !important; border: 1px solid var(--color-accent) !important; }

.td-sym { font-weight: 900 !important; font-size: 16px !important; color: #ffffff !important; }
.td-side { font-weight: 900 !important; font-size: 14px !important; }
.pnl-active { font-family: 'JetBrains Mono', monospace !important; font-size: 18px !important; font-weight: 900 !important; }

.sltp-row { font-size: 12px !important; color: var(--text-muted) !important; margin-top: 5px !important; font-family: 'JetBrains Mono', monospace !important; }
.sl-val { color: var(--color-down) !important; font-weight: bold !important; }
.tp1-val { color: var(--color-up) !important; font-weight: bold !important; }
.tp2-val { color: var(--color-accent) !important; font-weight: bold !important; }

.terminal-window, .terminal {
  background-color: #000000 !important;
  padding: 15px !important;
  border-radius: 4px !important;
  font-family: 'JetBrains Mono', monospace !important;
  font-size: 12px !important;
  color: #00FF00 !important;
  max-height: 300px !important;
  overflow-y: auto !important;
}
.term-prefix { color: #0984E3 !important; font-weight: bold !important; }
.term-content, .term-text { color: #00FF00 !important; }

.control-row { position: absolute !important; top: 20px !important; right: 20px !important; z-index: 100 !important; display: flex !important; gap: 10px !important; }
.control-row button {
  background-color: var(--bg-card) !important;
  color: #fff !important;
  border: 1px solid var(--border-color) !important;
  font-weight: bold !important;
  padding: 8px 16px !important;
  border-radius: 4px !important;
}
.control-row button:hover { background-color: #2A2E39 !important; }

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
