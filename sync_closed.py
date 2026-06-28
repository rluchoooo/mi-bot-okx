with open('scanner.py', 'r', encoding='utf-8') as f:
    content = f.read()

target = '''            with get_session() as db:
                for p in positions:'''

replacement = '''            with get_session() as db:
                # 0. Sync closed trades
                okx_inst_ids = [p.get("instId") for p in positions]
                open_db_trades = db.query(Trade).filter(Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])).all()
                for t in open_db_trades:
                    if t.symbol not in okx_inst_ids:
                        self._log(f"[{t.symbol}] 🤖 AUDITOR: Posición no encontrada en OKX. Marcando como CERRADA.", "SYSTEM")
                        t.status = TradeStatus.CLOSED
                        t.closed_at = datetime.utcnow()
                        
                        # Try to fetch actual PNL from OKX
                        try:
                            # OKX stores closed position history in /api/v5/account/positions-history or we can use the PNL if available.
                            hist = await client._req("GET", f"/api/v5/account/positions-history?instId={t.symbol}", auth=True)
                            if hist and len(hist) > 0:
                                # Get the most recent history record for this symbol
                                recent = hist[0]
                                if recent.get("realizedPnl"):
                                    t.realized_pnl = float(recent["realizedPnl"])
                        except Exception as e:
                            self._log(f"[{t.symbol}] Error fetching closed pnl: {e}", "WARN")

                db.commit()
                
                for p in positions:'''

if target in content:
    content = content.replace(target, replacement)
    with open('scanner.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Patched scanner.py to sync closed trades!')
else:
    print('Target not found in scanner.py')
