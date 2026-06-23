import re

def update_scanner():
    with open('scanner.py', 'r', encoding='utf-8') as f:
        content = f.read()
        
    # --- 1. INSERT _dynamically_adopt_trade ---
    dynamic_func = '''
    async def _dynamically_adopt_trade(self, client: OKXClient, db, pos: dict, iid: str, side_raw: str, mgn_mode: str) -> None:
        from models import Trade, TradeSide, Strategy, TradeStatus
        from decimal import Decimal
        from order_execution_engine import OrderExecutionEngine
        import lifecycle

        entry = Decimal(pos.get("avgPx", "0"))
        qty_raw = float(pos.get("pos", "0"))
        qty = abs(qty_raw)
        if entry == 0 or qty == 0:
            return

        current_price = Decimal(pos.get("last") or pos.get("markPx") or str(entry))
        
        if side_raw == "net":
            side = TradeSide.LONG if qty_raw > 0 else TradeSide.SHORT
        else:
            side = TradeSide.LONG if side_raw == "long" else TradeSide.SHORT
            
        side_str = "long" if side == TradeSide.LONG else "short"
        # Calcular ATR estricto como lo usa el bot
        atr_est = entry * Decimal("0.005") / Decimal("2.5")
        
        # Calcular niveles de ciclo de vida
        tp1_price = entry + (lifecycle.ATR_TP1 * atr_est) if side_str == "long" else entry - (lifecycle.ATR_TP1 * atr_est)
        be_price  = entry + (lifecycle.ATR_BREAKEVEN * atr_est) if side_str == "long" else entry - (lifecycle.ATR_BREAKEVEN * atr_est)
        
        crossed_be  = (current_price >= be_price) if side_str == "long" else (current_price <= be_price)
        crossed_tp1 = (current_price >= tp1_price) if side_str == "long" else (current_price <= tp1_price)

        trade_status = TradeStatus.OPEN
        profit_lock_active = 0
        trailing_active = 0
        tp1_done = 0
        
        from risk import compute_sl, breakeven_sl
        
        if crossed_tp1:
            tp1_done = 1
            trailing_active = 1
            trade_status = TradeStatus.TRAILING
            tp_to_set = None
            df_15m = None
            try:
                df_15m = await client.candles(iid, "15m", 50)
                if df_15m is not None and not df_15m.empty:
                    ema21 = lifecycle._ema(df_15m['close'], 21).iloc[-1]
                    sl_to_set = Decimal(str(ema21))
                else:
                    sl_to_set = current_price * Decimal("0.98") if side_str == "long" else current_price * Decimal("1.02")
            except:
                sl_to_set = current_price * Decimal("0.98") if side_str == "long" else current_price * Decimal("1.02")
            state_msg = "[ALTA GANANCIA] - Trailing Stop"
            
        elif crossed_be:
            profit_lock_active = 1
            trade_status = TradeStatus.BREAKEVEN
            sl_to_set = breakeven_sl(entry, side_str, atr=atr_est)
            tp_to_set = tp1_price
            state_msg = "[POCA GANANCIA] - SL en Breakeven"
        else:
            sl_to_set = compute_sl(entry, side_str, atr_est)
            tp_to_set = tp1_price
            state_msg = "[PÉRDIDA/INICIO] - SL original"

        sl_to_set = Decimal(str(sl_to_set))
        
        # Verify it hasn't been added yet concurrently
        already_open = db.query(Trade).filter(
            Trade.symbol == iid,
            Trade.side == side,
            Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])
        ).first()
        
        if already_open:
            return

        trade = Trade(
            symbol=iid, side=side, strategy=Strategy.ST_EMA_REGIME_MTF,
            entry_price=float(entry), position_size=qty, remaining_size=qty,
            sl_price=float(sl_to_set), tp_price=float(tp_to_set) if tp_to_set else None,
            atr=float(atr_est), risk_usd=float(FIXED_RISK_USDT), leverage=int(pos.get("lever", 10)),
            status=trade_status, highest_price=float(max(entry, current_price)), lowest_price=float(min(entry, current_price)),
            profit_lock_active=profit_lock_active, trailing_active=trailing_active,
            tp1_done=tp1_done
        )
        db.add(trade)
        db.commit()
        
        self._log(f"[{iid}] Adoptando dinámicamente: {state_msg}. Enviando protecciones nativas a OKX...")
        
        try:
            await client.cancel_algo_orders(iid, side_str)
            await self._place_algo_order_safe(
                client, iid, side_str, Decimal(str(qty)),
                sl=sl_to_set, tp=tp_to_set, td_mode=mgn_mode
            )
        except Exception as e:
            self._log(f"[{iid}] Error colocando seguros dinámicos en adopción: {e}", "ERROR")
'''
    
    # Inject before _self_heal_auditor
    if "async def _dynamically_adopt_trade" not in content:
        content = content.replace('async def _self_heal_auditor(self, client: OKXClient) -> None:', dynamic_func + '\n\n    async def _self_heal_auditor(self, client: OKXClient) -> None:')

    # --- 2. UPDATE _adopt_live ---
    adopt_live_target = '''
                    entry  = Decimal(pos.get("avgPx", "0"))
                    if entry == 0:
                        continue
                    inst   = self._instruments[iid]
                    ct_val = Decimal(inst["ctVal"])
                    # Assign conservative SL (5% of price)
                    atr_est = entry * Decimal("0.005") / Decimal("2.5")
                    sl = compute_sl(entry, side, atr_est)
                    db.add(Trade(
                        symbol=iid, side=TradeSide(side), strategy=Strategy.ST_EMA_REGIME_MTF,
                        entry_price=float(entry), position_size=float(abs(qty_raw)), remaining_size=float(abs(qty_raw)),
                        sl_price=float(sl), tp_price=None, atr=float(atr_est),
                        risk_usd=float(FIXED_RISK_USDT), leverage=LEVERAGE,
                        status=TradeStatus.OPEN, highest_price=float(entry), lowest_price=float(entry),
                    ))
                    count += 1
'''
    adopt_live_replacement = '''
                    mgn_mode = pos.get("mgnMode", "isolated")
                    await self._dynamically_adopt_trade(client, db, pos, iid, side_raw, mgn_mode)
                    count += 1
'''
    content = content.replace(adopt_live_target, adopt_live_replacement)
    
    # --- 3. UPDATE _self_heal_auditor ---
    auditor_target = '''
                    # 1. Adopt orphans (filtering by symbol AND side to prevent cross-side collision)
                    trade = db.query(Trade).filter(
                        Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT]),
                        Trade.symbol == inst_id,
                        Trade.side == side
                    ).first()
                    if not trade:
                        self._log(f"[{inst_id}] 🤖 AUDITOR: Posición huérfana detectada ({pos_side}). Adoptando en la BD...", "SYSTEM")
                        # Defaults para huérfanas
                        atr_est = entry * 0.015
                        trade = Trade(
                            symbol=inst_id, side=side, strategy=Strategy.ST_EMA_REGIME_MTF,
                            status=TradeStatus.OPEN, entry_price=entry, position_size=abs(qty), remaining_size=abs(qty),
                            sl_price=entry * (0.95 if side == TradeSide.LONG else 1.05),
                            tp_price=None,
                            atr=atr_est, leverage=int(p.get("lever", 10)),
                            highest_price=entry, lowest_price=entry
                        )
                        db.add(trade)
                        db.commit()
'''
    auditor_replacement = '''
                    # 1. Adopt orphans (filtering by symbol AND side to prevent cross-side collision)
                    trade = db.query(Trade).filter(
                        Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT]),
                        Trade.symbol == inst_id,
                        Trade.side == side
                    ).first()
                    if not trade:
                        self._log(f"[{inst_id}] 🤖 AUDITOR: Posición huérfana detectada ({pos_side}). Invocando Adopción Dinámica...", "SYSTEM")
                        await self._dynamically_adopt_trade(client, db, p, inst_id, pos_side_raw, mgn_mode)
'''
    content = content.replace(auditor_target, auditor_replacement)
    
    with open('scanner.py', 'w', encoding='utf-8') as f:
        f.write(content)
        
    print("Scanner updated with dynamic adoption.")

if __name__ == "__main__":
    update_scanner()
