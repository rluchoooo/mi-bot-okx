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
    "QUANTUM_V10_PRO":   "TREND",
    "QUANTUM_DIVERGENCE": "DIV",
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
        <div class="brand-name">QUANTUM V10 PRO TERMINAL</div>
        <div class="badges"><span>ELITE</span><span>DEMO EXCHANGE</span><span>DUAL STRATEGY</span><span>$8 RISK</span></div>
      </div>
    </div>
    <div class="status-pill {status_cls}">{running_badge}</div>
  </div>

  <div class="shield-bar">
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
      <div class="kv"><span>A: QUANTUM TREND V10 PRO</span><b class="pos">ON</b></div>
      <div class="kv"><span>  • Filtro 1H → EMA50 bias</span></div>
      <div class="kv"><span>  • Filtro 15M → EMA50 + RSI</span></div>
      <div class="kv"><span>  • Sniper 5M → FVG Entry</span></div>
      <div class="kv"><span>B: QUANTUM DIVERGENCE</span><b class="pos">ON</b></div>
      <div class="kv"><span>  • Divergencia RSI/Precio 15M</span></div>
      <div class="kv"><span>  • Confirmación FVG 5M</span></div>
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
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;700;900&display=swap');
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #06080d; --panel: #0a0d14; --panel-2: #0e1119;
  --line: rgba(255,255,255,.08); --text: #8892a4;
  --green: #00e676; --red: #ff1744; --cyan: #00e5ff;
  --purple: #8f5cff; --muted: #4a5568; --warn: #ffab40;
}
body, .gradio-container { background: var(--bg) !important; font-family: 'Inter', sans-serif; color: #e2e8f0; }
.terminal-shell { max-width: 1440px; margin: 0 auto; padding: 18px; }
.topbar { display:flex; justify-content:space-between; align-items:center; margin-bottom:18px; }
.brand { display:flex; gap:14px; align-items:center; }
.bolt { width:44px; height:44px; background:linear-gradient(135deg,var(--purple),var(--cyan)); border-radius:10px; display:grid; place-items:center; font-size:20px; font-weight:900; }
.brand-name { font-size:20px; font-weight:900; letter-spacing:.05em; }
.badges { display:flex; gap:6px; margin-top:4px; flex-wrap:wrap; }
.badges span { background:rgba(255,255,255,.07); border:1px solid var(--line); border-radius:4px; padding:2px 7px; font-size:10px; font-weight:700; color:var(--text); }
.status-pill { padding:8px 18px; border-radius:999px; font-size:12px; font-weight:900; border:1.5px solid; }
.status-pill.ok   { border-color:var(--green); color:var(--green); }
.status-pill.warn { border-color:var(--warn);  color:var(--warn);  }
.shield-bar { display:flex; align-items:center; gap:12px; background:var(--panel-2); border:1px solid var(--line); border-radius:10px; padding:10px 18px; margin-bottom:18px; font-size:12px; }
.shield-label { color:var(--text); font-weight:700; }
.shield-status { font-weight:900; }
.grid { display:grid; gap:18px; }
.hero-grid  { grid-template-columns: 1fr 1fr 1.2fr; }
.stat-grid  { grid-template-columns: repeat(4,1fr); margin-top:18px; }
.main-grid  { grid-template-columns: 2fr 1fr; margin-top:18px; }
.lower-grid { grid-template-columns: 1.3fr 1fr; margin-top:18px; }
.card, .stat-card {
  background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,.015)), var(--panel);
  border:1px solid var(--line); border-radius:12px; padding:22px;
}
.label, th, small { color:var(--text); font-size:11px; font-weight:800; letter-spacing:.04em; }
.big { font-size:32px; font-weight:900; margin-top:14px; }
.sub { color:var(--muted); font-size:12px; margin-top:8px; }
.mini { margin-top:14px; font-size:12px; font-weight:900; }
.kv { display:flex; justify-content:space-between; margin-top:10px; font-size:12px; font-weight:800; }
.kv b { color:var(--cyan); }
.stat-card { min-height:100px; }
.stat-card div { font-size:11px; font-weight:800; margin-bottom:10px; }
.stat-card strong { display:block; font-size:26px; font-weight:900; }
.stat-card small { color:var(--muted); display:block; margin-top:6px; }
.accent-a { border-color:rgba(143,92,255,.5); }
.accent-b { border-color:rgba(0,229,255,.4); }
.accent-c { border-color:rgba(255,255,255,.25); }
.pos { color:var(--green) !important; }
.neg { color:var(--red) !important; }
.warn { color:var(--warn) !important; }
.warn-sl { color:var(--red) !important; font-weight:900; }
.tp-col  { color:var(--cyan) !important; font-weight:900; }
.center { text-align:center; }
.muted  { color:var(--muted); }
.section-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; }
.section-head b { color:var(--cyan); background:rgba(0,229,255,.1); padding:4px 9px; border-radius:999px; font-size:10px; }
table { width:100%; border-collapse:collapse; }
th, td { padding:13px 10px; border-bottom:1px solid rgba(255,255,255,.05); text-align:left; font-size:12px; font-weight:800; }
thead { background:var(--panel-2); }
.tag { background:rgba(255,255,255,.08); border:1px solid var(--line); border-radius:4px; padding:2px 6px; font-size:10px; font-weight:800; }
.tag-strat { color:var(--purple); border-color:rgba(143,92,255,.4); }
.lifecycle-legend { display:flex; flex-direction:column; gap:10px; font-size:12px; font-weight:700; }
.lifecycle-legend div { padding:8px 12px; background:var(--panel-2); border-radius:8px; border:1px solid var(--line); }
.bar { height:10px; background:#1b1f28; border-radius:999px; overflow:hidden; margin:14px 0 20px; }
.bar span { display:block; height:100%; background:linear-gradient(90deg,var(--green),var(--cyan)); }
.perf-grid { display:grid; grid-template-columns:1fr 1fr; gap:18px; }
.perf-grid strong { display:block; font-size:22px; margin-top:4px; }
.trade-list { display:flex; flex-direction:column; gap:10px; }
.trade-card {
  border:1px solid var(--line); border-radius:10px; padding:14px;
  display:grid; grid-template-columns:40px 1.4fr 1fr auto; align-items:center; gap:12px;
  background:#08090e;
}
.coin { width:32px; height:32px; border-radius:6px; display:grid; place-items:center; background:#030405; color:var(--text); font-weight:900; font-size:11px; }
.trade-card small { color:var(--muted); display:block; margin-top:4px; }
.empty { color:var(--muted); border:1px dashed var(--line); border-radius:10px; padding:18px; font-size:12px; font-weight:800; }
.terminal {
  background:#020305; border-radius:10px; border:1px solid #10151e; padding:14px;
  min-height:280px; max-height:420px;
  font-family:"Cascadia Mono",Consolas,monospace; color:#b9ffdf; font-size:11px;
  line-height:1.7; overflow-y:auto;
}
.term-prefix { color:var(--cyan); font-weight:900; }
.strategy-card .kv span { color:var(--muted); font-size:11px; }
.control-row { display:flex; gap:10px; margin-bottom:14px; flex-wrap:wrap; }
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

with gr.Blocks(title="Quantum V10 Pro Terminal", css=APP_CSS) as demo:
    with gr.Row(elem_classes=["control-row"]):
        start_btn   = gr.Button("▶ Iniciar Bot",  variant="primary")
        stop_btn    = gr.Button("⏹ Detener")
        refresh_btn = gr.Button("🔄 Actualizar")
        shield_btn  = gr.Button("🔓 Desbloquear Escudo")

    output = gr.HTML(build_dashboard())

    start_btn.click(fn=lambda: (runtime.start(), build_dashboard())[1], outputs=output)
    stop_btn.click(fn=lambda: (runtime.stop(), build_dashboard())[1], outputs=output)
    refresh_btn.click(fn=build_dashboard, outputs=output)
    shield_btn.click(fn=lambda: (runtime.shield.force_clear(), build_dashboard())[1], outputs=output)

    if hasattr(gr, "Timer"):
        gr.Timer(8).tick(fn=build_dashboard, outputs=output)


if __name__ == "__main__":
    demo.launch()
