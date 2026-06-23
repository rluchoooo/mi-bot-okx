import re

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

new_css = '''
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
'''

content = re.sub(r'APP_CSS = \"\"\"[\s\S]*?\"\"\"', f'APP_CSS = \"\"\"\\n{new_css}\\n\"\"\"', content)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Replaced CSS')
