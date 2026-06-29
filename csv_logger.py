import csv
import os
from datetime import datetime

CSV_FILE = "historial_interno.csv"
HEADERS = [
    "FECHA APERTURA", "MONEDA", "ESTRATEGIA", "PRECIO ENTRADA",
    "VOLUMEN ($ USDT)", "TOKENS COMPRADOS", "DISTANCIA SL / TP",
    "BREAKEVEN & TRAILING", "FECHA/HORA CIERRE", "RESULTADO & PNL"
]

def log_trade_to_csv(trade):
    """
    Appends or updates a trade in the CSV file using the Trade model object.
    Since we don't have a sophisticated update mechanism for CSV, we just append 
    when it closes, or we append when it opens and append again when it closes.
    For clean history, it's best to append ONLY when the trade is completely closed.
    But user wants to see it 'cada vez que se habre la operacion'.
    So we append the open status, and then when closed we append the closed status.
    """
    file_exists = os.path.isfile(CSV_FILE)
    
    fecha_aper = trade.created_at.strftime("%Y-%m-%d %H:%M:%S") if trade.created_at else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    fecha_cierre = trade.closed_at.strftime("%Y-%m-%d %H:%M:%S") if trade.closed_at else "EN CURSO"
    
    vol_usd = trade.entry_price * trade.position_size if trade.entry_price and trade.position_size else 0
    
    dist_sl = abs(trade.entry_price - trade.sl_price) / trade.entry_price * 100 if trade.sl_price and trade.entry_price else 0
    dist_tp = abs(trade.entry_price - trade.tp_price) / trade.entry_price * 100 if getattr(trade, 'tp_price', None) and trade.entry_price else 0
    dist_str = f"SL: {dist_sl:.2f}% / TP: {dist_tp:.2f}%"
    
    be_str = "ACTIVO" if trade.profit_lock_active or trade.trailing_active else "INACTIVO"
    
    if trade.position_closed == 1 or trade.status == "CLOSED":
        pnl = getattr(trade, 'realized_pnl', 0) or 0.0
        res = f"{pnl:+.4f} USDT"
    else:
        res = "ABIERTO"
        
    strat_name = trade.strategy.name if hasattr(trade.strategy, 'name') else str(trade.strategy)
    
    row = [
        fecha_aper,
        trade.symbol,
        strat_name,
        f"{trade.entry_price:.6f}",
        f"${vol_usd:.2f}",
        f"{trade.position_size}",
        dist_str,
        be_str,
        fecha_cierre,
        res
    ]
    
    with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="|")
        if not file_exists:
            writer.writerow(HEADERS)
        writer.writerow(row)
        
    # Also log to console in exact requested format
    header_str = " | ".join(HEADERS)
    row_str = " | ".join(str(x) for x in row)
    print(f"\n[HISTORIAL] NUEVA ACTUALIZACION DE OPERACION:\n{header_str}\n{row_str}\n")
