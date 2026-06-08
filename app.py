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

def _fmt(v: float, decimals: int = 6) -> str:
    return f"{v:.{decimals}f}"

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
        stat_icon = STATUS_ICON.get(t.status.value if hasattr(t.status, "value") else str(t.status), "🔵")
        strat_lbl = STRATEGY_SHORT.get(t.strategy.value if hasattr(t.strategy, "value") else str(t.strategy), t.strategy)
        side_cls  = "pos" if (t.side.value if hasattr(t.side, "value") else t.side) == "long" else "neg"
        side_lbl  = (t.side.value if hasattr(t.side, "value") else t.side).upper()
        sym       = _esc(t.symbol.replace("-USDT-SWAP", "USDT"))
        sl_lbl    = _fmt(t.trail_sl or t.sl_price)
        tp_lbl    = _fmt(t.tp_price) if t.tp_price else "TRAILING"
        status_lbl = (t.status.value if hasattr(t.status, "value") else str(t.status))
        pos_rows += f"""
<tr>
  <td>{sym}</td>
  <td class="{side_cls}">{side_lbl}</td>
  <td><span class="tag tag-strat">{strat_lbl}</span></td>
  <td>{_fmt(t.entry_price)}</td>
  <td class="warn-sl">{sl_lbl}</td>
  <td class="tp-col">{tp_lbl}</td>
  <td>{stat_icon} {status_lbl}</td>
</tr>"""

    if not pos_rows:
        pos_rows = "<tr><td colspan='7' class='muted center'>Sin posiciones abiertas. Escaneando mercado...</td></tr>"

    # ── Closed trade cards ──
    trade_cards = ""
    for t in closed_trades:
        pnl   = t.realized_pnl or 0
        sym   = _esc(t.symbol.replace("-USDT-SWAP", "USDT"))
        side_cls = "pos" if (t.side.value if hasattr(t.side, "value") else t.side) == "long" else "neg"
        sign  = "+" if pnl >= 0 else ""
        reason = _esc((t.close_reason or "").upper())
        strat  = STRATEGY_SHORT.get(t.strategy.value if hasattr(t.strategy, "value") else str(t.strategy), "?")
        trade_cards += f"""
<div class="trade-card">
  <div class="coin">{sym[:2]}</div>
  <div>
    <strong>{sym} <span class="{side_cls}">{(t.side.value if hasattr(t.side,'value') else t.side).upper()}</span> <span class="tag">{strat}</span></strong>
    <small>E {_fmt(t.entry_price)} → S {_fmt(t.close_price or 0)}</small>
  </div>
  <div><small>CIERRE</small><strong>{reason}</strong></div>
  <b class="{_pnl_cls(pnl)}">{sign}{pnl:.2f} USDT</b>
</div>"""

    if not trade_cards:
        trade_cards = "<div class='empty'>Sin operaciones cerradas desde este arranque.</div>"

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
        <thead><tr><th>SÍMBOLO</th><th>LADO</th><th>ESTRAT.</th><th>ENTRADA</th><th>STOP</th><th>TAKE PROFIT</th><th>ESTADO</th></tr></thead>
        <tbody>{pos_rows}</tbody>
      </table>
    </section>
    <section class="card">
      <div class="section-head"><span>CICLO DE VIDA</span></div>
      <div class="lifecycle-legend">
        <div>🔵 <b>OPEN</b> – Buscando recorrido</div>
        <div>🛡️ <b>BREAKEVEN</b> – SL en entrada +$1.60</div>
        <div>🎯 <b>TRAILING</b> – Persiguiendo precio</div>
        <div>⚡ <b>EARLY EXIT</b> – Estructura fallida</div>
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
      <div class="trade-list">{trade_cards}</div>
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
  --bg: #090b14; --panel: #111526; --panel-2: #161b30;
  --line: rgba(255,255,255,.1); --text: #a0aec0; --title: #ffffff;
  --green: #00ff88; --red: #ff2a55; --cyan: #00e5ff;
  --purple: #a67cff; --muted: #64748b; --warn: #ffb74d;
}
body, .gradio-container { background: var(--bg) !important; font-family: 'Outfit', sans-serif; color: var(--text); max-width: 100% !important; padding: 0 !important; margin: 0 !important; }
.terminal-shell { max-width: 100%; margin: 0 auto; padding: 24px 48px; }
.topbar { display:flex; justify-content:space-between; align-items:center; margin-bottom:24px; padding: 20px 30px; background: linear-gradient(90deg, rgba(14,17,31,0.9), rgba(20,25,46,0.9)); border: 1px solid var(--line); border-radius: 20px; box-shadow: 0 10px 40px rgba(0,0,0,0.5); border-bottom: 3px solid var(--cyan); }
.brand { display:flex; gap:20px; align-items:center; }
.bolt { width:56px; height:56px; background:linear-gradient(135deg,var(--purple),var(--cyan)); border-radius:16px; display:grid; place-items:center; font-size:28px; font-weight:900; color: white; box-shadow: 0 0 20px rgba(0,229,255,0.4); }
.brand-name { font-size:28px; font-weight:900; letter-spacing:.02em; color: var(--title); text-shadow: 0 2px 10px rgba(0,0,0,0.5); }
.badges { display:flex; gap:10px; margin-top:8px; flex-wrap:wrap; }
.badges span { background:rgba(255,255,255,.1); border:1px solid rgba(255,255,255,.2); border-radius:8px; padding:4px 10px; font-size:11px; font-weight:700; color:white; text-transform: uppercase; letter-spacing: 0.05em; }
.status-pill { padding:12px 28px; border-radius:999px; font-size:15px; font-weight:900; border:2px solid; letter-spacing: 0.05em; text-transform: uppercase; color: white; }
.status-pill.ok   { border-color:var(--green); background: rgba(0,255,136,0.1); box-shadow: 0 0 20px rgba(0,255,136,0.3) inset, 0 0 15px rgba(0,255,136,0.3); }
.status-pill.warn { border-color:var(--warn);  background: rgba(255,183,77,0.1); box-shadow: 0 0 20px rgba(255,183,77,0.3) inset, 0 0 15px rgba(255,183,77,0.3); }
.shield-bar { background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px; margin: 20px 0; padding: 12px 20px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
.shield-active { border: 1px solid var(--red); background: rgba(255, 23, 68, 0.1); box-shadow: 0 0 20px rgba(255, 23, 68, 0.3); }
.shield-label { font-size: 11px; font-weight: 800; color: var(--text); letter-spacing: 0.1em; }
.shield-active .shield-label { color: var(--red); }
.shield-status { font-size: 13px; font-weight: 700; color: var(--cyan); }
.grid { display:grid; gap:24px; }
.hero-grid  { grid-template-columns: 1fr 1fr 1.2fr; }
.stat-grid  { grid-template-columns: repeat(4,1fr); margin-top:24px; }
.main-grid  { grid-template-columns: 2fr 1fr; margin-top:24px; }
.lower-grid { grid-template-columns: 1.3fr 1fr; margin-top:24px; }
.card, .stat-card {
  background: var(--panel);
  border: 1px solid var(--line); border-radius: 20px; padding: 28px;
  box-shadow: 0 10px 30px rgba(0,0,0,0.4); transition: transform 0.2s, box-shadow 0.2s;
  position: relative; overflow: hidden;
}
.card::before { content: ""; position: absolute; top:0; left:0; width:100%; height:4px; background: linear-gradient(90deg, var(--purple), var(--cyan)); opacity: 0.5; }
.card:hover, .stat-card:hover { transform: translateY(-3px); box-shadow: 0 15px 40px rgba(0,0,0,0.5); border-color: rgba(255,255,255,0.2); }
.label, th, small { color:var(--text); font-size:12px; font-weight:700; letter-spacing:.08em; text-transform: uppercase; }
.big { font-size:40px; font-weight:900; margin-top:16px; color: white; }
.sub { color:var(--muted); font-size:14px; margin-top:10px; }
.mini { margin-top:16px; font-size:14px; font-weight:900; color: white; }
.kv { display:flex; justify-content:space-between; margin-top:14px; font-size:14px; font-weight:700; color: white; border-bottom: 1px dashed var(--line); padding-bottom: 6px; }
.kv b { color:var(--cyan); }
.stat-card { min-height:120px; display:flex; flex-direction:column; justify-content:center; }
.stat-card div { font-size:13px; font-weight:800; margin-bottom:12px; color: var(--text); }
.stat-card strong { display:block; font-size:32px; font-weight:900; color: white; }
.stat-card small { color:var(--muted); display:block; margin-top:8px; font-size:12px; }
.pos { color:var(--green) !important; text-shadow: 0 0 10px rgba(0,255,136,0.3); }
.neg { color:var(--red) !important; text-shadow: 0 0 10px rgba(255,42,85,0.3); }
.warn { color:var(--warn) !important; }
.warn-sl { color:var(--red) !important; font-weight:900; }
.tp-col  { color:var(--cyan) !important; font-weight:900; }
.center { text-align:center; }
.muted  { color:var(--muted); }
.section-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; }
.section-head b { color:var(--cyan); background:rgba(0,229,255,.1); padding:6px 12px; border-radius:999px; font-size:12px; border: 1px solid rgba(0,229,255,0.3); }
table { width:100%; border-collapse:collapse; }
th, td { padding:16px 12px; border-bottom:1px solid rgba(255,255,255,.05); text-align:left; font-size:14px; font-weight:700; color: white; }
thead { background:rgba(255,255,255,0.03); }
.tag { background:rgba(255,255,255,.1); border:1px solid var(--line); border-radius:6px; padding:4px 8px; font-size:11px; font-weight:800; color: white; }
.tag-strat { color:white; background: var(--purple); border-color:var(--purple); box-shadow: 0 0 10px rgba(166,124,255,0.4); }
.lifecycle-legend { display:flex; flex-direction:column; gap:12px; font-size:14px; font-weight:700; color: white; }
.lifecycle-legend div { padding:12px 16px; background:var(--panel-2); border-radius:12px; border:1px solid var(--line); }
.bar { height:12px; background:#1e243b; border-radius:999px; overflow:hidden; margin:18px 0 24px; }
.bar span { display:block; height:100%; background:linear-gradient(90deg,var(--green),var(--cyan)); box-shadow: 0 0 10px rgba(0,255,136,0.5); }
.perf-grid { display:grid; grid-template-columns:1fr 1fr; gap:20px; }
.perf-grid strong { display:block; font-size:26px; margin-top:6px; color: white; }
.trade-list { display:flex; flex-direction:column; gap:12px; }
.trade-card {
  border:1px solid var(--line); border-radius:14px; padding:18px;
  display:grid; grid-template-columns:48px 1.4fr 1fr auto; align-items:center; gap:16px;
  background: var(--panel-2); transition: background 0.2s;
}
.trade-card:hover { background: rgba(255,255,255,0.05); }
.coin { width:40px; height:40px; border-radius:10px; display:grid; place-items:center; background:#000; color:white; font-weight:900; font-size:13px; border: 1px solid var(--line); }
.trade-card small { color:var(--text); display:block; margin-top:6px; font-size: 13px; }
.empty { color:var(--text); border:2px dashed var(--line); border-radius:14px; padding:24px; font-size:14px; font-weight:800; text-align: center; }
.terminal {
  background: #000000; border-radius:16px; border:1px solid rgba(255,255,255,0.1); padding:20px;
  min-height:300px; max-height:450px; box-shadow: inset 0 5px 20px rgba(0,0,0,0.8);
  font-family:"Cascadia Mono",Consolas,monospace; color:#00ff88; font-size:13px;
  line-height:1.8; overflow-y:auto;
}
.term-prefix { color:var(--cyan); font-weight:900; text-shadow: 0 0 8px rgba(0,229,255,0.6); }
.strategy-card .kv span { color:var(--text); font-size:13px; }
.control-row { display:flex; gap:12px; margin-bottom:20px; flex-wrap:wrap; }
.control-row button { font-family: 'Outfit', sans-serif !important; font-weight: 700 !important; letter-spacing: 0.05em !important; text-transform: uppercase !important; border-radius: 12px !important; }
@media (max-width:1100px) {
  .hero-grid, .main-grid, .lower-grid { grid-template-columns:1fr; }
  .stat-grid { grid-template-columns:1fr 1fr; }
}
@media (max-width:700px) {
  .stat-grid { grid-template-columns:1fr; }
  .trade-card { grid-template-columns:1fr; }
  table { min-width:700px; }
  .positions-card { overflow-x:auto; }
}
@media (max-width:1100px) {
  .hero-grid, .main-grid, .lower-grid { grid-template-columns:1fr; }
  .stat-grid { grid-template-columns:1fr 1fr; }
}
@media (max-width:700px) {
  .stat-grid { grid-template-columns:1fr; }
  .trade-card { grid-template-columns:1fr; }
  table { min-width:700px; }
  .positions-card { overflow-x:auto; }
}
"""


# ──────────────────────────────────────────────────────────────────────
# Gradio App
# ──────────────────────────────────────────────────────────────────────

# Auto-start bot on container boot
runtime.start()

with gr.Blocks(title="Quantum V10 Pro Terminal", css=APP_CSS, fill_width=True) as demo:
    with gr.Row(elem_classes=["control-row"]):
        start_btn   = gr.Button("▶️ Iniciar Bot",  variant="primary")
        stop_btn    = gr.Button("⏹️ Detener")
        refresh_btn = gr.Button("🔄 Actualizar")
        shield_btn  = gr.Button("🛡️ Desbloquear Escudo")
        reset_btn   = gr.Button("🗑️ Resetear Stats", variant="stop")

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
