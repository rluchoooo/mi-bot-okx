import sys
import os
from models import get_session, Trade
from datetime import datetime

def generate_report():
    with get_session() as db:
        trades = db.query(Trade).order_by(Trade.created_at.desc()).all()
    
    table_header = "| FECHA APERTURA | MONEDA | ESTRATEGIA | PRECIO ENTRADA | VOLUMEN ($ USDT) | TOKENS COMPRADOS | DISTANCIA SL / TP | BREAKEVEN & TRAILING | FECHA/HORA CIERRE | RESULTADO & PNL |\n"
    table_header += "|---|---|---|---|---|---|---|---|---|---|\n"
    
    rows = []
    for t in trades:
        fecha_aper = t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "-"
        fecha_cierre = t.closed_at.strftime("%Y-%m-%d %H:%M") if t.closed_at else "EN CURSO"
        
        # Vol USD is approx Entry Price * Size
        vol_usd = t.entry_price * t.position_size
        
        # SL/TP distance calculation
        dist_sl = abs(t.entry_price - t.sl_price) / t.entry_price * 100 if t.sl_price else 0
        dist_tp = abs(t.entry_price - t.tp_price) / t.entry_price * 100 if t.tp_price else 0
        dist_str = f"SL: {dist_sl:.2f}% / TP: {dist_tp:.2f}%"
        
        # BE & Trailing
        be_str = "ACTIVO" if t.profit_lock_active or t.trailing_active else "INACTIVO"
        
        # PNL
        if t.status == "CLOSED":
            pnl = t.realized_pnl if t.realized_pnl is not None else 0.0
            res = f"{pnl:+.4f} USDT"
        else:
            res = "ABIERTO"
            
        row = f"| {fecha_aper} | {t.symbol} | {t.strategy.name if hasattr(t.strategy, 'name') else t.strategy} | {t.entry_price:.6f} | ${vol_usd:.2f} | {t.position_size} | {dist_str} | {be_str} | {fecha_cierre} | {res} |"
        rows.append(row)
        
    markdown_content = f"""# Memoria de Operaciones y Revisión Analítica

Este documento mantiene un registro sincronizado de todas las operaciones ejecutadas y procesadas por el Motor Quantum V10 Pro.
El historial se sincroniza directamente con el exchange OKX.

## Registro Histórico de Trades

{table_header}{chr(10).join(rows)}
"""
    
    with open("analytical_review.md", "w", encoding="utf-8") as f:
        f.write(markdown_content)
        
    print("¡Reporte generado en analytical_review.md!")

if __name__ == "__main__":
    generate_report()
