import re

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

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

new_blocks_line = "cyber_theme = gr.themes.Monochrome(primary_hue=\"cyan\", secondary_hue=\"blue\", neutral_hue=\"slate\")\n\nwith gr.Blocks(title=\"OKX Quantum Elite\", theme=cyber_theme, css=custom_css) as demo:"

content = content.replace('with gr.Blocks(title="OKX Quantum Elite", theme=gr.themes.Base()) as demo:', new_blocks_line)

# Make sure to inject custom_css variable right before new_blocks_line
if "custom_css = '''" not in content:
    content = content.replace("cyber_theme = gr.themes.Monochrome", "custom_css = '''\n" + custom_css.strip() + "\n'''\n\ncyber_theme = gr.themes.Monochrome")

with open("app.py", "w", encoding="utf-8") as f:
    f.write(content)

print("CSS and Theme injected.")
